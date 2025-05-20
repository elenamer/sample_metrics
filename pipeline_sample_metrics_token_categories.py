# implement both standard and EE here
# enable config files, to choose criterion and action (relabel or mask loss)

import argparse
import json
import os

import numpy as np
import torch
import sys

sys.path.append('./')

import flair

from flair.datasets import ColumnCorpus
from flair.embeddings import TransformerWordEmbeddings
from sequence_tagger_model import SequenceTaggerTokenMetrics, EarlyExitSequenceTagger
from flair.trainers import ModelTrainer
from flair.data import (
    get_spans_from_bio,
)
from typing import Any, Dict, Set, Tuple, Union
from flair.data import Dictionary
from collections import Counter
import itertools
import sklearn
import logging

logging.basicConfig()
logger_experiment = logging.getLogger(__name__)
logger_experiment.setLevel(level="INFO")


category_conditions = {
    '1': (True, True),  # pred == observed, observed == O
    '2': (False, True),  # pred != observed, observed == O
    '3': (True, False),
    '4': (False, False)
}


def get_data_paths(config, corpus_name):
    train_extension = config["paths"]["train_filename_extension"]
    dev_extension = config["paths"]["dev_filename_extension"]
    test_extension = config["paths"]["test_filename_extension"]
    data_path = config["paths"]["data_path"]

    train_filename = f"{data_path}{corpus_name}{train_extension}"

    if "clean" in dev_extension:
        dev_filename = f"{data_path}{dev_extension}"
    else:
        dev_filename = f"{data_path}{corpus_name}{dev_extension}"

    if "clean" in test_extension:
        test_filename = f"{data_path}{test_extension}"
    else:
        test_filename = f"{data_path}{corpus_name}{test_extension}"

    return train_filename, dev_filename, test_filename


def run_standard_baseline(seed, corpus_name, config, max_epochs):
    learning_rate = float(config["parameters"]["learning_rate"])
    batch_size = int(config["parameters"]["batch_size"])
    num_epochs = max_epochs
    metrics_mode = config["parameters"]["metrics_mode"]

    if 'baseline_paths' in config['paths']:
        # this is if the function is called from run.py
        baseline_path = config['paths']['baseline_paths']['standard']
    else:
        # this is if the function is called from main()
        baseline_path = f"{config['paths']['resources_path']}baseline/standard"

    output_path_training = f"{baseline_path}/{corpus_name}/{str(seed)}"

    train_filename, dev_filename, test_filename = get_data_paths(config, corpus_name)

    tag_type = 'ner'

    if 'document_separator_token' in config['parameters']:
        if config['parameters']['document_separator_token'] != False:
            document_separator_token = config['parameters']['document_separator_token']
        else:
            document_separator_token = None
    else:
        document_separator_token = "-DOCSTART-"

    conll_corpus = ColumnCorpus(
        data_folder="./",
        column_format={0: "text", 1: tag_type + "_clean", 2: tag_type},  # if we work with nessie (two-column) format
        document_separator_token=document_separator_token,  # EST
        train_file=train_filename,
        dev_file=dev_filename,
        test_file=test_filename,
        column_delimiter='\t'
    )


    tag_dictionary = conll_corpus.make_label_dictionary(label_type=tag_type, add_unk=False)
    
    if 'use_context' in config['parameters']:
        use_context = config['parameters']['use_context']
    else:
        use_context = True

    embeddings = TransformerWordEmbeddings(
        model=config["parameters"]["model"],
        layers="-1",
        subtoken_pooling="first",
        fine_tune=True,
        use_context=use_context,  # EST
    )

    tagger = SequenceTaggerTokenMetrics(
        hidden_size=256,
        embeddings=embeddings,
        tag_dictionary=tag_dictionary,
        tag_type=tag_type,
        use_crf=False,
        use_rnn=False,
        reproject_embeddings=False,
        calculate_sample_metrics=True,
        metrics_mode=metrics_mode,
        metrics_save_list=[]
    )

    fine_tuning_args = {
        "base_path": output_path_training,
        "learning_rate": learning_rate,
        "mini_batch_size": batch_size,
        "max_epochs": num_epochs,
        "save_final_model": False,
        "monitor_test": config["parameters"]["monitor_test"],
        "monitor_train_sample": 1.0,
    }

    # PHASE 1: Retrain the model with updated labels
    trainer = ModelTrainer(tagger, conll_corpus)

    if config["parameters"]["scheduler"] and config["parameters"]["scheduler"] == "None":
        fine_tuning_args["scheduler"] = None

    tagger.print_out_path = output_path_training

    out = trainer.fine_tune(**fine_tuning_args)


    return baseline_path, out["test_score"]


