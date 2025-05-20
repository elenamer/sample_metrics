import itertools
import logging
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union, cast
from urllib.error import HTTPError

import torch
import torch.nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence
from torch.utils.data.dataset import Dataset
from tqdm import tqdm
import numpy as np

import os

import flair.nn
from flair.data import Dictionary, Label, Sentence, Span, get_spans_from_bio, DT
from flair.datasets import DataLoader, FlairDatapointDataset
from flair.embeddings import TokenEmbeddings, TransformerWordEmbeddings
from flair.file_utils import cached_path, unzip_file, Tqdm
from flair.models.sequence_tagger_utils.crf import CRF
from flair.models.sequence_tagger_utils.viterbi import ViterbiDecoder, ViterbiLoss
from flair.training_utils import store_embeddings, Result

log = logging.getLogger("flair")


def calculate_mild_f(prediction_correct_flags):
    # prediction_correct_flags[i] is 1, if the prediction at epoch 1 was correct (0 if it was incorrect); 
    #   example: [01101100]
    # forgetting episode - when prediction_correct_flag goes from 1 to 0
    #   example: forgetting episodes are '11' and '11'
    flags_string = ''.join([str(int(item)) for item in prediction_correct_flags])
    forgetting_list = [item for item in flags_string.split('0') if item] # get only forgetting episodes
    F = len(''.join(forgetting_list)) if len(forgetting_list) != 0 else 0 # total 'duration/length' of forgetting episodes (going from 1 to 0); example: total length is 4
    return F


def calculate_mild_m(prediction_correct_flags):
    # prediction_correct_flags[i] is 1, if the prediction at epoch 1 was correct (0 if it was incorrect); 
    #   example: [01101100]
    # memorization episode - when prediction_correct_flag goes from 1 to 0
    #   example: memorization episodes are '0' and '0'
    flags_string = ''.join([str(int(item)) for item in prediction_correct_flags])
    memorization_list = [item for item in flags_string.split('1') if item] # get only memorization episodes
    M = len(''.join(memorization_list)) if len(memorization_list) != 0 else 0 # total 'duration/length' of memorization episodes (going from 0 to 1); example: total length is 2
    return M


