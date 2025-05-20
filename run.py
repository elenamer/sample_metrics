import argparse
import json
from pipeline_sample_metrics_token_categories import *
from optimize_metric_parameters import *
from summarize_scores_sample_metrics import *
import os
import logging
import datetime
from pathlib import Path
import socket
from copy import deepcopy

def get_combined_config(config, mode):

    source_corpus = '_'.join(config['source_corpora'])

    if mode == 'EE':
        base_config = {
        "experiment_name": "relabel_combined_source_"+source_corpus,
        "paths": {
            "resources_path": f"{config['paths']['resources_path']}/relabel_combined_source_{source_corpus}/",
            "data_path":config['paths']['data_path'],
            "train_filename_extension" :config['paths']['train_filename_extension'],
            "dev_filename_extension" :config['paths']['dev_filename_extension'],
            "test_filename_extension" :config['paths']['test_filename_extension'],
            "baseline_paths":{
                "EE":config['paths']['baseline_paths']['EE'],
                "standard":config['paths']['baseline_paths']['standard'],
            }
        },
        "parameters": {
            "batch_size":config['parameters']['batch_size'],
            "learning_rate":config['parameters']['learning_rate'],
            "num_epochs":config['parameters']['num_epochs'],
            "model":config['parameters']['model'],
            "monitor_test":config['parameters']['monitor_test'],
            "scheduler":config['parameters']['scheduler'],
            "metrics_mode":config['parameters']['metrics_mode'],
            "model_reinit":config['parameters']['model_reinit'],
            "decoder_init":config['parameters']['decoder_init'],
            "seq_tagger_mode":mode,
            "modify_category1": {
                                'epoch_change': 1,
                                'metric':'tac',
                                'f_type':'f05',
                                'threshold':20,
                                'direction':'left',
                                'modification':'mask'
                                },
            "modify_category2": {
                                'epoch_change': 5,
                                'metric':'tac',
                                'f_type':'f05',
                                'threshold':24,
                                'direction':'left',
                                'modification':'relabel'
                                },
            "modify_category3": False,
            "modify_category4": {
                                'epoch_change': 4,
                                'metric':'tac',
                                'f_type':'f05',
                                'threshold':6,
                                'direction':'left',
                                'modification':'relabel'
                                },
        },
        "corpora" : config['corpora'],
        "seeds":config['seeds']
        }
    else:
        base_config = {
        "experiment_name": "relabel_combined_source_"+source_corpus,
        "paths": {
            "resources_path": f"{config['paths']['resources_path']}/relabel_combined_source_{source_corpus}/",
            "data_path":config['paths']['data_path'],
            "train_filename_extension" :config['paths']['train_filename_extension'],
            "dev_filename_extension" :config['paths']['dev_filename_extension'],
            "test_filename_extension" :config['paths']['test_filename_extension'],
            "baseline_paths":{
                "EE":config['paths']['baseline_paths']['EE'],
                "standard":config['paths']['baseline_paths']['standard'],
            }
        },
        "parameters": {
            "batch_size":config['parameters']['batch_size'],
            "learning_rate":config['parameters']['learning_rate'],
            "num_epochs":config['parameters']['num_epochs'],
            "model":config['parameters']['model'],
            "monitor_test":config['parameters']['monitor_test'],
            "scheduler":config['parameters']['scheduler'],
            "metrics_mode":config['parameters']['metrics_mode'],
            "model_reinit":config['parameters']['model_reinit'],
            "decoder_init":config['parameters']['decoder_init'],
            "seq_tagger_mode":mode,
            "modify_category1": {
                                'epoch_change': 9,
                                'metric':'variability',
                                'f_type':'f05',
                                'threshold':0.15,
                                'direction':'right',
                                'modification':'mask'
                                },
            "modify_category2": {
                                'epoch_change': 2,
                                'metric':'confidence',
                                'f_type':'f05',
                                'threshold':0.4,
                                'direction':'left',
                                'modification':'relabel'
                                },
            "modify_category3": False,
            "modify_category4": {
                                'epoch_change': 4,
                                'metric':'confidence',
                                'f_type':'f05',
                                'threshold':0.3,
                                'direction':'left',
                                'modification':'relabel'
                                },
        },
        "corpora" : config['corpora'],
        "seeds":config['seeds']
        }

    return base_config