def run_EE_baseline(seed, corpus_name, config, max_epochs):
    initialize_decoders_lr = float(config["parameters"]["decoder_init"]["lr"])
    num_epochs_decoder_init = int(config["parameters"]["decoder_init"]["num_epochs"])

    learning_rate = float(config["parameters"]["learning_rate"])
    batch_size = int(config["parameters"]["batch_size"])
    num_epochs = max_epochs
    metrics_mode = config["parameters"]["metrics_mode"]

    if 'baseline_paths' in config['paths']:
        # this is if the function is called from run.py
        baseline_path = config['paths']['baseline_paths']['EE']
    else:
        # this is if the function is called from main()
        baseline_path = f"{config['paths']['resources_path']}baseline/EE"

    output_path_training = f"{baseline_path}/{corpus_name}/{str(seed)}_with_init-{initialize_decoders_lr}"

    train_filename, dev_filename, test_filename = get_data_paths(config, corpus_name)

    tag_type = 'ner'

    if 'document_separator_token' in config['parameters']:
        if config['parameters']['document_separator_token'] != False:
            document_separator_token = config['parameters']['document_separator_token']
        else:
            document_separator_token = None
    else:
        document_separator_token = "-DOCSTART-"

    conll_corpus = ColumnCorpus(
        data_folder="./",
        column_format={0: "text", 1: "ner_clean", 2: "ner"},  # if we work with nessie (two-column) format
        document_separator_token=document_separator_token,  # EST
        train_file=train_filename,
        dev_file=dev_filename,
        test_file=test_filename,
        column_delimiter='\t'
    )

    tag_dictionary = conll_corpus.make_label_dictionary(label_type=tag_type, add_unk=False)

    # Load embeddings
    embeddings = TransformerWordEmbeddings(
        model="xlm-roberta-large",
        layers="all",
        subtoken_pooling="first",
        fine_tune=True,
        use_context=False,  # maybe it should be True?
        layer_mean=False,
    )

    # initialize tagger
    tagger = EarlyExitSequenceTagger(
        hidden_size=256,
        embeddings=embeddings,
        tag_dictionary=tag_dictionary,
        tag_type=tag_type,
        use_crf=False,
        use_rnn=False,
        reproject_embeddings=False,
        weighted_loss=False,
        print_all_predictions=False,
        calculate_sample_metrics=True,
        metrics_mode=metrics_mode,
        metrics_save_list=[]
    )

    # initialize trainer
    trainer = ModelTrainer(tagger, conll_corpus)

    # initialize decoders
    # First, train only the offramp-decoders
    tagger.embeddings.fine_tune = False
    tagger.embeddings.static_embeddings = True

    # init all decoders equally
    tagger.weighted_loss = False
    tagger.modified_loss = False

    # decoder init
    trainer.fine_tune(
        output_path_training + os.sep + "decoder_init",
        learning_rate=initialize_decoders_lr,
        mini_batch_size=batch_size,
        max_epochs=num_epochs_decoder_init,
        save_final_model=False,
        monitor_test=False,  #
        monitor_train_sample=1.0,  #
    )  #

    tagger.print_out_path = output_path_training
    tagger.print_all_predictions = True

    if metrics_mode == 'epoch_end':
        # copy last decoder init to be epoch 0
        os.rename(output_path_training + os.sep + 'decoder_init' + os.sep + f'epoch_log_{num_epochs_decoder_init}.log',
                  output_path_training + os.sep + 'epoch_log_0.log')

        tagger.calculate_sample_metrics = True
        kwargs = {}
        kwargs['final_train_eval'] = True
        tagger.evaluate(
            conll_corpus.test, gold_label_type=tag_type, out_path=output_path_training + os.sep + "train_sample_0.tsv",
            **kwargs
        )
        os.rename(output_path_training + os.sep + 'decoder_init' + os.sep + f'epoch_log_{num_epochs_decoder_init}.log',
                  output_path_training + os.sep + 'epoch_log_0_test.log')

        tagger.evaluate(
            conll_corpus.dev, gold_label_type=tag_type, out_path=output_path_training + os.sep + "train_sample_0.tsv",
            **kwargs
        )
        os.rename(output_path_training + os.sep + 'decoder_init' + os.sep + f'epoch_log_{num_epochs_decoder_init}.log',
                  output_path_training + os.sep + 'epoch_log_0_dev.log')

    tagger.embeddings.fine_tune = True
    tagger.embeddings.static_embeddings = False
    tagger.calculate_sample_metrics = True

    fine_tuning_args = {
        "base_path": output_path_training,
        "learning_rate": learning_rate,
        "mini_batch_size": batch_size,
        "max_epochs": num_epochs,
        "save_final_model": False,
        "monitor_test": config["parameters"]["monitor_test"],
        "monitor_train_sample": 1.0,
    }

    if config["parameters"]["scheduler"] and config["parameters"]["scheduler"] == "None":
        fine_tuning_args["scheduler"] = None

    trainer = ModelTrainer(tagger, conll_corpus)

    # fine-tune
    out = trainer.fine_tune(**fine_tuning_args)

    return baseline_path, out["test_score"]