class SequenceTaggerTokenMetrics(flair.models.SequenceTagger):
    def __init__(
            self,
            calculate_sample_metrics: bool = False,
            metrics_mode: str = "epoch_end",
            metrics_save_list: List[str] = [],
            **seqtaggerargs
    ) -> None:
        """Sequence Tagger class for predicting labels for single tokens. Can be parameterized by several attributes.
        Extended from the original SequenceTagger class to include metrics for each token in the batch.

        In case of multitask learning, pass shared embeddings or shared rnn into respective attributes.

        Args:
            embeddings: Embeddings to use during training and prediction
            tag_dictionary: Dictionary containing all tags from corpus which can be predicted
            tag_type: type of tag which is going to be predicted in case a corpus has multiple annotations
            use_rnn: If true, use a RNN, else Linear layer.
            rnn: Takes a torch.nn.Module as parameter by which you can pass a shared RNN between different tasks.
            rnn_type: Specifies the RNN type to use, default is 'LSTM', can choose between 'GRU' and 'RNN' as well.
            hidden_size: Hidden size of RNN layer
            rnn_layers: number of RNN layers
            bidirectional: If True, RNN becomes bidirectional
            use_crf: If True, use a Conditional Random Field for prediction, else linear map to tag space.
            reproject_embeddings: If True, add a linear layer on top of embeddings, if you want to imitate fine tune non-trainable embeddings.
            dropout: If > 0, then use dropout.
            word_dropout: If > 0, then use word dropout.
            locked_dropout: If > 0, then use locked dropout.
            train_initial_hidden_state: if True, trains initial hidden state of RNN
            loss_weights: Dictionary of weights for labels for the loss function. If any label's weight is unspecified it will default to 1.0.
            init_from_state_dict: Indicator whether we are loading a model from state dict since we need to transform previous models' weights into CRF instance weights
            allow_unk_predictions: If True, allows spans to predict <unk> too.
            tag_format: the format to encode spans as tags, either "BIO" or "BIOES"
            c
            alculate_sample_metrics: whether to calculate and log the sample metrics or not,
            metrics_mode: one of two modes, "epoch_end" means all metrics are computed at the end of an epoch, "batch_forward" means metrics are computed during forward pass
            metrics_save_list: which metrics to save to the datapoint. for now all available are saved to the file, but none are saved to the datapoint.
        """
        super().__init__(**seqtaggerargs)

        self.calculate_sample_metrics = calculate_sample_metrics

        # set lists of metrics to log (metrics_list) and metrics info to save to datapoints (metrics_history_variables_list)
        if self.calculate_sample_metrics:
            self.metrics_list = ['confidence',
                                 'variability',
                                 'correctness',
                                 'msp',
                                 'BvSB',
                                 'cross_entropy',
                                 'entropy',
                                 'iter_norm',
                                 'pehist',
                                 'mild_m',
                                 'mild_f',
                                 'mild']
            self.metrics_history_variables_list = ['last_prediction',
                                                   'last_confidence_sum',
                                                   'last_sq_difference_sum',
                                                   'last_correctness_sum',
                                                   'last_iteration',
                                                   'hist_prediction',
                                                   'hist_MILD',
                                                   'total_epochs']

            # maximum value required for pehist TODO: check
            self.max_certainty = -np.log(1.0 / float(self.tagset_size))
            self.mode = metrics_mode
            self.metrics_save_list = metrics_save_list

        self.print_out_path = None

        self.log_metrics_train_eval = True # for now not really used, but would be needed if this is integrated into flair 

        self.to(flair.device)


    # start of sample metrics functions
    def _get_history_metrics_for_batch(self, sentences):
        # get metrics from the previous epoch for each token in the batch 

        history_metrics_dict = {}

        for metric in self.metrics_history_variables_list:
            history_metrics_dict[metric] = []

        for sentence in sentences:
            for metric in self.metrics_history_variables_list:
                metric_list = [token.get_metric(metric) for token in sentence]
                history_metrics_dict[metric].extend(metric_list)

        for metric in self.metrics_history_variables_list:
            if metric == "hist_prediction" or metric == "hist_MILD": # these will be lists of lists
                history_metrics_dict[metric] = history_metrics_dict[metric]

            else:
                history_metrics_dict[metric] = history_metrics_dict[metric] # these will be lists

        return history_metrics_dict

    def _init_metrics_logging(self, epoch_log_path, sentences):
        # set file header; only if the epoch_log_path file hasn't been opened yet
        if not os.path.isfile(self.print_out_path / epoch_log_path):
            with open(self.print_out_path / epoch_log_path, "w") as outfile:
                outfile.write("Text\t" +
                              "sent_index\t" +
                              "token_index\t" +
                              "predicted\t" +
                              "noisy\t" +
                              "clean\t" +
                              "noisy_flag\t")
                for metric in self.metrics_history_variables_list:
                    if metric == "hist_prediction" or metric == "hist_MILD":
                        continue
                    outfile.write(f"{metric}\t")
                for metric in self.metrics_list:
                    outfile.write(f"{metric}\t")
                outfile.write("\n")

        if "hist_prediction" not in sentences[0].tokens[0].metric_history:
            # maybe this should be moved to the constructor.
            # for now, if history is not initialized for the samples in this batch, simply initialize it.
            # initialize metrics history
            for sent in sentences:
                for dp in sent.tokens:
                    # enable choice of metrics to store?
                    dp.set_metric('last_prediction', -1)
                    dp.set_metric('last_confidence_sum', 0)
                    dp.set_metric('last_sq_difference_sum', 0)
                    dp.set_metric('last_correctness_sum', 0)
                    dp.set_metric('last_iteration', 0)
                    dp.set_metric('total_epochs', 0)
                    dp.set_metric("hist_prediction", [0] * self.tagset_size) # distribution of the predictions in all past epochs
                    dp.set_metric("hist_MILD", [0]) # list of 1/0 (according to predictions in each past epoch). 1 - correct prediction, 0 - incorrect

    def _log_metrics(self, epoch_log_path, sentences, metrics_dict, history_metrics_dict, updated_history_metrics_dict,
                     pred, gold_labels, clean_labels):

        i = 0

        with open(self.print_out_path / epoch_log_path, "a") as outfile:
            for sent_ind, sent in enumerate(sentences):
                for token_ind, token in enumerate(sent):

                    # printout token info
                    outfile.write(
                        f"{str(token.text)}\t"
                        + f"{str(sent.ind)}\t"
                        + f"{str(token_ind)}\t"
                        + f"{str(self.label_dictionary.get_item_for_index(pred[i].item()))}\t"
                        + f"{str(self.label_dictionary.get_item_for_index(gold_labels[i].item()))}\t"
                        + f"{str(self.label_dictionary.get_item_for_index(clean_labels[i].item()))}\t"
                        + f"{str(self.label_dictionary.get_item_for_index(gold_labels[i].item()) != self.label_dictionary.get_item_for_index(clean_labels[i].item()))}\t")
                    
                    # printout metric history to a file 
                    for metric in self.metrics_history_variables_list:
                        if metric == 'last_prediction':
                            outfile.write(
                                f"{str(self.label_dictionary.get_item_for_index(history_metrics_dict['last_prediction'][i]))}\t")
                        elif metric == "hist_prediction" or metric == "hist_MILD":
                            # don't print these because they are lists
                            continue
                        else:
                            outfile.write(f"{str(round(history_metrics_dict[metric][i], 4))}\t")

                    # printout actual metrics to a file 
                    for metric in self.metrics_list:
                        outfile.write(f"{str(round(metrics_dict[metric][i], 4))}\t")
                    outfile.write("\n")

                    # save updated metric history to the datapoint
                    token.set_metric('last_prediction', updated_history_metrics_dict['last_prediction'][i])
                    token.set_metric('last_confidence_sum',
                                     updated_history_metrics_dict['last_confidence_sum'][i])
                    token.set_metric('last_sq_difference_sum',
                                     updated_history_metrics_dict['last_sq_difference_sum'][i])
                    token.set_metric('last_correctness_sum',
                                     updated_history_metrics_dict['last_correctness_sum'][i])
                    token.set_metric('last_iteration', updated_history_metrics_dict['last_iteration'][i])
                    token.set_metric("total_epochs", updated_history_metrics_dict["total_epochs"][i])
                    token.set_metric("hist_prediction", updated_history_metrics_dict["hist_prediction"][i])
                    token.set_metric("hist_MILD", updated_history_metrics_dict["hist_MILD"][i])

                    # optional: save selected metrics to the datapoint
                    for metric in self.metrics_save_list:
                        if metric != '':
                            token.set_metric(metric, metrics_dict[metric][i])
                    i += 1
                outfile.write('\n')

    def _calculate_metrics(self, history_metrics_dict, scores, gold_labels):

        # Initialize metrics dictionary and new updated metrics history dictionary
        metrics_dict = {key: [] for key in self.metrics_list}
        updated_history_metrics_dict = {key: [] for key in self.metrics_history_variables_list}

        softmax = F.softmax(scores, dim=-1)
        predicted_labels = torch.argmax(softmax, dim=-1).cpu().detach().numpy()

        for token_index in range(scores.size(0)):
            # Calculate variables needed for all metrics

            softmax_token = softmax[token_index].cpu().detach().numpy()
            gold_label = gold_labels[token_index].cpu().detach().numpy()

            top_2_indices_argmax = np.argsort(softmax_token)[::-1][:2] # argsort returns indices in ascending order
            prediction = top_2_indices_argmax[0]

            total_epochs = history_metrics_dict["total_epochs"][token_index]
            total_epochs = total_epochs + 1

            updated_history_metrics_dict["last_prediction"].append(prediction)
            updated_history_metrics_dict["total_epochs"].append(total_epochs)

            probability_of_predicted_label = softmax_token[top_2_indices_argmax[0]]
            probability_of_second_ranked_prediction = softmax_token[top_2_indices_argmax[1]]
            
            # Metric: Max softmax probability
            probability_of_true_label = softmax_token[gold_label]
            metrics_dict['msp'].append(probability_of_predicted_label)


            # Best vs second best
            BvSB = probability_of_predicted_label - probability_of_second_ranked_prediction
            metrics_dict['BvSB'].append(BvSB)


            # Confidence 
            confidence_sum = history_metrics_dict['last_confidence_sum'][token_index] + probability_of_true_label
            confidence = confidence_sum / total_epochs
            metrics_dict['confidence'].append(confidence)
            updated_history_metrics_dict["last_confidence_sum"].append(confidence_sum)

            # Variability 
            sq_difference_sum = history_metrics_dict['last_sq_difference_sum'][token_index] + np.square(probability_of_true_label - confidence)
            variability = np.sqrt(sq_difference_sum / total_epochs)
            metrics_dict['variability'].append(variability)
            updated_history_metrics_dict["last_sq_difference_sum"].append(sq_difference_sum)

            # Correctness
            correctness_sum = history_metrics_dict['last_correctness_sum'][token_index] + int(gold_label == prediction)
            correctness = correctness_sum / total_epochs
            metrics_dict['correctness'].append(correctness)
            updated_history_metrics_dict["last_correctness_sum"].append(correctness_sum)

            # Iteration Learned
            last_iteration = history_metrics_dict["last_iteration"][token_index]
            prediction_changed = (prediction != history_metrics_dict["last_prediction"][token_index])

            if prediction_changed:
                last_iteration = total_epochs
            
            updated_history_metrics_dict["last_iteration"].append(last_iteration)

            iter_norm = last_iteration / total_epochs
            metrics_dict['iter_norm'].append(iter_norm)

            # Entropy of prediction history
            count_predictions_history = history_metrics_dict["hist_prediction"][token_index]
            count_predictions_history[prediction] += 1
            updated_history_metrics_dict["hist_prediction"].append(count_predictions_history)

            frequencies_prediction_history = [x / total_epochs for x in count_predictions_history]

            log_of_frequencies = np.log(frequencies_prediction_history)
            log_of_frequencies[np.isinf(log_of_frequencies)] = 0

            entropy_prediction_history = frequencies_prediction_history * log_of_frequencies
            
            pe_hist_entropy = -np.sum(entropy_prediction_history)  # sum over all labels.
            pe_hist_entropy = pe_hist_entropy / self.max_certainty
            if pe_hist_entropy == 0:
                pe_hist_entropy = 0.0
            metrics_dict['pehist'].append(pe_hist_entropy)

            # MILD: memorization and forgetting metrics
            mild_history = history_metrics_dict["hist_MILD"][token_index] # list of True/False (whether the predictions in each past epoch are correct)
            prediction_correct = int(prediction == gold_label)
            mild_history_new = mild_history[:]
            mild_history_new.append(prediction_correct)
            updated_history_metrics_dict["hist_MILD"].append(mild_history_new)

            mild_m = calculate_mild_m(mild_history_new)
            mild_f = calculate_mild_f(mild_history_new)
            mild = mild_m - mild_f

            metrics_dict['mild'].append(mild)
            metrics_dict['mild_f'].append(mild_f)
            metrics_dict['mild_m'].append(mild_m)

            # predictive entropy
            entropy = -np.sum(softmax_token * np.nan_to_num(np.log(softmax_token)), axis=-1)
            metrics_dict['entropy'].append(entropy)

            # calculate cross entropy for the given data point
            cross_entropy = - np.nan_to_num(np.log(softmax_token[gold_label]))
            if cross_entropy == 0:
                cross_entropy = 0.0
            metrics_dict['cross_entropy'].append(cross_entropy)
            
        return predicted_labels, metrics_dict, updated_history_metrics_dict

    def calculate_and_log_metrics(self, sentences, scores, observed_labels, clean_labels):

        epoch_log_path = Path("epoch_log_" + str(self.model_card["training_parameters"]["epoch"]) + ".log")

        self._init_metrics_logging(epoch_log_path, sentences)
        history_metrics_dict = self._get_history_metrics_for_batch(sentences)

        pred, metrics_dict, updated_history_metrics_dict = self._calculate_metrics(history_metrics_dict, scores,
                                                                                   observed_labels)

        self._log_metrics(epoch_log_path, sentences, metrics_dict, history_metrics_dict, updated_history_metrics_dict,
                          pred, observed_labels, clean_labels)

    def forward_loss(self, sentences: List[Sentence]) -> Tuple[torch.Tensor, int]:
        # if there are no sentences, there is no loss
        if len(sentences) == 0:
            return torch.tensor(0.0, dtype=torch.float, device=flair.device, requires_grad=True), 0
        sentences = sorted(sentences, key=len, reverse=True)

        sentence_tensor, lengths = self._prepare_tensors(sentences)

        # forward pass to get scores
        scores = self.forward(sentence_tensor, lengths)

        # BIOES
        gold_labels = self._prepare_label_tensor(sentences)

        if self.calculate_sample_metrics and self.mode == 'batch_forward':
            # BIOES
            clean_labels = self._prepare_label_tensor(sentences, label_type=self.label_type + '_clean')

            self.calculate_and_log_metrics(sentences, scores, gold_labels, clean_labels)

        # calculate loss given scores and labels
        return self._calculate_loss(scores, gold_labels)

    def _prepare_tensors(self, data_points: Union[List[Sentence], Sentence]) -> Tuple[torch.Tensor, torch.LongTensor]:
        sentences = [data_points] if not isinstance(data_points, list) else data_points
        self.embeddings.embed(sentences)

        # make a zero-padded tensor for the whole sentence
        lengths, sentence_tensor = self._make_padded_tensor_for_batch(sentences)

        return sentence_tensor, lengths


    def _get_gold_labels(self, sentences: List[Sentence], label_type=None) -> List[str]:
        # is esentially noisy (observed) label
        """Extracts gold labels from each sentence.

        Args:
            sentences: List of sentences in batch
        """
        if label_type is None:
            label_type = self.label_type

        # spans need to be encoded as token-level predictions
        if self.predict_spans:
            all_sentence_labels = []
            for sentence in sentences:
                sentence_labels = ["O"] * len(sentence)
                for label in sentence.get_labels(label_type):
                    span: Span = label.data_point
                    if self.tag_format == "BIOES":
                        if len(span) == 1:
                            sentence_labels[span[0].idx - 1] = "S-" + label.value
                        else:
                            sentence_labels[span[0].idx - 1] = "B-" + label.value
                            sentence_labels[span[-1].idx - 1] = "E-" + label.value
                            for i in range(span[0].idx, span[-1].idx - 1):
                                sentence_labels[i] = "I-" + label.value
                    else:
                        sentence_labels[span[0].idx - 1] = "B-" + label.value
                        for i in range(span[0].idx, span[-1].idx):
                            sentence_labels[i] = "I-" + label.value
                all_sentence_labels.extend(sentence_labels)
            labels = all_sentence_labels

        # all others are regular labels for each token
        else:
            labels = [token.get_label(self.label_type, "O").value for sentence in sentences for token in sentence]

        return labels

    def _prepare_label_tensor(self, sentences: List[Sentence], label_type=None):
        gold_labels = self._get_gold_labels(sentences, label_type=label_type)
        labels = torch.tensor(
            [self.label_dictionary.get_idx_for_item(label) for label in gold_labels],
            dtype=torch.long,
            device=flair.device,
        )
        return labels

    def predict(
            self,
            sentences: Union[List[Sentence], Sentence],
            mini_batch_size: int = 32,
            return_probabilities_for_all_classes: bool = False,
            verbose: bool = False,
            label_name: Optional[str] = None,
            return_loss=False,
            embedding_storage_mode="none",
            force_token_predictions: bool = False,
    ):
        """Predicts labels for current batch with CRF or Softmax.

        Args:
            sentences: List of sentences in batch
            mini_batch_size: batch size for test data
            return_probabilities_for_all_classes: Whether to return probabilities for all classes
            verbose: whether to use progress bar
            label_name: which label to predict
            return_loss: whether to return loss value
            embedding_storage_mode: determines where to store embeddings - can be "gpu", "cpu" or None.
            force_token_predictions: add labels per token instead of span labels, even if `self.predict_spans` is True
        """
        if label_name is None:
            label_name = self.tag_type

        with torch.no_grad():
            if not sentences:
                return sentences

            # make sure it's a list
            if not isinstance(sentences, list) and not isinstance(sentences, flair.data.Dataset):
                sentences = [sentences]

            Sentence.set_context_for_sentences(cast(List[Sentence], sentences))

            # filter empty sentences
            sentences = [sentence for sentence in sentences if len(sentence) > 0]

            # reverse sort all sequences by their length
            reordered_sentences = sorted(sentences, key=len, reverse=True)

            if len(reordered_sentences) == 0:
                return sentences

            dataloader = DataLoader(
                dataset=FlairDatapointDataset(reordered_sentences),
                batch_size=mini_batch_size,
            )
            # progress bar for verbosity
            if verbose:
                dataloader = tqdm(dataloader, desc="Batch inference")

            overall_loss = torch.zeros(1, device=flair.device)
            label_count = 0
            for batch in dataloader:
                # stop if all sentences are empty
                if not batch:
                    continue

                # get features from forward propagation
                sentence_tensor, lengths = self._prepare_tensors(batch)
                features = self.forward(sentence_tensor, lengths)

                # remove previously predicted labels of this type
                for sentence in batch:
                    sentence.remove_labels(label_name)

                # if return_loss, get loss value
                if return_loss:
                    gold_labels = self._prepare_label_tensor(batch)
                    loss = self._calculate_loss(features, gold_labels)
                    overall_loss += loss[0]
                    label_count += loss[1]

                if self.calculate_sample_metrics and self.mode == 'epoch_end' and self.log_metrics_train_eval:
                    # BIOES
                    gold_labels = self._prepare_label_tensor(batch)

                    clean_labels = self._prepare_label_tensor(batch, label_type=self.label_type + "_clean")

                    self.calculate_and_log_metrics(batch, features, gold_labels, clean_labels)

                # make predictions
                if self.use_crf:
                    predictions, all_tags = self.viterbi_decoder.decode(
                        features, return_probabilities_for_all_classes, batch
                    )
                else:
                    predictions, all_tags = self._standard_inference(
                        features, batch, return_probabilities_for_all_classes
                    )

                # add predictions to Sentence
                for sentence, sentence_predictions in zip(batch, predictions):
                    # BIOES-labels need to be converted to spans
                    if self.predict_spans and not force_token_predictions:
                        sentence_tags = [label[0] for label in sentence_predictions]
                        sentence_scores = [label[1] for label in sentence_predictions]
                        predicted_spans = get_spans_from_bio(sentence_tags, sentence_scores)
                        for predicted_span in predicted_spans:
                            span: Span = sentence[predicted_span[0][0]: predicted_span[0][-1] + 1]
                            span.add_label(label_name, value=predicted_span[2], score=predicted_span[1])

                    # token-labels can be added directly ("O" and legacy "_" predictions are skipped)
                    else:
                        for token, label in zip(sentence.tokens, sentence_predictions):
                            if label[0] in ["O", "_"]:
                                continue
                            token.add_label(typename=label_name, value=label[0], score=label[1])

                # all_tags will be empty if all_tag_prob is set to False, so the for loop will be avoided
                for sentence, sent_all_tags in zip(batch, all_tags):
                    for token, token_all_tags in zip(sentence.tokens, sent_all_tags):
                        token.add_tags_proba_dist(label_name, token_all_tags)

                store_embeddings(sentences, storage_mode=embedding_storage_mode)

            if return_loss:
                return overall_loss, label_count
            return None

    def _print_predictions(self, batch, gold_label_type):
        lines = []
        if self.predict_spans:
            for datapoint in batch:
                # all labels default to "O"
                for token in datapoint:
                    token.set_label("gold_bio", "O")
                    token.set_label("predicted_bio", "O")

                # set gold token-level
                for gold_label in datapoint.get_labels(gold_label_type):
                    gold_span: Span = gold_label.data_point
                    prefix = "B-"
                    for token in gold_span:
                        token.set_label("gold_bio", prefix + gold_label.value)
                        prefix = "I-"

                # set predicted token-level
                for predicted_label in datapoint.get_labels("predicted"):
                    predicted_span: Span = predicted_label.data_point
                    prefix = "B-"
                    for token in predicted_span:
                        token.set_label("predicted_bio", prefix + predicted_label.value)
                        prefix = "I-"

                # now print labels in CoNLL format
                for token in datapoint:
                    eval_line = (
                        f"{token.text} "
                        f"{token.get_label('gold_bio').value} "
                        f"{token.get_label('predicted_bio').value}\n"
                    )
                    lines.append(eval_line)
                lines.append("\n")

        else:
            for datapoint in batch:
                # print labels in CoNLL format
                for token in datapoint:
                    eval_line = (
                        f"{token.text} "
                        f"{token.get_label(gold_label_type).value} "
                        f"{token.get_label('predicted').value}\n"
                    )
                    lines.append(eval_line)
                lines.append("\n")
        return lines