def output_configs(config, category_table_path, cat_id, mode, metrics_list, only_best = False):
    data = pd.read_csv(category_table_path, header = 0, index_col=[0,1])
    data.columns = data.columns.str.strip()
    experiment_configs = []

    source_corpus = '_'.join(config['source_corpora'])

    base_config = {
    "experiment_name": "relabel_cat"+cat_id+"_source_"+source_corpus,
    "paths": {
        "resources_path": f"{config['paths']['resources_path']}/relabel_cat{cat_id}_source_{source_corpus}/",
        "data_path":config['paths']['data_path'],
        "train_filename_extension" :config['paths']['train_filename_extension'],
        "dev_filename_extension" :config['paths']['dev_filename_extension'],
        "test_filename_extension" :config['paths']['test_filename_extension'],
        "baseline_paths":{
            "EE":config['paths']['baseline_paths']['EE'],
            "standard":config['paths']['baseline_paths']['standard'],
        }
    },
    "parameters": {
        "batch_size":config['parameters']['batch_size'],
        "learning_rate":config['parameters']['learning_rate'],
        "num_epochs":config['parameters']['num_epochs'],
        "model":config['parameters']['model'],
        "monitor_test":config['parameters']['monitor_test'],
        "scheduler":config['parameters']['scheduler'],
        "metrics_mode":config['parameters']['metrics_mode'],
        "model_reinit":config['parameters']['model_reinit'],
        "decoder_init":config['parameters']['decoder_init'],
        "modify_category1":False,
        "modify_category2":False,
        "modify_category3":False,
        "modify_category4":False,
    },
    "corpora" : config['corpora'],
    "seeds":config['seeds']
    }
    if only_best:

        temp_data = data[data.index.get_level_values(1).str.contains('f05')]
        print(temp_data)
        best_params_index = temp_data['score'].idxmax()
        row = data.loc[best_params_index]

        current_config = deepcopy(base_config)
        current_config['parameters']['seq_tagger_mode'] = mode

        # add current category modification parameters with 'mask' option
        current_config['parameters']['modify_category'+cat_id] = {
                                                        'epoch_change': str(row['epoch']).strip(),
                                                        'metric':str(row.name[0]).strip(),
                                                        'f_type':row.name[1].strip(),
                                                        'threshold':str(row['threshold']).strip(),
                                                        'direction':row['direction'],
                                                        'modification':'mask'
                                                        }
        experiment_configs.append(deepcopy(current_config))
        
        if int(cat_id) == 2 or int(cat_id) == 4:
            # add current category modification parameters with 'relabel' option
            # *only for categories 2 and 4 (because we have an alternative label there: the predicted one)
            current_config['parameters']['modify_category'+cat_id] = {
                                                        'epoch_change': str(row['epoch']).strip(),
                                                        'metric':str(row.name[0]).strip(),
                                                        'f_type':row.name[1].strip(),
                                                        'threshold':str(row['threshold']).strip(),
                                                        'direction':row['direction'].strip(),
                                                        'modification':'relabel'
                                                        }
            experiment_configs.append(current_config)
    else:
        for ind, row in data.iterrows():
            if str(ind[0]).strip() in metrics_list:
                print(row['epoch'])

                current_config = deepcopy(base_config)
                current_config['parameters']['seq_tagger_mode'] = mode

                # add current category modification parameters with 'mask' option
                current_config['parameters']['modify_category'+cat_id] = {
                                                                'epoch_change': str(row['epoch']).strip(),
                                                                'metric':str(ind[0]).strip(),
                                                                'f_type':ind[1].strip(),
                                                                'threshold':str(row['threshold']).strip(),
                                                                'direction':row['direction'],
                                                                'modification':'mask'
                                                                }
                experiment_configs.append(deepcopy(current_config))
                
                if int(cat_id) == 2 or int(cat_id) == 4:
                    # add current category modification parameters with 'relabel' option
                    # *only for categories 2 and 4 (because we have an alternative label there: the predicted one)
                    current_config['parameters']['modify_category'+cat_id] = {
                                                                'epoch_change': str(row['epoch']).strip(),
                                                                'metric':str(ind[0]).strip(),
                                                                'f_type':ind[1].strip(),
                                                                'threshold':str(row['threshold']).strip(),
                                                                'direction':row['direction'].strip(),
                                                                'modification':'relabel'
                                                                }
                    experiment_configs.append(current_config)
    
    return experiment_configs
    