def run_baseline(mode, seed, corpus_name, config, max_epochs):
    if mode == 'EE':
        return run_EE_baseline(seed, corpus_name, config, max_epochs)
    else:
        return run_standard_baseline(seed, corpus_name, config, max_epochs)


def update_dataset_with_epoch_log_info(epoch_log_path, dataset, metric, predicted_bio_column, tag_bio_column):
    with open(epoch_log_path, 'r') as f:
        lines = f.readlines()
        columns = lines[0].split('\t')
        sentence = None
        for line in lines[1:]:
            if len(line) == 1:
                sentence = None
                continue

            line = line.strip().split('\t')

            if sentence is None:
                sentence_id = int(line[columns.index('sent_index')])
                sentence = dataset[sentence_id]

            token_id = line[columns.index('token_index')]
            metric_value = float(line[columns.index(metric)])

            token_id = int(token_id)

            token = sentence[token_id]

            predicted_bio = line[columns.index('predicted')]
            tag_bio = line[columns.index('noisy')]

            token.set_label(predicted_bio_column, predicted_bio)
            token.set_label(tag_bio_column, tag_bio)
            token.set_metric(metric, metric_value)


def output_bio_dataset(dataset, tag_column, filename):
    if not os.path.exists(os.path.dirname(filename)):
        os.makedirs(os.path.dirname(filename))

    with open(filename, "w") as f:
        for sentence in dataset:
            for token in sentence:
                val = token.get_label(tag_column).value
                if val.startswith('S-'):
                    val = val.replace('S-', 'B-')
                if val.startswith('E-'):
                    val = val.replace('E-', 'I-')
                f.write('\t'.join([token.text, val]))
                f.write('\n')
            f.write('\n')