class EarlyExitSequenceTagger(SequenceTaggerTokenMetrics):
    def __init__(
            self,
            embeddings: TransformerWordEmbeddings,  # layer_mean = False, layers = "all"
            tag_dictionary: Dictionary,
            tag_type: str,
            use_rnn=False,
            use_crf=False,
            reproject_embeddings=False,
            weighted_loss: bool = True,
            last_layer_only: bool = False,
            print_all_predictions=True,
            calculate_sample_metrics=False,
            **seqtaggerargs
    ):
        """
        Adds Early-Exit functionality to the SequenceTagger
        :param weighted_loss: controls whether to compute a weighted or a simple average loss
        over all the early-exit layers.
        :param last_layer_only: allows to use outputs of the last layer only to train the
        model (like in the case of the regular SequenceTagger).
        """
        super().__init__(
            embeddings=embeddings,
            tag_dictionary=tag_dictionary,
            tag_type=tag_type,
            use_rnn=use_rnn,
            use_crf=use_crf,
            reproject_embeddings=reproject_embeddings,
            calculate_sample_metrics=calculate_sample_metrics,
            **seqtaggerargs
        )

        if embeddings.layer_mean:
            raise AssertionError("layer_mean must be disabled for the transformer embeddings")
        self.n_layers = len(
            embeddings.layer_indexes)  # the output of the emb layer before the transformer blocks counts as well
        self.final_embedding_size = int(embeddings.embedding_length / self.n_layers)
        self.linear = torch.nn.ModuleList(
            torch.nn.Linear(self.final_embedding_size, len(self.label_dictionary))
            for _ in range(self.n_layers)
        )
        self.weighted_loss = weighted_loss
        self.last_layer_only = last_layer_only
        self.print_all_predictions = print_all_predictions

        # add layer metrics to the list of metrics to log  
        if self.calculate_sample_metrics:
            self.metrics_list.append('pd')
            self.metrics_list.append('fl')
            self.metrics_list.append('tac')
            self.metrics_list.append('tal')
            self.metrics_list.append('le')

        self.to(flair.device)

    def _make_padded_tensor_for_batch(self, sentences: List[Sentence]) -> Tuple[torch.LongTensor, torch.Tensor]:
        names = self.embeddings.get_names()
        lengths: List[int] = [len(sentence.tokens) for sentence in sentences]
        longest_token_sequence_in_batch: int = max(lengths)
        pre_allocated_zero_tensor = torch.zeros(
            self.embeddings.embedding_length * longest_token_sequence_in_batch,
            dtype=torch.float,
            device=flair.device,
        )
        all_embs = list()
        for sentence in sentences:
            all_embs += [emb for token in sentence for emb in token.get_each_embedding(names)]
            nb_padding_tokens = longest_token_sequence_in_batch - len(sentence)

            if nb_padding_tokens > 0:
                t = pre_allocated_zero_tensor[: self.embeddings.embedding_length * nb_padding_tokens]
                all_embs.append(t)

        sentence_tensor = torch.cat(all_embs).view(
            [
                len(sentences),
                longest_token_sequence_in_batch,
                self.n_layers,
                self.final_embedding_size,
            ]
        )
        return torch.LongTensor(lengths), sentence_tensor

    def forward(self, sentence_tensor: torch.Tensor, lengths: torch.LongTensor):  # type: ignore[override]
        """
        Forward propagation through network.
        :param sentence_tensor: A tensor representing the batch of sentences.
        :param lengths: A IntTensor representing the lengths of the respective sentences.
        """
        scores = []
        for i in range(self.n_layers):
            sentence_layer_tensor = sentence_tensor[:, :, i, :]
            if self.use_dropout:
                sentence_layer_tensor = self.dropout(sentence_layer_tensor)
            if self.use_word_dropout:
                sentence_layer_tensor = self.word_dropout(sentence_layer_tensor)
            if self.use_locked_dropout:
                sentence_layer_tensor = self.locked_dropout(sentence_layer_tensor)

            # linear map to tag space
            features = self.linear[i](sentence_layer_tensor)

            # -- A tensor of shape (aggregated sequence length for all sentences in batch, tagset size) for linear layer
            layer_scores = self._get_scores_from_features(features, lengths)
            scores.append(layer_scores)

        return torch.stack(scores)

    def _calculate_metrics(self, history_metrics_dict, scores, gold_labels):
        # scores: (num_layers, num_tokens, num_classes)
        pred, metrics_dict, updated_history_metrics_dict = super()._calculate_metrics(
            history_metrics_dict, scores[-1], gold_labels
        )

        # softmax over the scores from all layers
        softmax = F.softmax(scores, dim=-1).cpu()
        pd = []
        fl = []
        total_last = []
        total_correct = []
        layer_entropy = []

        # iterate over tokens and calculate layer metrics
        for i in range(softmax.size()[1]):
            layer_metrics = self._calculate_layer_metrics(softmax[:, i, :].cpu(), gold_labels[i].item())
            pd.append(layer_metrics['prediction_depth'])
            fl.append(layer_metrics['first_layer'])
            total_last.append(layer_metrics['total_agree_w_last'])
            total_correct.append(layer_metrics['total_agree_w_correct'])
            layer_entropy.append(layer_metrics['layer_entropy'])


        metrics_dict["pd"] = pd
        metrics_dict["fl"] = fl
        metrics_dict["tac"] = total_correct
        metrics_dict["tal"] = total_last
        metrics_dict["le"] = layer_entropy
        
        return pred, metrics_dict, updated_history_metrics_dict

    def forward_loss(self, sentences: List[Sentence]) -> Tuple[torch.Tensor, int]:
        # if there are no sentences, there is no loss
        if len(sentences) == 0:
            return torch.tensor(0.0, dtype=torch.float, device=flair.device, requires_grad=True), 0

        sentences = sorted(sentences, key=len, reverse=True)

        sentence_tensor, lengths = self._prepare_tensors(sentences)

        # forward pass to get scores
        scores = self.forward(sentence_tensor, lengths)

        gold_labels = self._prepare_label_tensor(sentences)

        if self.calculate_sample_metrics and self.mode == "batch_forward":
            clean_labels = self._prepare_label_tensor(sentences, label_type=self.label_type + '_clean')
            self.calculate_and_log_metrics(sentences, scores, gold_labels, clean_labels)

            # calculate loss given scores and labels
        return self._calculate_loss(scores, gold_labels)

    def _calculate_loss(self, scores: torch.Tensor, labels: torch.LongTensor) -> Tuple[torch.Tensor, int]:

        if labels.size(0) == 0:
            return torch.tensor(0.0, requires_grad=True, device=flair.device), 1

        if self.last_layer_only:
            loss = self.loss_function(scores[-1], labels)
        else:
            if self.weighted_loss:
                layer_weights = torch.arange(self.n_layers, device=flair.device)

                # 0.01 and 1 weights
                # layer_weights = [0.01 for i in range(self.n_layers)]
                # layer_weights[-1] = 1
                # layer_weights = torch.tensor(layer_weights, dtype=torch.float, device=flair.device, requires_grad=False)

                layer_weighted_loss = 0
                for i in range(self.n_layers):
                    layer_loss = self.loss_function(scores[i], labels)
                    layer_weighted_loss += layer_weights[i] * layer_loss
                loss = layer_weighted_loss / sum(layer_weights)  # sample-sum layer-weighted average loss
            else:
                loss = 0
                for i in range(1, self.n_layers):
                    loss += self.loss_function(scores[i], labels)
                loss = loss / (self.n_layers - 1)  # sample-sum layer average loss
        return loss, len(labels)

    def _calculate_layer_metrics(self, scores: torch.Tensor, gold_label: int) -> int:
        """
        Calculates the layer metrics for a given (single) data point.
        :param scores: tensor with softmax or sigmoid scores of all layers
        """

        # Initialize variables
        pd = self.n_layers
        final_pd = False

        fl = self.n_layers

        total_agree_w_last = 0
        total_agree_w_correct = 0

        # Calculate the predictions from each layer
        pred_labels = torch.argmax(scores, dim=-1)

        # Calculate layer entropy 
        frequencies = torch.bincount(pred_labels, minlength=len(self.label_dictionary))
        frequencies = frequencies / frequencies.sum()  # normalize frequencies

        layer_entropy = -torch.sum(torch.mul(frequencies, torch.nan_to_num(torch.log(frequencies)))) 
        layer_entropy = layer_entropy.item()

        if layer_entropy == 0:
            layer_entropy = 0.0
        
        for i in range(self.n_layers - 1, -1, -1):  
            # iterate over the layers starting from the penultimate one
            if pred_labels[i] == gold_label:
                # fl (first layer): will have the ID of the lowest layer predicting the training label
                fl = i  
                # total_agree_w_correct: count how many layers aggre with the training label
                total_agree_w_correct += 1 

            if pred_labels[i] == pred_labels[-1]: 
                if not final_pd: 
                    # if prediction is the same as the last layer, decrease pd (prediction depth)
                    pd -= 1
                # total_agree_w_last: count how many layers aggre with the last layer's prediction
                total_agree_w_last += 1  
            else:  
                # if the prediction is not the same as the last layer, then the pd sequence is broken and final pd value is set
                final_pd = True

        return {'prediction_depth': pd, 'first_layer': fl, 'layer_entropy': layer_entropy,
                'total_agree_w_last': total_agree_w_last, 'total_agree_w_correct': total_agree_w_correct}

    def _standard_inference(self, features: torch.Tensor, batch: List[Sentence], probabilities_for_all_classes: bool):
        """
        Softmax over emission scores from forward propagation.
        :param features: sentence tensor from forward propagation
        :param batch: list of sentence
        :param probabilities_for_all_classes: whether to return score for each tag in tag dictionary
        """
        softmax_batch = F.softmax(features, dim=2).cpu()
        full_scores_batch, full_prediction_batch = torch.max(softmax_batch, dim=2)
        predictions = []
        all_tags = []

        for i in range(self.n_layers):
            layer_predictions = []
            scores_batch, prediction_batch = full_scores_batch[i], full_prediction_batch[i]
            for sentence in batch:
                scores = scores_batch[: len(sentence)]
                predictions_for_sentence = prediction_batch[: len(sentence)]
                layer_predictions.append(
                    [
                        (self.label_dictionary.get_item_for_index(prediction), score.item())
                        for token, score, prediction in zip(sentence, scores, predictions_for_sentence)
                    ]
                )
                scores_batch = scores_batch[len(sentence):]
                prediction_batch = prediction_batch[len(sentence):]
            predictions.append(layer_predictions)

        if probabilities_for_all_classes:
            for i in range(self.n_layers):
                lengths = [len(sentence) for sentence in batch]
                layer_tags = self._all_scores_for_token(batch, softmax_batch[i], lengths)
                all_tags.append(layer_tags)

        return predictions, all_tags

    def predict(
            self,
            sentences: Union[List[Sentence], Sentence],
            mini_batch_size: int = 32,
            return_probabilities_for_all_classes: bool = False,
            verbose: bool = False,
            label_name: Optional[str] = None,
            return_loss=False,
            embedding_storage_mode="none",
            force_token_predictions: bool = False,
            layer_idx: int = -1,
    ):  # type: ignore
        """
        Predicts labels for current batch with Softmax.
        :param sentences: List of sentences in batch
        :param mini_batch_size: batch size for test data
        :param return_probabilities_for_all_classes: Whether to return probabilites for all classes
        :param verbose: whether to use progress bar
        :param label_name: which label to predict
        :param return_loss: whether to return loss value
        :param embedding_storage_mode: determines where to store embeddings - can be "gpu", "cpu" or None.
        :param layer_idx: determines which layer is used to write the predictions to spans or tokens.
        """
        if abs(layer_idx) > self.n_layers:
            raise ValueError('Layer index out of range')

        if label_name is None:
            label_name = self.tag_type

        with torch.no_grad():
            if not sentences:
                return sentences

            # make sure its a list
            if not isinstance(sentences, list) and not isinstance(sentences, flair.data.Dataset):
                sentences = [sentences]

            # filter empty sentences
            sentences = [sentence for sentence in sentences if len(sentence) > 0]

            # reverse sort all sequences by their length
            reordered_sentences = sorted(sentences, key=len, reverse=True)

            if len(reordered_sentences) == 0:
                return sentences

            dataloader = DataLoader(
                dataset=FlairDatapointDataset(reordered_sentences),
                batch_size=mini_batch_size,
            )
            # progress bar for verbosity
            if verbose:
                dataloader = tqdm(dataloader, desc="Batch inference")

            overall_loss = torch.zeros(1, device=flair.device)
            label_count = 0
            for batch in dataloader:

                # stop if all sentences are empty
                if not batch:
                    continue

                # get features from forward propagation
                sentence_tensor, lengths = self._prepare_tensors(batch)
                features = self.forward(sentence_tensor, lengths)

                # remove previously predicted labels of this type
                for sentence in batch:
                    sentence.remove_labels(label_name)

                # if return_loss, get loss value
                if return_loss:
                    gold_labels = self._prepare_label_tensor(batch)
                    loss = self._calculate_loss(features, gold_labels)
                    overall_loss += loss[0]
                    label_count += loss[1]

                if self.calculate_sample_metrics and self.mode == 'epoch_end' and self.log_metrics_train_eval:  # log_metrics_train_eval is only set when running evaluate() from the trainer, with monitor_train_sample set
                    # BIOES
                    gold_labels = self._prepare_label_tensor(batch)
                    clean_labels = self._prepare_label_tensor(batch, label_type=self.label_type + "_clean")

                    self.calculate_and_log_metrics(batch, features, gold_labels, clean_labels)

                # make predictions
                predictions, all_tags = self._standard_inference(
                    features, batch, return_probabilities_for_all_classes
                )

                # add predictions to Sentence
                for sentence, sentence_predictions in zip(batch, predictions[layer_idx]):

                    # BIOES-labels need to be converted to spans
                    if self.predict_spans and not force_token_predictions:
                        sentence_tags = [label[0] for label in sentence_predictions]
                        sentence_scores = [label[1] for label in sentence_predictions]
                        predicted_spans = get_spans_from_bio(sentence_tags, sentence_scores)
                        for predicted_span in predicted_spans:
                            span: Span = sentence[predicted_span[0][0]: predicted_span[0][-1] + 1]
                            span.add_label(label_name, value=predicted_span[2], score=predicted_span[1])

                    # token-labels can be added directly ("O" and legacy "_" predictions are skipped)
                    else:
                        for token, label in zip(sentence.tokens, sentence_predictions):
                            if label[0] in ["O", "_"]:
                                continue
                            token.add_label(typename=label_name, value=label[0], score=label[1])

                # all_tags will be empty if all_tag_prob is set to False, so the for loop will be avoided
                if len(all_tags) > 0:
                    for (sentence, sent_all_tags) in zip(batch, all_tags[layer_idx]):
                        for (token, token_all_tags) in zip(sentence.tokens, sent_all_tags):
                            token.add_tags_proba_dist(label_name, token_all_tags)

                store_embeddings(sentences, storage_mode=embedding_storage_mode)

            if return_loss:
                return overall_loss, label_count

    def evaluate(
            self,
            data_points: Union[List[DT], Dataset],
            gold_label_type: str,
            out_path: Union[str, Path] = None,
            embedding_storage_mode: str = "none",
            mini_batch_size: int = 32,
            num_workers: Optional[int] = 8,
            main_evaluation_metric: Tuple[str, str] = ("micro avg", "f1-score"),
            exclude_labels: List[str] = [],
            gold_label_dictionary: Optional[Dictionary] = None,
            return_loss: bool = True,
            layer_idx=-1,
            **kwargs,
    ) -> Result:
        import numpy as np
        import sklearn

        """
        This override contains solely the following chagne:
        :param layer_idx: determines which layer is used to write the predictions to spans or tokens.
        This parameters is passed onto the :predict: method to allow for the evaluation of each early-exit
        layer individually.
        """

        if "final_train_eval" in kwargs:
            self.log_metrics_train_eval = True
        else:
            self.log_metrics_train_eval = False

        # make sure <unk> is contained in gold_label_dictionary, if given
        if gold_label_dictionary and not gold_label_dictionary.add_unk:
            raise AssertionError("gold_label_dictionary must have add_unk set to true in initialization.")

        # read Dataset into data loader, if list of sentences passed, make Dataset first
        if not isinstance(data_points, Dataset):
            data_points = FlairDatapointDataset(data_points)

        with torch.no_grad():

            # loss calculation
            eval_loss = torch.zeros(1, device=flair.device)
            average_over = 0

            # variables for printing
            lines: List[str] = []

            # variables for computing scores
            all_spans: Set[str] = set()
            all_true_values = {}
            all_predicted_values = {}

            loader = DataLoader(data_points, batch_size=mini_batch_size)

            sentence_id = 0
            for batch in Tqdm.tqdm(loader):

                # remove any previously predicted labels
                for datapoint in batch:
                    datapoint.remove_labels("predicted")

                # predict for batch
                loss_and_count = self.predict(
                    batch,
                    embedding_storage_mode=embedding_storage_mode,
                    mini_batch_size=mini_batch_size,
                    label_name="predicted",
                    return_loss=return_loss,
                    layer_idx=layer_idx,
                )

                if return_loss:
                    if isinstance(loss_and_count, tuple):
                        average_over += loss_and_count[1]
                        eval_loss += loss_and_count[0]
                    else:
                        eval_loss += loss_and_count

                # get the gold labels
                for datapoint in batch:

                    for gold_label in datapoint.get_labels(gold_label_type):
                        representation = str(sentence_id) + ": " + gold_label.unlabeled_identifier

                        value = gold_label.value
                        if gold_label_dictionary and gold_label_dictionary.get_idx_for_item(value) == 0:
                            value = "<unk>"

                        if representation not in all_true_values:
                            all_true_values[representation] = [value]
                        else:
                            all_true_values[representation].append(value)

                        if representation not in all_spans:
                            all_spans.add(representation)

                    for predicted_span in datapoint.get_labels("predicted"):
                        representation = str(sentence_id) + ": " + predicted_span.unlabeled_identifier

                        # add to all_predicted_values
                        if representation not in all_predicted_values:
                            all_predicted_values[representation] = [predicted_span.value]
                        else:
                            all_predicted_values[representation].append(predicted_span.value)

                        if representation not in all_spans:
                            all_spans.add(representation)

                    sentence_id += 1

                store_embeddings(batch, embedding_storage_mode)

                # make printout lines
                if out_path and layer_idx == -1 and self.print_all_predictions:
                    lines.extend(self._print_predictions(batch, gold_label_type))

            self.log_metrics_train_eval = False

            # convert true and predicted values to two span-aligned lists
            true_values_span_aligned = []
            predicted_values_span_aligned = []
            for span in all_spans:
                list_of_gold_values_for_span = all_true_values[span] if span in all_true_values else ["O"]
                # delete exluded labels if exclude_labels is given
                for excluded_label in exclude_labels:
                    if excluded_label in list_of_gold_values_for_span:
                        list_of_gold_values_for_span.remove(excluded_label)
                # if after excluding labels, no label is left, ignore the datapoint
                if not list_of_gold_values_for_span:
                    continue
                true_values_span_aligned.append(list_of_gold_values_for_span)
                predicted_values_span_aligned.append(
                    all_predicted_values[span] if span in all_predicted_values else ["O"]
                )

            # write all_predicted_values to out_file if set (per-epoch)
            if out_path and layer_idx == -1 and self.print_all_predictions:
                epoch_log_path = Path(str(out_path)[:-4] + '_' + str(
                    self.model_card["training_parameters"]["epoch"]) + '.tsv')
                with open(Path(epoch_log_path), "w", encoding="utf-8") as outfile:
                    outfile.write("".join(lines))

            # make the evaluation dictionary
            evaluation_label_dictionary = Dictionary(add_unk=False)
            evaluation_label_dictionary.add_item("O")
            for true_values in all_true_values.values():
                for label in true_values:
                    evaluation_label_dictionary.add_item(label)
            for predicted_values in all_predicted_values.values():
                for label in predicted_values:
                    evaluation_label_dictionary.add_item(label)

        # check if this is a multi-label problem
        multi_label = False
        for true_instance, predicted_instance in zip(true_values_span_aligned, predicted_values_span_aligned):
            if len(true_instance) > 1 or len(predicted_instance) > 1:
                multi_label = True
                break

        log.info(f"Evaluating as a multi-label problem: {multi_label}")

        # compute numbers by formatting true and predicted such that Scikit-Learn can use them
        y_true = []
        y_pred = []
        if multi_label:
            # multi-label problems require a multi-hot vector for each true and predicted label
            for true_instance in true_values_span_aligned:
                y_true_instance = np.zeros(len(evaluation_label_dictionary), dtype=int)
                for true_value in true_instance:
                    y_true_instance[evaluation_label_dictionary.get_idx_for_item(true_value)] = 1
                y_true.append(y_true_instance.tolist())

            for predicted_values in predicted_values_span_aligned:
                y_pred_instance = np.zeros(len(evaluation_label_dictionary), dtype=int)
                for predicted_value in predicted_values:
                    y_pred_instance[evaluation_label_dictionary.get_idx_for_item(predicted_value)] = 1
                y_pred.append(y_pred_instance.tolist())
        else:
            # single-label problems can do with a single index for each true and predicted label
            y_true = [
                evaluation_label_dictionary.get_idx_for_item(true_instance[0])
                for true_instance in true_values_span_aligned
            ]
            y_pred = [
                evaluation_label_dictionary.get_idx_for_item(predicted_instance[0])
                for predicted_instance in predicted_values_span_aligned
            ]

        # now, calculate evaluation numbers
        target_names = []
        labels = []

        counter = Counter(itertools.chain.from_iterable(all_true_values.values()))
        counter.update(list(itertools.chain.from_iterable(all_predicted_values.values())))

        for label_name, count in counter.most_common():
            if label_name == "O":
                continue
            target_names.append(label_name)
            labels.append(evaluation_label_dictionary.get_idx_for_item(label_name))

        # there is at least one gold label or one prediction (default)
        if len(all_true_values) + len(all_predicted_values) > 1:
            classification_report = sklearn.metrics.classification_report(
                y_true,
                y_pred,
                digits=4,
                target_names=target_names,
                zero_division=0,
                labels=labels,
            )

            classification_report_dict = sklearn.metrics.classification_report(
                y_true,
                y_pred,
                target_names=target_names,
                zero_division=0,
                output_dict=True,
                labels=labels,
            )

            accuracy_score = round(sklearn.metrics.accuracy_score(y_true, y_pred), 4)
            macro_f_score = round(classification_report_dict["macro avg"]["f1-score"], 4)

            # if there is only one label, then "micro avg" = "macro avg"
            if len(target_names) == 1:
                classification_report_dict["micro avg"] = classification_report_dict["macro avg"]

            if "micro avg" in classification_report_dict:
                # micro average is only computed if zero-label exists (for instance "O")
                precision_score = round(classification_report_dict["micro avg"]["precision"], 4)
                recall_score = round(classification_report_dict["micro avg"]["recall"], 4)
                micro_f_score = round(classification_report_dict["micro avg"]["f1-score"], 4)
            else:
                # if no zero-label exists (such as in POS tagging) micro average is equal to accuracy
                precision_score = round(classification_report_dict["accuracy"], 4)
                recall_score = round(classification_report_dict["accuracy"], 4)
                micro_f_score = round(classification_report_dict["accuracy"], 4)

            # same for the main score
            if "micro avg" not in classification_report_dict and main_evaluation_metric[0] == "micro avg":
                main_score = classification_report_dict["accuracy"]
            else:
                main_score = classification_report_dict[main_evaluation_metric[0]][main_evaluation_metric[1]]

        else:
            # issue error and default all evaluation numbers to 0.
            log.error(
                "ACHTUNG! No gold labels and no all_predicted_values found! "
                "Could be an error in your corpus or how you "
                "initialize the trainer!"
            )
            accuracy_score = precision_score = recall_score = micro_f_score = macro_f_score = main_score = 0.0
            classification_report = ""
            classification_report_dict = {}

        detailed_result = (
                "\nResults:"
                f"\n- F-score (micro) {micro_f_score}"
                f"\n- F-score (macro) {macro_f_score}"
                f"\n- Accuracy {accuracy_score}"
                "\n\nBy class:\n" + classification_report
        )

        if average_over > 0:
            eval_loss /= average_over

        result = Result(
            main_score=main_score,
            detailed_results=detailed_result,
            classification_report=classification_report_dict,
            scores={'loss': eval_loss.item()},
        )

        return result

    def _print_predictions(self, batch, gold_label_type):
        # this override also prints out PD for each token
        lines = []
        if self.predict_spans:
            for datapoint in batch:
                # all labels default to "O"
                for token in datapoint:
                    token.set_label("gold_bio", "O")
                    token.set_label("clean_bio", "O")
                    token.set_label("predicted_bio", "O")

                # set gold token-level
                for gold_label in datapoint.get_labels(gold_label_type):
                    gold_span: Span = gold_label.data_point
                    prefix = "B-"
                    for token in gold_span:
                        token.set_label("gold_bio", prefix + gold_label.value)
                        prefix = "I-"

                sentence_flag = datapoint.get_labels(gold_label_type) != datapoint.get_labels(
                    gold_label_type + '_clean')

                # set clean token-level
                for clean_label in datapoint.get_labels(gold_label_type + '_clean'):
                    clean_span: Span = clean_label.data_point
                    prefix = "B-"
                    for token in clean_span:
                        token.set_label("clean_bio",
                                        prefix + clean_label.value)  # TODO: add checks, this only works if ner_clean column is given
                        prefix = "I-"

                # set predicted token-level
                for predicted_label in datapoint.get_labels("predicted"):
                    predicted_span: Span = predicted_label.data_point
                    prefix = "B-"
                    for token in predicted_span:
                        token.set_label("predicted_bio", prefix + predicted_label.value)
                        prefix = "I-"

                # now print labels in CoNLL format
                for token in datapoint:
                    gold = token.get_label('gold_bio').value
                    clean = token.get_label('clean_bio').value
                    pred = token.get_label('predicted_bio').value
                    eval_line = (
                        f"{token.text} "
                        f"{gold} "  # observed (noisy) label
                        f"{clean} "  # clean label
                        f"{pred} "  # predicted label
                        f"{pred == gold} "  # correct prediction flag
                        f"{gold != clean} "  # noisy flag 
                        f"{sentence_flag} "  # sentence noisy flag
                        f"{token.get_label('PD').score}\n"
                    )
                    lines.append(eval_line)
                lines.append("\n")

        else:
            for datapoint in batch:
                # print labels in CoNLL format
                for token in datapoint:
                    eval_line = (
                        f"{token.text} "
                        f"{token.get_label(gold_label_type).value} "
                        f"{token.get_label('predicted').value} "
                    )
                    lines.append(eval_line)
                lines.append("\n")

        return lines