def setup_logging(config):
    servername = socket.gethostname()
    device = torch.cuda.current_device()

    base_path = config['paths']['resources_path']+os.sep+'logs'
    filename = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")+'_'+config['config_filepath']+'.log'

    base_path = Path(base_path)
    base_path.mkdir(parents=True, exist_ok=True)

    file = base_path / filename
    file.touch(exist_ok=True)

    logger_flair = logging.getLogger("flair")
    logger_flair.handlers[0].setLevel(logging.WARNING)

    logger_experiment = logging.getLogger(__name__)
    logger_experiment.setLevel(level="INFO")

    logFormatter = logging.Formatter("%(asctime)s [%(levelname)-5.5s]  %(message)s")

    fileHandler =    logging.FileHandler(file, mode="a", encoding="utf-8")

    fileHandler.setFormatter(logFormatter)
    logger_experiment.addHandler(fileHandler)


    logger_experiment.info(f"Hostname: {servername}")
    logger_experiment.info(f"GPU: {device}")
    logger_experiment.info(json.dumps(config, indent = 4))

    return logger_experiment


def run(config, gpu=0):

    logger_experiment = setup_logging(config)
    logger_experiment.setLevel(level="DEBUG")

    flair.device = torch.device("cuda:" + str(gpu))
    seeds = [int(seed) for seed in config['seeds']]
    corpora = config['corpora']
    combined_only_flag = False

    if 'combined_only' in config:
        combined_only_flag = config['combined_only']

    merged_corpora_names = '_'.join(config['source_corpora'])
    for mode in config['parameters']['modes']:
        parameter_settings_path = f"{config['paths']['results_tables_path']}/{merged_corpora_names}/{mode}_mode"

        if not os.path.exists(parameter_settings_path):

            for source_corpus in config['source_corpora']:

                # 1. Run baseline for each mode: standard fine-tuning and early-exit fine-tuning
                temp_f1_scores = []
                if not os.path.exists(f"{config['paths']['baseline_paths'][mode]}/{source_corpus}/{seeds[0]}/text_results.csv") and not os.path.exists(f"{config['paths']['baseline_paths'][mode]}/{source_corpus}/{seeds[0]}_with_init-{config['parameters']['decoder_init']['lr']}/test_results.csv"):
                    # both source and downstream corpora baseline paths are in the same folder
                    logger_experiment.info(f"Running baseline for {source_corpus}, {mode} mode")
                    for seed in seeds:
                        logger_experiment.info(f"Running seed {seed}")
                        # baseline paths don't have to be set beforehand
                        baseline_path, score = run_baseline(mode, seed, source_corpus, config,
                                                            int(config['parameters']['num_epochs']))

                        temp_f1_scores.append(score)

                    with open(baseline_path +os.sep+source_corpus+ os.sep + "test_results.tsv", "w", encoding='utf-8') as f:
                        f.write("params\tmean\tstd\n")
                        label = "f1"
                        f.write(f"{label} \t{np.mean(temp_f1_scores)!s} \t {np.std(temp_f1_scores)!s} \n")
                    logger_experiment.info(f"Baseline for {source_corpus}, {mode} mode for seeds {seeds} finished")

                # Optional: plot histograms of metrics for baseline runs, for each seed
                if config['plot_histograms']:
                    logger_experiment.info(f"Plotting histograms and lineplot for {source_corpus} corpus")
                    for mode in config['parameters']['modes']:
                        plot_metric_distributions(base_path=f"{config['paths']['baseline_paths'][mode]}/{source_corpus}/",
                                                mode=mode, seeds=seeds, sample_metrics=config['sample_metrics'][mode],
                                                dset='train', max_epochs=int(config['parameters']['num_epochs']) + 1)
                    plot_category_membership_through_epochs(base_paths = config['paths']['baseline_paths'] , corpus_name = source_corpus, seeds= seeds,dset = 'train', max_epochs=11)

            # 2. Find optimal parameter sets for each sample metric, mode and category
            logger_experiment.info(f"Optimizing F scores for {'_'.join(config['source_corpora'])}; saving to csv files and generating config json files; path: {parameter_settings_path}")
            optimize_F1s(config)#, corpus_name=source_corpus)

    if config['plot_histograms']:
        plot_train_score_percentage_through_epochs(base_paths = config['paths']['baseline_paths'], modes= config['parameters']['modes'],source_corpora = config['source_corpora'], seeds= seeds,dset = 'train', max_epochs=11, percentage=True)


    source_corpus = '_'.join(config['source_corpora'])

    # 3. Run experiment (relabel or mask each category) based on the optimal parameter sets from 2. 
    if not config['only_results_summarization']:
        # we can set the only_results_summarization to true if we only want to re-generate the summary tables, and not re-run the experiments
        for mode in config['parameters']['modes']:
            if not combined_only_flag:
                parameter_settings_path = f"{config['paths']['results_tables_path']}/{source_corpus}/{mode}_mode"

                logger_experiment.info(f"Running experiment for {mode} mode.")
                metrics_list = config['sample_metrics'][mode]

                for cat in config['categories']:
                    category_table_path = f"{parameter_settings_path}/optimal_F1s_{cat}_parameters_merged.csv"
                    logger_experiment.info(f"Read parameter settings for {cat} from {category_table_path}.")
                    experiment_configs = output_configs(config, category_table_path, cat[-1], mode, metrics_list, only_best=config['only_best_parameter_sets'])

                    for experiment_config in experiment_configs:
                        if experiment_config['parameters']['modify_category'+cat[-1]]['modification'] in config['modifications']:

                            logger_experiment.info(f"Running category modification experiment... \n\t\tFor metric: {experiment_config['parameters']['modify_'+cat]['metric']}\n\t\tFor f_type: {experiment_config['parameters']['modify_'+cat]['f_type']}\n\t\tFor modification: {experiment_config['parameters']['modify_'+cat]['modification']}\n\t\tResources path: {experiment_config['paths']['resources_path']}\n\t\tData path:  {experiment_config['paths']['data_path']}\n\t\tFor following corpora: {experiment_config['corpora']}")

                            # here the experiment is ran for all noise types listed in the config file
                            main(experiment_config, gpu)
                            logger_experiment.info(f"Finished experiment")
            else:
                logger_experiment.info(f"Running combined experiment... \n\t\tFor mode: {mode}")
        
                combined_config = get_combined_config(config, mode)
                main(combined_config, gpu)

    categories_ids = [cat[-1] for cat in config['categories']]
    if not config['only_best_parameter_sets']:
        for corpus_name in corpora:
            logger_experiment.info(f"Summarizing test scores for {corpus_name}")

            # 4. Summarize the test scores from 3. 
            summarize_test_scores(config['paths']['results_tables_path'], source_corpus, corpus_name, resources_path=config['paths']['resources_path'], categories_ids = categories_ids)

            # 5. Merge the optimal parameter sets from 2. and the summarized test scores from 4.
            merge_tables(f"{config['paths']['results_tables_path']}", source_corpus, corpus_name, config['parameters']['modes'], categories_ids = categories_ids, merged_parameters = True)
            merge_tables(f"{config['paths']['results_tables_path']}", source_corpus, corpus_name, config['parameters']['modes'], categories_ids = categories_ids, merged_parameters = False)

    logger_experiment.info(f"Calculating correlations between the optimal parameter sets and the summarized test scores.")
    calculate_correlations(config) # here we use the table from 5., but the one without merged parameters.

    if config['only_best_parameter_sets']:
        logger_experiment.info(f"Summarizing all final scores and baselines.")
        summarize_test_scores_and_baselines(config)
        logger_experiment.info(f"Saving tables to latex.")

        # save parameter tables to latex
        save_parameter_tables_to_latex(config['paths']['results_tables_path'], source_corpus, config['parameters']['modes'], categories_ids)
        save_noise_shares_to_latex(config)
    logger_experiment.info(f"Finished all experiments and summarized scores.")

if __name__ == "__main__":
    argParser = argparse.ArgumentParser()

    argParser.add_argument("-c", "--config", help="filename with experiment configuration")
    argParser.add_argument("-g", "--gpu", help="set gpu id", default=0)

    args = argParser.parse_args()

    with open(args.config) as json_file:
        config = json.load(json_file)

    config['config_filepath'] = os.path.splitext(json_file.name)[0].split(os.sep)[-1]

    run(config, args.gpu)