def calculate_f1_between_columns(dataset, column1, column2, label_dictionary):
    all_spans: Set[str] = set()
    all_true_values = {}
    all_predicted_values = {}
    sentence_id = 0

    # get the gold labels
    for datapoint in dataset:
        for gold_label in datapoint.get_labels(column1):
            representation = str(sentence_id) + ": " + gold_label.unlabeled_identifier

            value = gold_label.value

            if representation not in all_true_values:
                all_true_values[representation] = [value]
            else:
                all_true_values[representation].append(value)

            if representation not in all_spans:
                all_spans.add(representation)

        for predicted_span in datapoint.get_labels(column2):
            representation = str(sentence_id) + ": " + predicted_span.unlabeled_identifier

            # add to all_predicted_values
            if representation not in all_predicted_values:
                all_predicted_values[representation] = [predicted_span.value]
            else:
                all_predicted_values[representation].append(predicted_span.value)

            if representation not in all_spans:
                all_spans.add(representation)

        sentence_id += 1

    # convert true and predicted values to two span-aligned lists
    true_values_span_aligned = []
    predicted_values_span_aligned = []
    for span in all_spans:
        list_of_gold_values_for_span = all_true_values[span] if span in all_true_values else ["O"]
        # if after excluding labels, no label is left, ignore the datapoint
        if not list_of_gold_values_for_span:
            continue
        true_values_span_aligned.append(list_of_gold_values_for_span)
        predicted_values_span_aligned.append(
            all_predicted_values[span] if span in all_predicted_values else ["O"]
        )

    # make the evaluation dictionary
    evaluation_label_dictionary = Dictionary(add_unk=False)
    evaluation_label_dictionary.add_item("O")
    for true_values in all_true_values.values():
        for label in true_values:
            evaluation_label_dictionary.add_item(label)
    for predicted_values in all_predicted_values.values():
        for label in predicted_values:
            evaluation_label_dictionary.add_item(label)

    # compute numbers by formatting true and predicted such that Scikit-Learn can use them
    y_true = []
    y_pred = []
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

    for label_name, _count in counter.most_common():
        if label_name == "O":
            continue
        # if label_name == 'MASK':
        #     continue
        target_names.append(label_name)
        labels.append(evaluation_label_dictionary.get_idx_for_item(label_name))

    # there is at least one gold label or one prediction (default)
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
    confusion_matrix = sklearn.metrics.confusion_matrix(y_true, y_pred, labels=labels)

    # compute accuracy separately as it is not always in classification_report (e.. when micro avg exists)
    accuracy_score = round(sklearn.metrics.accuracy_score(y_true, y_pred), 4)

    # if there is only one label, then "micro avg" = "macro avg"
    if len(target_names) == 1:
        classification_report_dict["micro avg"] = classification_report_dict["macro avg"]

    # The "micro avg" appears only in the classification report if no prediction is possible.
    # Otherwise, it is identical to the "macro avg". In this case, we add it to the report.
    if "micro avg" not in classification_report_dict:
        classification_report_dict["micro avg"] = {}
        for precision_recall_f1 in classification_report_dict["macro avg"]:
            classification_report_dict["micro avg"][precision_recall_f1] = classification_report_dict[
                "accuracy"
            ]

    detailed_result = (
            "\nResults:"
            f"\n- F-score (micro) {round(classification_report_dict['micro avg']['f1-score'], 4)}"
            f"\n- F-score (macro) {round(classification_report_dict['macro avg']['f1-score'], 4)}"
            f"\n- Accuracy {accuracy_score}"
            "\n\nBy class:\n" + classification_report
    )

    logger_experiment.info(detailed_result)

    # Create and populate score object for logging with all evaluation values, plus the loss
    scores: Dict[Union[Tuple[str, ...], str], Any] = {}

    for avg_type in ("micro avg", "macro avg"):
        for metric_type in ("f1-score", "precision", "recall"):
            scores[(avg_type, metric_type)] = classification_report_dict[avg_type][metric_type]

    scores["accuracy"] = accuracy_score

    return scores, detailed_result


def relabel_category(dataset, tag_column='ner', new_tag_column='ner_new', prediction_bio_column='predicted_bio',
                     metric='confidence', threshold=0.7, direction='left', category_id='2'):
    # can only be used with category 2 and 4.
    tag_column_bio = f'{tag_column}_bio'
    new_tag_column_bio = f'{new_tag_column}_bio'
    tokens_changed = 0
    tokens_changed_additionally = 0

    for sent in dataset:
        flag = False
        prev = 'O'
        previous_changed = False

        for token in sent:
            if (not token.get_label(tag_column_bio).value.endswith('MASK')) and (
                    token.get_label(tag_column_bio).value == token.get_label(prediction_bio_column).value) == \
                    category_conditions[category_id][0] and (token.get_label(tag_column_bio).value == 'O') == \
                    category_conditions[category_id][1]:  # if incorrect prediction
                flag = True
                if direction == 'left':
                    if token.get_metric(metric) < float(threshold):  # rule for masking
                        token.set_label(new_tag_column_bio, token.get_label(prediction_bio_column).value)
                        tokens_changed += 1
                        prev = token.get_label(prediction_bio_column).value
                        previous_changed = True
                else:
                    if token.get_metric(metric) > float(threshold):  # rule for masking
                        token.set_label(new_tag_column_bio, token.get_label(prediction_bio_column).value)
                        tokens_changed += 1
                        prev = token.get_label(prediction_bio_column).value
                        previous_changed = True
            else:
                if (previous_changed and prev != 'O') and (
                        token.get_label(tag_column_bio).value.endswith(prev.split('-')[1]) and token.get_label(
                        prediction_bio_column).value.endswith(
                        prev.split('-')[1])):  # if predicted is same entity type as prev and as observed
                    token.set_label(new_tag_column_bio, token.get_label(prediction_bio_column).value)
                    tokens_changed_additionally += 1
                else:
                    previous_changed = False

        # sent.remove_labels(tag_column)
        bioes_tags = [token.get_label(new_tag_column_bio).value for token in sent]
        predicted_spans = get_spans_from_bio(bioes_tags)
        for span_indices, score, label in predicted_spans:
            span = sent[span_indices[0]: span_indices[-1] + 1]
            if label != "O":
                span.set_label(new_tag_column, value=label, score=score)
    return tokens_changed, tokens_changed_additionally


def mask_category(dataset, tag_column='ner', new_tag_column='ner_new', prediction_bio_column='predicted_bio',
                  metric='confidence', threshold=0.7, direction='left', category_id='1'):
    tag_column_bio = f'{tag_column}_bio'
    new_tag_column_bio = f'{new_tag_column}_bio'
    tokens_changed = 0

    for sent in dataset:
        # debug
        logger_experiment.debug(sent.text)
        logger_experiment.debug('predicted')
        logger_experiment.debug(sent.get_labels(prediction_bio_column))
        logger_experiment.debug('ner')
        logger_experiment.debug(sent.get_labels(tag_column))
        for token in sent:
            logger_experiment.debug(
                f"{token.text} {token.get_label(prediction_bio_column).value} {token.get_label(tag_column_bio).value}")
        flag = False
        for token in sent:
            if (not token.get_label(tag_column_bio).value.endswith('MASK')) and (
                    token.get_label(tag_column_bio).value == token.get_label(prediction_bio_column).value) == \
                    category_conditions[category_id][0] and (token.get_label(tag_column_bio).value == 'O') == \
                    category_conditions[category_id][1]:  # if incorrect prediction
                if direction == 'left':
                    if token.get_metric(metric) < float(threshold):  # rule for masking
                        flag = True
                        token.set_label(new_tag_column_bio, 'S-MASK')
                        tokens_changed += 1

                else:
                    if token.get_metric(metric) > float(threshold):  # rule for masking
                        flag = True
                        token.set_label(new_tag_column_bio, 'S-MASK')
                        tokens_changed += 1


        # sent.remove_labels(tag_column)

        bioes_tags = [token.get_label(new_tag_column_bio).value for token in sent]
        predicted_spans = get_spans_from_bio(bioes_tags)

        for span_indices, score, label in predicted_spans:
            span = sent[span_indices[0]: span_indices[-1] + 1]
            if label != "O":
                span.set_label(new_tag_column, value=label, score=score)
    return tokens_changed, 0


def add_bioes_ner_tags(dataset, tag_column='ner', bio_tag_column=None):
    # add a new column to dataset, which contains token-level tags for NER in BIOES (observed label)
    if bio_tag_column is None:
        bio_tag_column = f'{tag_column}_bio'

    for sent in dataset:
        for token in sent:
            token.add_label(bio_tag_column, 'O')
            token.set_label('modified', False)

        for span in sent.get_spans(tag_column):
            if len(span.tokens) == 1:
                span[0].set_label(bio_tag_column, f'S-{span.get_label(tag_column).value}')
            else:
                span[0].set_label(bio_tag_column, f'B-{span.get_label(tag_column).value}')
                span[-1].set_label(bio_tag_column, f'E-{span.get_label(tag_column).value}')
                for i in range(1, len(span) - 1):
                    span[i].set_label(bio_tag_column, f'I-{span.get_label(tag_column).value}')


def copy_new_tag_to_original(dataset, tag_column='ner', new_tag_column='ner_new'):
    for sent in dataset:
        for lab in sent.get_labels(new_tag_column):
            lab.data_point.set_label(tag_column, lab.value)


def run_experiment(seed, config, category_configs, corpus_name, tag_type, category_id, paths_to_baselines, output_path):
    logger_experiment.debug('DEBUGGING')

    train_filename, dev_filename, test_filename = get_data_paths(config, corpus_name)

    if 'document_separator_token' in config['parameters']:
        if config['parameters']['document_separator_token'] != False:
            document_separator_token = config['parameters']['document_separator_token']
        else:
            document_separator_token = None
    else:
        document_separator_token = "-DOCSTART-"

    conll_corpus = ColumnCorpus(
        data_folder="./",
        column_format={0: "text", 1: "ner_clean", 2: "ner"},  # if we work with nessie (two-column) format
        document_separator_token=document_separator_token,  # EST
        train_file=train_filename,
        dev_file=dev_filename,
        test_file=test_filename,
        column_delimiter='\t'
    )

    tag_dictionary = conll_corpus.make_label_dictionary(label_type=tag_type + '_clean', add_unk=False)

    calculate_f1_between_columns(conll_corpus.train, tag_type + '_clean', tag_type, label_dictionary=tag_dictionary)

    add_bioes_ner_tags(conll_corpus.train, tag_column=tag_type)
    output_bio_dataset(conll_corpus.train, tag_column=tag_type + '_bio',
                       filename=f'{output_path}/noise_crowd_backup.train')

    learning_rate = float(config["parameters"]["learning_rate"])
    batch_size = int(config["parameters"]["batch_size"])
    num_epochs = int(config["parameters"]["num_epochs"])

    flair.set_seed(seed)

    output_path_training = f"{output_path}/{seed}"

    if not os.path.exists(output_path_training):
        os.makedirs(output_path_training)
    logger_experiment.debug(len(conll_corpus.train))

    ## main code block
    mask_flag = False

    if category_id != '0':

        noise_f1s = []

        add_bioes_ner_tags(conll_corpus.train, tag_column=tag_type, bio_tag_column=tag_type + '_new_bio')

        for category_config in category_configs:

            logger_experiment.debug(category_config)

            if category_config['modification'] == 'mask':
                mask_flag = True

            current_epoch = category_config["epoch_change"]
            current_metric = category_config["metric"]
            current_threshold = float(category_config["threshold"])
            current_direction = category_config["direction"].strip()
            current_id = category_config["id"]

            # gradually change the labels of 'new_ner' column
            if config['parameters']['seq_tagger_mode'] == 'standard':
                epoch_file = f"{paths_to_baselines[config['parameters']['seq_tagger_mode']]}/{corpus_name}/{seed}/epoch_log_{current_epoch}.log"
            else:
                epoch_file = f"{paths_to_baselines[config['parameters']['seq_tagger_mode']]}/{corpus_name}/{seed}_with_init-{config['parameters']['decoder_init']['lr']}/epoch_log_{current_epoch}.log"

            if not os.path.exists(epoch_file):
                raise Exception(f"File {epoch_file} does not exist. Please provide a valida baseline path.")

            update_dataset_with_epoch_log_info(epoch_file, conll_corpus.train, metric=current_metric,
                                               predicted_bio_column='predicted_bio',
                                               tag_bio_column='ner_bio')  # predicted_bio, ner, ner_bio

            # PHASE 2: Relabel categories
            logger_experiment.debug(len(conll_corpus.train))

            if category_config['modification'] == 'relabel':
                tokens_changed, tokens_changed_additionally = relabel_category(conll_corpus.train, tag_column=tag_type,
                                                                               prediction_bio_column='predicted_bio',
                                                                               metric=current_metric,
                                                                               threshold=current_threshold,
                                                                               direction=current_direction,
                                                                               category_id=current_id)
                logger_experiment.debug('number of tokens changed:', tokens_changed)
                logger_experiment.debug('number of consecutive tokens changed:', tokens_changed_additionally)

            elif category_config['modification'] == 'mask':
                tokens_changed, tokens_changed_additionally = mask_category(conll_corpus.train, tag_column=tag_type, prediction_bio_column='predicted_bio',
                              metric=current_metric, threshold=current_threshold, direction=current_direction,
                              category_id=current_id)
                logger_experiment.debug('number of tokens changed:', tokens_changed)

            score, detailed_result = calculate_f1_between_columns(conll_corpus.train, 'ner_new', 'ner_clean',
                                                                  label_dictionary=tag_dictionary)

            noise_f1s.append(score[('micro avg', 'f1-score')])

        with open(f'{output_path_training}/noise_f1.txt', 'w') as f:
            for noise_f1 in reversed(noise_f1s):
                f.write(f'{noise_f1}\n')
                # f.write(detailed_result)
                f.write(f'{tokens_changed}\n')
                f.write(f'{tokens_changed_additionally}\n')


        output_bio_dataset(conll_corpus.train, tag_column='ner_new_bio',
                           filename=f'{output_path_training}/noise_crowd_relabeled.train')

        copy_new_tag_to_original(conll_corpus.train, tag_column=tag_type, new_tag_column=tag_type + '_new')

    # PHASE 3: Retrain the model with updated labels

    if True:
        # model_reinit is always True for now
        # if config["parameters"]["model_reinit"] or config["parameters"]["seq_tagger_mode"] == 'EE': 
    
        if 'use_context' in config['parameters']:
            use_context = config['parameters']['use_context']
        else:
            use_context = True

        embeddings = TransformerWordEmbeddings(
            model=config["parameters"]["model"],
            layers="-1",
            subtoken_pooling="first",
            fine_tune=True,
            use_context=use_context,  # EST
        )
        if category_id != 'O' and mask_flag == True:
            tag_dictionary.add_item('MASK')
            tagger = SequenceTaggerTokenMetrics(
                hidden_size=256,
                embeddings=embeddings,
                tag_dictionary=tag_dictionary,
                tag_type=tag_type,  # this is where the relabelling efectively happens
                use_crf=False,
                use_rnn=False,
                reproject_embeddings=False,
                calculate_sample_metrics=False,
                loss_weights={'S-MASK': 0.0, 'B-MASK': 0.0, 'E-MASK': 0.0, 'I-MASK': 0.0}
            )
        else:
            tagger = SequenceTaggerTokenMetrics(
                hidden_size=256,
                embeddings=embeddings,
                tag_dictionary=tag_dictionary,
                tag_type=tag_type,
                use_crf=False,
                use_rnn=False,
                reproject_embeddings=False,
                calculate_sample_metrics=False
            )

    trainer = ModelTrainer(tagger, conll_corpus)

    fine_tuning_args = {
        "base_path": output_path_training + os.sep + 'phase3',
        "learning_rate": float(learning_rate),
        "mini_batch_size": int(batch_size),
        "max_epochs": num_epochs,
        "save_final_model": False,
        "monitor_test": config["parameters"]["monitor_test"],
        "monitor_train_sample": 1.0,
    }

    out = trainer.fine_tune(**fine_tuning_args)  # out: after phase 3

    return out["test_score"]


def main(config, gpu=0):
    flair.device = torch.device("cuda:" + str(gpu))

    corpora = config["corpora"]

    seq_tagger_mode = config["parameters"]["seq_tagger_mode"]
    paths_to_baselines = config['paths']['baseline_paths']

    category_config_empty = {
        'metric': '',
        'f_type': '',
        'modification': '',
        'threshold': '',
        'direction': '',
        'epoch_change': ''
    }

    category_configs = []
    category_ids = []

    for cat_id in category_conditions:
        category_config = config["parameters"]['modify_category' + cat_id]
        logger_experiment.debug(category_config)
        if category_config is not False:
            category_ids.append(cat_id)
            category_config['id'] = cat_id
            category_configs.append(category_config)

    category_configs = sorted(category_configs, key=lambda x: int(x['epoch_change']))

    if len(category_configs) == 0:
        category_configs.append(category_config_empty)

    if len(category_ids) > 0:
        category_id = ''.join(category_ids)
    else:
        category_id = '0'

    experiment_path = config["paths"]["resources_path"] + "category" + category_id + os.sep + f"{seq_tagger_mode}_{category_configs[0]['metric']}" + os.sep + \
                          category_configs[0]['f_type'] + os.sep + category_configs[0]['modification'] + os.sep

    seeds = [int(seed) for seed in config['seeds']]
    tag_type = "ner"

    if category_id != '0':
        if not os.path.exists(experiment_path):
            os.makedirs(experiment_path)

        with open(experiment_path + "config.json", "w", encoding='utf-8') as f:
            json.dump(config, f)

    for corpus_name in corpora:
        output_path = experiment_path + corpus_name
        
        flag_run_baseline = True 

        if len(paths_to_baselines) > 0 and os.path.exists(f"{config['paths']['baseline_paths'][seq_tagger_mode]}/{corpus_name}/test_results.tsv"): # here we assume that the baseline was ran for all seeds and for the correct seeds.
            # if a valid baseline path is not provided, then run the baseline
            flag_run_baseline = False
            
        temp_f1_scores = []
        temp_baseline_f1_scores = []

        for seed in seeds:

            if flag_run_baseline:
                if category_id == '0':
                    max_epochs = int(config['parameters']['num_epochs'])
                else:
                    max_epochs = int(category_configs[-1]['epoch_change'])
                path_to_baselines_seed, baseline_score = run_baseline(seq_tagger_mode, seed, corpus_name, config,
                                                                                max_epochs)
                temp_baseline_f1_scores.append(baseline_score)

            if category_id != '0':
                score = run_experiment(seed, config, category_configs, corpus_name, tag_type, category_id,
                                       paths_to_baselines, output_path)
            elif flag_run_baseline:
                score = baseline_score
            else:
                score = 0

            temp_f1_scores.append(score)

        with open(output_path + os.sep + "test_results.tsv", "w", encoding='utf-8') as f:
            f.write("params\tmean\tstd\n")
            label = "f1"
            f.write(f"{label} \t{np.mean(temp_f1_scores)!s} \t {np.std(temp_f1_scores)!s} \n")

        if flag_run_baseline:
            with open(paths_to_baselines[seq_tagger_mode] + os.sep + corpus_name+os.sep+"test_results.tsv", "w", encoding='utf-8') as f:
                f.write("params\tmean\tstd\n")
                label = "f1"
                f.write(f"{label} \t{np.mean(temp_baseline_f1_scores)!s} \t {np.std(temp_baseline_f1_scores)!s} \n")


if __name__ == "__main__":
    argParser = argparse.ArgumentParser()

    argParser.add_argument("-c", "--config", help="filename with experiment configuration")
    argParser.add_argument("-g", "--gpu", help="set gpu id", default=0)
    # set gpu ID

    args = argParser.parse_args()

    with open(args.config) as json_file:
        config = json.load(json_file)

    main(config, gpu=args.gpu)
