import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import csv
from optimize_metric_parameters import *
import logging

logger_experiment = logging.getLogger(__name__)
logger_experiment.setLevel(level="INFO")


CATEGORIES = [
    { 
        'id':'1',
        'name':'Category: Learned - O',
        'axes_indices':(0,0),
        'correct_prediction_flag':True,
        'observed_label':'O',
        'color':'#55a868'
    },
    {
        'id': '2',
        'name': 'Category: Misclassified - O',
        'axes_indices':(0,1),
        'correct_prediction_flag':False,
        'observed_label':'O',
        'color':'#c44e52'
    },                
    {
        'id':'3',
        'name':'Category: Learned - entity',
        'axes_indices':(1,0),
        'correct_prediction_flag':True,
        'observed_label':'non-O',
        'color':'#55a868'
    },

    {
        'id':'4',
        'name':'Category: Misclassified - entity',
        'axes_indices':(1,1),
        'correct_prediction_flag':False,
        'observed_label':'non-O',
        'color':'#c44e52'
    }
]

CORRECT_PREDICTION_FLAG_NAME = 'correct_prediction_flag'
NOISE_FLAG_NAME = 'noisy_flag'

latex_name_dictionary = {
    'noise_crowd':'Crowd',
    'noise_weak':'Weak',
    'noise_distant':'Distant',
    'noise_expert':'Expert',
    'noise_crowdbest':'Crowd++',
    'noise_llm':'LLM',
    'cat1_mask':'Category 1 - Mask',
    'cat2_mask':'Category 2 - Mask',
    'cat3_mask':'Category 3 - Mask',
    'cat4_mask':'Category 4 - Mask',
    'cat2_relabel':'Category 2 - Relabel',
    'cat4_relabel':'Category 4 - Relabel',
    'cat1':'Learned-\\textit{O}',
    'cat2':'Misclassified-\\textit{O}',
    'cat3':'Learned-\\textit{entity}',
    'cat4':'Misclassified-\\textit{entity}',
      "estner_noisy_labelset1":"NoisyNER 1",
    "estner_noisy_labelset2":"NoisyNER 2",
    "estner_noisy_labelset3":"NoisyNER 3",
    "estner_noisy_labelset4":"NoisyNER 4",
    "estner_noisy_labelset5":"NoisyNER 5",
    "estner_noisy_labelset6":"NoisyNER 6",
    "estner_noisy_labelset7":"NoisyNER 7",
    'german_noise_llm':'Ger. LLM',
    'german_noise_expert':'Ger. Expert',
    "cross_entropy":"cross-entropy",
    "msp":"MSP",
    "pehist":'entropy-history',
    "iter_norm":'iteration-learned',
    "tal":'agreement-predicted',
    "pd":"prediction-depth",
    "tac":"agreement-true",
    "le":"layer-entropy",
    "fl":'first-layer',
    "mild":'MILD'
}

noise_shares_dict = {
    'noise_crowd':'36.6',
    'noise_weak':'40.4',
    'noise_distant':'31.3',
    'noise_expert':'5.5',
    'noise_crowdbest':'15.3',
    'noise_llm':'45.6',
    "estner_noisy_labelset1":"72.0",
    "estner_noisy_labelset2":"61.0",
    "estner_noisy_labelset3":"66.0",
    "estner_noisy_labelset4":"60.0",
    "estner_noisy_labelset5":"56.0",
    "estner_noisy_labelset6":"54.0",
    "estner_noisy_labelset7":"46.0",
    'german_noise_llm':'54.0',
    'german_noise_expert':'16.2',
}

metrics_order = { 'standard':['cross-entropy','MSP','BvSB','entropy','confidence','variability','correctness','iteration-learned', 'MILD','entropy-history'],
                 'EE': ['prediction-depth','first-layer','agreement-predicted','agreement-true','layer-entropy' ] }


def summarize_test_scores(results_tables_path, source_corpus, corpus_name, resources_path,   categories_ids, merged_parameters = True):
    '''
    Example path to test scores: source_noise_crowd_target_noise_llm/test_scores
    '''
    dataset = corpus_name

    for category_id in categories_ids:
        
        test_scores_tables_path = f"{results_tables_path}/source_{source_corpus}_target_{corpus_name}/test_scores"

        if not os.path.exists(test_scores_tables_path):
            os.makedirs(test_scores_tables_path)

        optimize_F1s_output = open(test_scores_tables_path + os.sep+'category'+category_id+'_test_scores.csv','w')

        optimize_F1s_output.write('metric, f_score, modification, noise share, test score, std test score \n')

        experiment_path = f"{resources_path}/relabel_cat{category_id}_source_{source_corpus}/category{category_id}"

        for metric in os.listdir(experiment_path):
            # this includes both standard and EE metrics
            for f_type in os.listdir(os.path.join(experiment_path,metric)):
                for modif in os.listdir(os.path.join(experiment_path, metric, f_type)):

                    results_path = os.path.join(experiment_path, metric, f_type, modif)
                    if os.path.exists(results_path+os.sep+dataset):

                        logger_experiment.debug(results_path)
                        noise_shares = []
                    
                        for seed_path in os.listdir(results_path+os.sep+dataset):
                            fname = os.path.join(results_path, dataset, seed_path, 'noise_f1.txt')
                            if os.path.isfile(fname):
                                with open(fname) as f:
                                    lines = f.read().strip().split('\n')
                                    noise = lines[0].split(' ')[0]
                                    noise_shares.append(float(noise))
                                    logger_experiment.debug(noise_shares)
                        test_results_fname = results_path+os.sep+dataset+os.sep+'test_results.tsv'
                        if os.path.isfile(test_results_fname):
                            results_df = pd.read_csv(test_results_fname, delimiter='\t', header=0)
                            logger_experiment.debug(results_df[['mean']].values)
                            score = float(results_df[['mean']].values[0])
                            stdev = float(results_df[['std']].values[0])
                            optimize_F1s_output.write(f"{'_'.join(metric.split('_')[1:])}, {f_type}, {modif}, {np.mean(noise_shares):.3f}, {round(score, 4):.3f}, {round(stdev, 4):.3f}\n")
                            #optimize_F1s_output.write(f"{metric.split('_')[1:]}, {f_type}, {modif}, {0}, {0}\n")


def merge_tables(results_tables_path, source_corpus, corpus_name, modes, categories_ids, merged_parameters = True):

    for category in categories_ids:

        test_scores_df = pd.read_csv(f"{results_tables_path}/source_{source_corpus}_target_{corpus_name}/test_scores/category{category}_test_scores.csv",header = 0, index_col=[0,1,2], delimiter = ', ')
        test_scores_df.columns = [c.strip() for c in test_scores_df.columns]
        test_scores_df.index.names = [c.strip() for c in test_scores_df.index.names]

        if not merged_parameters:
            test_scores_df.reset_index(inplace=True, names=['metric','f_score', 'modification'])
            test_scores_df['f_score'] = test_scores_df['f_score'].apply(lambda x: [i.strip() for i in x.split('_')])
            test_scores_df = test_scores_df.explode('f_score')
            test_scores_df.set_index(['metric','f_score', 'modification'], inplace=True)

        full_data = None
        for mode in modes:
            parameter_settings_tables_path = f"{results_tables_path}/{source_corpus}/{mode}_mode"

            if not merged_parameters:
                parameter_settings_df = pd.read_csv(f"{parameter_settings_tables_path}/optimal_F1s_category{category}.csv",header = 0, index_col=[0,1], delimiter = ', ')
            else:
                print(f"{parameter_settings_tables_path}/optimal_F1s_category{category}_parameters_merged.csv")
                parameter_settings_df = pd.read_csv(f"{parameter_settings_tables_path}/optimal_F1s_category{category}_parameters_merged.csv",header = 0, index_col=[0,1],  delimiter = ', ')
            parameter_settings_df.columns = [c.strip() for c in parameter_settings_df.columns]
            parameter_settings_df.index.names = [c.strip() for c in parameter_settings_df.index.names]

            logger_experiment.debug(parameter_settings_df)

            if full_data is None:
                full_data = pd.merge(parameter_settings_df, test_scores_df, left_index=True, right_index=True)
            else:
                full_data = pd.concat([full_data, pd.merge(parameter_settings_df, test_scores_df, left_index=True, right_index=True)], axis = 0)

        final_tables_path = f"{results_tables_path}/source_{source_corpus}_target_{corpus_name}/final_tables"

        if not os.path.exists(final_tables_path):
            os.makedirs(final_tables_path)
        
        if merged_parameters:
            full_data.to_csv(final_tables_path+os.sep+f'category{category}_final_table_parameters_merged.csv',index=True,header=True, float_format='%.3f')
        else:
            full_data.to_csv(final_tables_path+os.sep+f'category{category}_final_table.csv',index=True,header=True, float_format='%.3f')



def plot_metric_distributions(base_path, seeds, mode, sample_metrics, dset = 'train', y_limit=2000, max_epochs=11):
    '''
    Plots the distribution of PD values, for different categories of samples and for each epoch.

    For only one seed, the first one (100).
    Categories:

    1. correct prediction and predicted label is O 
    2. correct prediction and predicted label is not O 
    3. incorrect prediction and predicted label is O 
    4. incorrect prediction and predicted label is not O 

    The four categories are shown side by side. 

    The histograms for noisy and clean samples are shown in different colors.

    '''

    if mode == 'EE':
        exp_paths = [f'{seed}_with_init-0.3/' for seed in seeds]
    else:
        exp_paths = [f'{seed}/' for seed in seeds]

    plt.legend(markerscale=2)

    filepath = base_path + exp_paths[0]+'epoch_log'+'_0.log'

    if not os.path.exists(filepath):
        start_index = 1
    else:
        start_index = 0

    if dset == 'train':
        ext = ''
    else:
        ext = '_'+dset
    
    for metric in sample_metrics:
        for i in [str(i) for i in range(start_index, max_epochs)]:

            for exp_path in exp_paths: 
                ## plot metric distribution for only one seed
                
                path = base_path + exp_path

                filepath = path+'epoch_log'+'_'+i+ext+'.log'
                logger_experiment.debug(filepath)
                if not os.path.exists(path+'histograms_'+metric):
                    os.mkdir(path+'histograms_'+metric)

                try:
                    df = pd.read_csv(filepath,  delimiter='\t', header=0, quoting=csv.QUOTE_NONE)
                except:
                    continue
                logger_experiment.debug(df.columns)
                df[CORRECT_PREDICTION_FLAG_NAME] = df['predicted'] == df['noisy']

                logger_experiment.debug(df.groupby(NOISE_FLAG_NAME).count())
                logger_experiment.debug(len(df))
                if metric in ['pd','fl','tal','tac','mild','mild_f','mild_m']:
                    max_metric = df[metric].max()
                    binwidth = 1
                    if metric == 'mild':
                        binrange = (-max_metric, max_metric)
                        logger_experiment.debug(binrange)
                    else:
                        binrange = (0, max_metric)
                elif metric in ['le','cross_entropy','entropy','pehist']:
                    max_metric = 8
                    binwidth = 0.1
                    binrange = (0,max_metric)
                elif metric in ['variability']:
                    max_metric = 0.5
                    binwidth = 0.05
                    binrange = (0,max_metric)
                else:
                    max_metric = df[metric].max()
                    binwidth = 0.1
                    binrange = (0,1)

                df.rename(columns = {'noisy':'label'}, inplace=True)

                fig, axes = plt.subplots(2, 2, figsize=(9.5, 3.5))
                for category in CATEGORIES:
                    #cat1
                    if category['observed_label'] == 'O':
                        dataset = df[(df[CORRECT_PREDICTION_FLAG_NAME] == category['correct_prediction_flag']) & (df['label'] == 'O') ]
                    else:
                        dataset = df[(df[CORRECT_PREDICTION_FLAG_NAME] == category['correct_prediction_flag']) & (df['label'] != 'O') ]
                    num_samples = len(dataset)
                    num_samples_selected = len(dataset[dataset[metric]<0.4])

                    sns.histplot( dataset, x=metric, binwidth=binwidth, binrange=binrange, ax = axes[category['axes_indices']], hue= NOISE_FLAG_NAME)
                    # axes[category['axes_indices']].set_title('Epoch '+i+' - '+category['name']+'_'+str(num_samples)+'_'+str(num_samples_selected))

                    axes[category['axes_indices']].legend(title='Token label', loc='upper right', labels=['clean', 'noisy'])
                    axes[category['axes_indices']].set_ylabel('count', fontsize=12)
                    axes[category['axes_indices']].set_xlabel(metric, fontsize=12)


                    axes[category['axes_indices']].set_title(category['name'], fontsize=14)
                    axes[category['axes_indices']].set_xlim(binrange)
                    axes[category['axes_indices']].set_ylim((0,y_limit))

                fig.tight_layout() 
                fig.savefig(path+'histograms_'+metric+os.sep+metric+'_distribution_epoch_'+i+ext+'.png', dpi=300)
                plt.close()
                
                fig = plt.figure(figsize=(7.5, 2.25))

                ax= sns.histplot( df, x=metric, binwidth=binwidth, binrange=binrange, hue= NOISE_FLAG_NAME)
                # ax.set_title('Epoch '+i+' - all categories')
                ax.legend(title='Token label', loc='upper right', labels=['clean', 'noisy'])
                ax.set_ylabel('count', fontsize=10)
                ax.set_xlabel(metric, fontsize=10)

                ax.set_xlim(binrange)
                ax.set_ylim((0,4*y_limit))

                fig.tight_layout() 
                fig.savefig(path+'histograms_'+metric+os.sep+metric+'_distribution_epoch_'+i+ext+'_all_tokens.png', dpi=300)
                plt.close()


def plot_category_membership_through_epochs(base_paths, corpus_name, seeds, dset = 'train', max_epochs=11):

    exp_paths={}
    exp_paths['EE'] = [f'{seed}_with_init-0.3/' for seed in seeds]
    exp_paths['standard'] = [f'{seed}/' for seed in seeds]

    plt.legend(markerscale=2)
    plt.style.use('seaborn-v0_8-whitegrid')
    seaborn_blue = '#4C72B0'
    seaborn_orange = '#DD8452'
    categories_counts = {}

    for mode in ['EE','standard']:
        logger_experiment.debug(mode)
        filepath = f"{base_paths[mode]}/{corpus_name}/{exp_paths[mode][0]}/epoch_log_0.log"

        if not os.path.exists(filepath):
            start_index = 1
        else:
            start_index = 0

        if dset == 'train':
            ext = ''
        else:
            ext = '_'+dset

        # for each mode, category and sample type (clean or noisy): save a list with sample counts from all epochs
        categories_counts[mode] = {cat['id']: {'clean':[], 'noisy':[]} for cat in CATEGORIES} 

        for i in [str(i) for i in range(start_index, max_epochs)]:

            for exp_path in exp_paths[mode][:1]: 
                ## temporary: plot for only one seed - the first one
                
                path = f"{base_paths[mode]}/{corpus_name}/{exp_paths[mode][0]}/"
                filepath = path+'epoch_log'+'_'+i+ext+'.log'
                epoch_log_df = pd.read_csv(filepath,  delimiter='\t', header=0, quoting=csv.QUOTE_NONE)

                epoch_log_df[CORRECT_PREDICTION_FLAG_NAME] = epoch_log_df['predicted'] == epoch_log_df['noisy']

                for category in CATEGORIES:
                    category_id = category['id']

                    # take the corresponding data subset
                    if category['observed_label'] == 'O':
                        category_epoch_log_df = epoch_log_df[(epoch_log_df[CORRECT_PREDICTION_FLAG_NAME] == category['correct_prediction_flag'])  & (epoch_log_df['noisy']=='O')]
                    else:
                        category_epoch_log_df = epoch_log_df[(epoch_log_df[CORRECT_PREDICTION_FLAG_NAME] == category['correct_prediction_flag'])  & (epoch_log_df['noisy']!='O')]

                    # count clean and noisy samples
                    count_clean, count_noisy = len(category_epoch_log_df[NOISE_FLAG_NAME].values) - sum(category_epoch_log_df[NOISE_FLAG_NAME].values), sum(category_epoch_log_df[NOISE_FLAG_NAME].values)

                    categories_counts[mode][category_id]['clean'].append(count_clean)
                    categories_counts[mode][category_id]['noisy'].append(count_noisy)            

    # Plot line plots with number of samples per-category. 
    # y-axis: number of samples
    # x-acis: epoch number
    # dashed lines: EE runs
    # solid lines: standard runs
    # clean and noisy samples are in different colors
    fig, axes = plt.subplots(2, 2, figsize=(14, 6))
    for category in CATEGORIES:
        category_id = category['id']
        axes[category['axes_indices']].plot(categories_counts['EE'][category_id]['clean'], color=seaborn_blue, linestyle='dashed', marker='o', label= 'EE: clean')
        axes[category['axes_indices']].plot(categories_counts['EE'][category_id]['noisy'], color=seaborn_orange, linestyle='dashed', marker='o',label= 'EE: noisy')
        axes[category['axes_indices']].plot(categories_counts['standard'][category_id]['clean'], color=seaborn_blue, linestyle='solid', marker='o', label= 'standard: clean')
        axes[category['axes_indices']].plot(categories_counts['standard'][category_id]['noisy'], color=seaborn_orange, linestyle='solid', marker='o', label= 'standard: noisy')
        axes[category['axes_indices']].set_title(category['name'])
    axes[1,1].legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(f"{base_paths['standard']}/{corpus_name}/lineplots_category_memberships_{corpus_name}.png")
    plt.close()


    # Plot barplots of numbers of samples; per (observed) label type
    # 4 stacked barplots (stack number of samples in pairs of complementary categories)
    # (observed label O is left: category 1 or 2)
    # (observed label non-O is right: category 3 or 4)
    # x-axis: epoch number
    # training modes (standard or EE) have bars with different opacity
    # categories have different colors

    fig, axes = plt.subplots(2, 2, figsize=(14, 6))
    width = 0.25

    bottom = np.zeros(10)
    for cat1 in [CATEGORIES[0], CATEGORIES[1]]:
        
        p = axes[0,0].bar(x= range(10), height = categories_counts['standard'][cat1['id']]['clean'][:10] , width=width, color=cat1['color'], label='standard: category'+cat1['id'], bottom=bottom)
        bottom += categories_counts['standard'][cat1['id']]['clean'][:10] 
    bottom = np.zeros(10)
    for cat1 in [CATEGORIES[0], CATEGORIES[1]]:
        p = axes[0,0].bar(x= [c + width for c in range(10)], height = categories_counts['EE'][cat1['id']]['clean'][:10] , width=width, color=cat1['color'], label='EE: category'+cat1['id'], alpha=0.5, bottom=bottom)
        bottom += categories_counts['EE'][cat1['id']]['clean'][:10] 
    axes[0,0].set_title('Category 1/2 (Observed label O) - clean')

    bottom = np.zeros(10)
    for cat1 in [CATEGORIES[0], CATEGORIES[1]]:
        p = axes[1,0].bar(x= range(10), height = categories_counts['standard'][cat1['id']]['noisy'][:10] , width=width, color=cat1['color'], label='standard: category'+cat1['id'], bottom=bottom)
        bottom += categories_counts['standard'][cat1['id']]['noisy'][:10] 
    bottom = np.zeros(10)
    for cat1 in [CATEGORIES[0], CATEGORIES[1]]:
        p = axes[1,0].bar(x= [c + width for c in range(10)], height = categories_counts['EE'][cat1['id']]['noisy'][:10]  , width=width, color=cat1['color'], label='EE: category'+cat1['id'], alpha=0.5, bottom=bottom)
        bottom += categories_counts['EE'][cat1['id']]['noisy'][:10] 
    axes[1,0].set_title('Category 1/2 (Observed label O) - noisy')


    bottom = np.zeros(10)
    for cat1 in [CATEGORIES[2], CATEGORIES[3]]:
        p = axes[0,1].bar(x= range(10), height = categories_counts['standard'][cat1['id']]['clean'][:10] ,   width=width, color=cat1['color'], label='standard: category'+cat1['id'], bottom=bottom)
        bottom += categories_counts['standard'][cat1['id']]['clean'][:10] 
    bottom = np.zeros(10)
    for cat1 in [CATEGORIES[2], CATEGORIES[3]]:
        p = axes[0,1].bar(x= [c + width for c in range(10)], height = categories_counts['EE'][cat1['id']]['clean'][:10],   width=width, color=cat1['color'], label='EE: category'+cat1['id'], alpha=0.5, bottom=bottom)
        bottom += categories_counts['EE'][cat1['id']]['clean'][:10] 
    axes[0,1].set_title('Category 3/4 (Observed label non-O) - clean')


    bottom = np.zeros(10)
    for cat1 in [CATEGORIES[2], CATEGORIES[3]]:
        p = axes[1,1].bar(x= range(10), height = categories_counts['standard'][cat1['id']]['noisy'][:10] ,   width=width, color=cat1['color'], label='standard: category'+cat1['id'], bottom=bottom)
        bottom += categories_counts['standard'][cat1['id']]['noisy'][:10] 
    bottom = np.zeros(10)
    for cat1 in [CATEGORIES[2], CATEGORIES[3]]:
        p = axes[1,1].bar(x= [c + width for c in range(10)], height = categories_counts['EE'][cat1['id']]['noisy'][:10] ,   width=width, color=cat1['color'], label='EE: category'+cat1['id'], alpha=0.5, bottom=bottom)
        bottom += categories_counts['EE'][cat1['id']]['noisy'][:10] 
    axes[1,1].set_title('Category 3/4 (Observed label non-O) - noisy')

    handles, labels = axes[1,1].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', ncol = 4, bbox_to_anchor = (0.5, 0),)
    fig.savefig(f"{base_paths['standard']}/{corpus_name}/lineplots_category_memberships_version2_{corpus_name}.png")
    plt.close()

def plot_train_score_percentage_through_epochs(base_paths,modes, source_corpora, seeds, dset = 'train', max_epochs=11, percentage = False):
    # enable either percentages or absolute values.
    # maybe through is a bad preposition

    # set percentage to True if you want to plot %max train instead of absolute train f scores

    for mode in modes:
        print(mode)
        sns.set_style("whitegrid")

        fig, ax = plt.subplots(1, 1)
        df = None
        for corpus in source_corpora:
            for seed in seeds:
                if mode == 'EE':
                    exp_path = f'{seed}_with_init-0.3'
                else:
                    exp_path = f'{seed}'

                filepath = f"{base_paths[mode]}/{corpus}/{exp_path}/loss.tsv"
                print(filepath)
                if df is None:
                    df = pd.read_csv(filepath, index_col=None, header=0, delimiter='\t')
                    df['corpus'] = corpus
                else:
                    current_df = pd.read_csv(filepath, index_col=None, header=0, delimiter='\t')
                    current_df['corpus'] = corpus
                    df = pd.concat([df, current_df], axis=0)
            if percentage == True:
                temp_df = df[df['corpus']==corpus]
                max_train = temp_df[temp_df['EPOCH'] == 10]
                max_train = max_train['TRAIN_SAMPLE_F1'].mean()
                print(max_train)
                temp_df['TRAIN_SAMPLE_F1'] = temp_df['TRAIN_SAMPLE_F1']/max_train
                df[df['corpus']==corpus] = temp_df

        print(df)
        sns.lineplot(df, x='EPOCH', y='TRAIN_SAMPLE_F1', hue='corpus', ax=ax, marker='o')
        fig.tight_layout()
        fig.savefig(f"{base_paths[mode]}/train_score_percentage_through_epochs.png")
        # fix path


def summarize_test_scores_and_baselines(config):
    source_corpora = config['source_corpora']
    source_corpus = '_'.join(source_corpora)

    corpora = config['corpora']
    if len(corpora) == 15:
        target_corpus = 'all'
    else:
        target_corpus = '_'.join(corpora)
    # we can set the only_results_summarization to true if we only want to re-generate the summary tables, and not re-run the experiments
    for mode in config['parameters']['modes']:
        
        # open new file
        results_file_path = f"{config['paths']['results_tables_path']}/{source_corpus}/{mode}_mode"
        file = open(results_file_path + os.sep + 'all_summarized_scores_' + target_corpus + '.csv','w')
        file.write(', ,'+','.join([latex_name_dictionary[corpus] for corpus in corpora])+'\n')
        # write baselines
        file.write('Baseline, ')

        num_columns = len(corpora)

        for corpus in corpora:

            test_results_fname = f"{config['paths']['baseline_paths'][mode]}/{corpus}/test_results.tsv"

            if os.path.isfile(test_results_fname):
                results_df = pd.read_csv(test_results_fname, delimiter='\t', header=0)
                logger_experiment.debug(results_df[['mean']].values)
                score = float(results_df[['mean']].values[0])*100
                stdev = float(results_df[['std']].values[0])*100
            else:
                score = 0
                stdev = 0
            file.write(f",{score:.2f}\\small$\pm${stdev:.2f}")
        file.write('\n')


        # write category experiment scores
        for cat in config['categories']:
            resources_path = f"{config['paths']['resources_path']}/relabel_cat{cat[-1]}_source_{source_corpus}/category{cat[-1]}/"
            for p in os.listdir(resources_path):
                if mode in p:
                    mode_metric = p
            
            print(resources_path)
            print(mode_metric)
            for p in os.listdir(os.path.join(resources_path,mode_metric)):
                f_type = p

            modifications = []

            for p in os.listdir(os.path.join(resources_path,mode_metric,f_type)):
                modifications.append(p)

            #logger_experiment.info(f"Read parameter settings for {cat} from {category_table_path}.")
            
            resources_path = os.path.join(resources_path,mode_metric,f_type)
            
            for modif in modifications:
                file.write(latex_name_dictionary[f'cat{cat[-1]}']+', '+modif)

                for corpus in corpora:
                    test_results_fname = f"{resources_path}/{modif}/{corpus}/test_results.tsv"

                    if os.path.isfile(test_results_fname):
                        results_df = pd.read_csv(test_results_fname, delimiter='\t', header=0)
                        logger_experiment.debug(results_df[['mean']].values)
                        score = float(results_df[['mean']].values[0])*100
                        stdev = float(results_df[['std']].values[0])*100
                    else:
                        score = 0
                        stdev = 0         
                    file.write(f",{score:.1f}\\small$\pm${stdev:.1f}")
                file.write('\n')
        
        combined_resources_path = f"{config['paths']['resources_path']}/relabel_combined_source_{source_corpus}/category124/"
        for p in os.listdir(combined_resources_path):
            if mode in p:
                mode_metric = p
        
        for p in os.listdir(os.path.join(combined_resources_path,mode_metric)):
            f_type = p

        modifications = []

        for p in os.listdir(os.path.join(combined_resources_path,mode_metric,f_type)):
            modifications.append(p)


        resources_path = os.path.join(combined_resources_path,mode_metric,f_type)

        file.write('Combined, ')
        for modif in modifications:
            for corpus in corpora:
                test_results_fname = f"{resources_path}/{modif}/{corpus}/test_results.tsv"

                if os.path.isfile(test_results_fname):
                    results_df = pd.read_csv(test_results_fname, delimiter='\t', header=0)
                    logger_experiment.debug(results_df[['mean']].values)
                    score = float(results_df[['mean']].values[0])*100
                    stdev = float(results_df[['std']].values[0])*100
                else:
                    score = 0
                    stdev = 0         
                file.write(f",{score:.1f}\\small$\pm${stdev:.1f}")
            file.write('\n')

        file.close()
        resultsdf = pd.read_csv(results_file_path + os.sep + 'all_summarized_scores_' + target_corpus + '.csv', header=0, index_col=[0,1], delimiter=',')

        def extract_mean(val):
            return float(val.split('\\small$\\pm$')[0])

        df_means = resultsdf.applymap(extract_mean)

        # Step 2: Bold the full string with highest mean per row
        def bold_max_in_row(row, original_row):
            print(row.astype(float))
            max_idx = row.astype(float).idxmax()
            original_row[max_idx] = f"\\textbf{{{original_row[max_idx]}}}"
            return original_row

        df_bolded = df_means.combine(resultsdf, bold_max_in_row)

        df_bolded.T.to_latex(results_file_path + os.sep + 'all_summarized_scores_' + target_corpus + '.tex', index=True, multicolumn=True,multicolumn_format='c', escape=False, column_format='llccccccc', label='', caption='')

def mixed_formatter(x):
    if isinstance(x, int) or x == int(x):
        return f"{int(x)}"
    else:
        return f"{x:.1f}"

def f_score_formatter(label):
    label=label.strip()
    parts = label.split('_')
    formatted_parts = []
    for part in parts:
        print(part)
        if part.startswith('f'):
            # Convert 'f05' → '0.5', 'f1' → '1'
            num_str = part[1:]
            if len(num_str) == 2 and num_str.startswith('0'):
                value = f"0.{num_str[1]}"
            else:
                value = num_str
            formatted_parts.append(fr"\normalsize{{F}}\scriptsize{{{value}}}")
        else:
            formatted_parts.append(part)
    return '\\normalsize{/}'.join(formatted_parts)
    
def format_threshold(row):
    if row['direction'].strip() == 'left':
        char = '<'
    else:
        char='>'
    threshold = mixed_formatter(row['threshold'])

    return char+' '+threshold

def get_oracle_f_scores(results_tables_path, category, mode, source_corpus):
    corpora = source_corpus.split('noise_')
    print(corpora)
    corpora = ['noise_'+corpus.strip('_') for corpus in corpora if corpus != '']
    print(corpora)
    full_data = None
    for corpus in corpora:
        table_path = f"{results_tables_path}/{corpus}/{mode}_mode"
        data = pd.read_csv(f"{table_path}/optimal_F1s_category{category}.csv", header = 0, index_col=[0,1])
        data.columns = [c.strip() for c in data.columns]
        data.index = data.index.set_levels(
            data.index.levels[1].map(lambda x: x.strip()), level=1
        )
        data.drop(index='f1', level=1, inplace=True)
        data.drop(index='f2', level=1, inplace=True)

        data.index = data.index.set_levels(
            data.index.levels[1].map(lambda x: f_score_formatter(x)), level=1
        )
        data.index = data.index.set_levels(
        data.index.levels[0].map(lambda x: latex_name_dictionary.get(x, x)), level=0
        )
        data.index.names = [ x.replace('_','\_') for x in data.index.names]
        data.drop(columns='threshold', inplace=True)
        data.drop(columns='direction', inplace=True)
        data.drop(columns='epoch', inplace=True)
        print(data.index.names)
        data = data.droplevel(' f\_score')
        data.index = pd.CategoricalIndex(data.index, categories = metrics_order[mode], ordered=True)
        data = data.sort_index(level=0)
        if full_data is None:
            full_data = data
        else:
            full_data['score'] = full_data['score'] + data['score']
    full_data['score'] = full_data['score'] / len(corpora)
    full_data.rename(columns={'score':'oracle score'}, inplace=True)

    return full_data

def save_parameter_tables_to_latex(results_tables_path, source_corpus, modes, categories_ids):
    large_table = None
    formatters = {'score':lambda x: f"{x:.2f}"}

    for category in categories_ids:
        for mode in modes:

            table_path = f"{results_tables_path}/{source_corpus}/{mode}_mode"

            data_oracle = get_oracle_f_scores(results_tables_path, category, mode, source_corpus)
            print(data_oracle)
            data = pd.read_csv(f"{table_path}/optimal_F1s_category{category}.csv", header = 0, index_col=[0,1])
            data.columns = [c.strip() for c in data.columns]
            print(data.columns)
            print(data.index.names)
            print(data.index)
            data.index = data.index.set_levels(
                data.index.levels[1].map(lambda x: x.strip()), level=1
            )
            data.drop(index='f1', level=1, inplace=True)
            data.drop(index='f2', level=1, inplace=True)

            data.index = data.index.set_levels(
                data.index.levels[1].map(lambda x: f_score_formatter(x)), level=1
            )
            data.index = data.index.set_levels(
            data.index.levels[0].map(lambda x: latex_name_dictionary.get(x, x)), level=0
            )
            data.index.names = [ x.replace('_','\_') for x in data.index.names]
            data['new_threshold'] = data.apply(lambda row: format_threshold(row), axis=1)
            data.drop(columns=['threshold', 'direction'], inplace=True)
            data.rename(columns={'new_threshold':'threshold'}, inplace=True)
            print(data.index.names)
            data = data.droplevel(' f\_score')
            data.index = pd.CategoricalIndex(data.index, categories = metrics_order[mode], ordered=True)
            data = data.sort_index(level=0)

            data= pd.concat([data, data_oracle], axis=1)
            print(data)
            if mode == 'standard':
                full_data = data

        full_data = pd.concat([full_data, data], axis=0)
        full_data = full_data.reset_index()
        full_data.drop(columns=['threshold','epoch'], inplace=True)
        full_data.to_latex(f'{table_path}/optimal_F1s_category{category}.tex', index=False,escape=False,column_format='llcccc', label='', caption='',formatters = formatters )
        full_data.set_index('metric', inplace=True)
        if large_table is None:
            large_table = full_data
        else:
            large_table = pd.concat([large_table, full_data], axis=1)
            
    
    print(large_table)
    col_level = []
    for cat in categories_ids:
        col_level.extend(['Category '+str(cat)] * 2)
    large_table.columns = [large_table.columns, col_level]
    large_table = large_table.swaplevel(axis=1)
    large_table = large_table.reset_index()
    print(large_table)
    print(large_table.columns[1:])
    large_table.style.highlight_max(axis=0, props='textbf:--rwrap', subset=large_table.columns[1:]).format(precision = 3).hide(axis="index").to_latex(f'{table_path}/optimal_F1s_large_table.tex', multicol_align='c',column_format='lcccccccc', label='', caption='' )

def get_mismatch(original_data, relabeled_data, masked_data):
    # read the data
    relabeled_data = pd.read_csv(relabeled_data, delimiter='\t', header=None,index_col=None, quoting=csv.QUOTE_NONE, names=['token','label'])
    masked_data = pd.read_csv(masked_data, delimiter='\t', header=None, index_col=None,quoting=csv.QUOTE_NONE, names=['token','label'])
    original_data = pd.read_csv(original_data, delimiter='\t', header=None,index_col=None, quoting=csv.QUOTE_NONE, names=['token','label_clean','label'])

    # get the labels
    original_labels = original_data['label'].values
    relabeled_labels = relabeled_data['label'].values
    masked_labels = masked_data['label'].values
    clean_labels = original_data['label_clean'].values


    print(len(original_labels))
    print(len(relabeled_data))
    print(len(masked_labels))
    print(len(clean_labels))
    print('\n')
    print(sum(original_labels != clean_labels))
    print('\n')


    print('the next two should mostly be zeros')
    print(original_data[original_labels != relabeled_labels])
    print(original_data[masked_labels == 'B-MASK'])
    print(sum(masked_labels == 'B-MASK'))
    print(sum(original_labels != relabeled_labels))
    # if np.abs(sum(masked_labels == 'B-MASK') - sum(original_labels != relabeled_labels)) < 0.15*sum(original_labels != relabeled_labels):
    #     print('next')
    # else:
    #     input()

    # print(relabeled_data[original_labels != relabeled_labels])
    # print(masked_data[original_labels != relabeled_labels])

    # get the mismatches
    mismatch_original = original_labels[masked_labels == 'B-MASK']
    mismatch_relabel = relabeled_labels[masked_labels == 'B-MASK']
    mismatch_masked = masked_labels[masked_labels == 'B-MASK']
    mismatch_clean = clean_labels[masked_labels == 'B-MASK']

    return mismatch_original, mismatch_relabel, mismatch_masked, mismatch_clean

def count_errors(original_data, relabeled_data, masked_data):
    # get 4 lists (numpy arrays) of token labels, only for the tokens where relabelled/masked != original
    # or just identify the tokens where masked = B-MASK
    mismatch_original, mismatch_relabel, mismatch_masked, mismatch_clean = get_mismatch(original_data, relabeled_data, masked_data) 

    count_bad_masked = sum(mismatch_original == mismatch_clean) # counnt how many of the masked, are actually correct (inaccurate masking)
    count_good_masked = sum(mismatch_original != mismatch_clean) # counnt how many of the masked, are incorrect (accurate masking)

    count_bad_relabel = sum((mismatch_clean != mismatch_relabel) & (mismatch_original != mismatch_clean)) # count how many of the relabeled, are relabelled into a bad class (inaccurate relabeling)
    count_good_relabel = sum((mismatch_clean == mismatch_relabel) & (mismatch_original != mismatch_clean)) # count how many of the relabeled, are relabelled into a good class (accurate relabeling)

    ''' 
        ?? what do we do with those where mismatch_clean != mismatch_original and mismatch_clean != mismatch_relabel?
        do we not count them at all?
    '''
    total_masked = len(mismatch_original)
    return {'bad_masked':count_bad_masked/total_masked*100, 'good_masked':count_good_masked/total_masked*100, 'bad_relabel':count_bad_relabel/(count_bad_relabel + count_good_relabel)*100, 'good_relabel':count_good_relabel/(count_bad_relabel + count_good_relabel)*100}

def save_noise_shares_to_latex(config):
    source_corpora = config['source_corpora']
    source_corpus = '_'.join(source_corpora)

    corpora = config['corpora']
    if len(corpora) == 15:
        target_corpus = 'all'
    else:
        target_corpus = '_'.join(corpora)
    # we can set the only_results_summarization to true if we only want to re-generate the summary tables, and not re-run the experiments
    for mode in config['parameters']['modes']:
        
        # open new file
        results_file_path = f"{config['paths']['results_tables_path']}/{source_corpus}/{mode}_mode"
        file_noise_shares = open(results_file_path + os.sep + 'noise_shares_' + target_corpus + '.csv','w')
        file_error_numbers = open(results_file_path + os.sep + 'error_numbers_' + target_corpus + '.csv','w')

        file_noise_shares.write(','+','.join([latex_name_dictionary[corpus] for corpus in corpora])+'\n')
        file_error_numbers.write(',,mask,mask,relabel,relabel\n')
        file_error_numbers.write(',,\%$\checkmark$,\%$\\times$,\%$\checkmark$,\%$\\times$\n')

        file_noise_shares.write('Original,'+','.join([noise_shares_dict[corpus] for corpus in corpora])+'\n')
        
        categories = ['category2','category4']
        categories = [cat for cat in categories if cat in config['categories']]

        print(categories)
        
        # write category experiment scores
        for cat in categories:
            resources_path = f"{config['paths']['resources_path']}/relabel_cat{cat[-1]}_source_{source_corpus}/category{cat[-1]}/"
            for p in os.listdir(resources_path):
                if mode in p:
                    mode_metric = p
            
            for p in os.listdir(os.path.join(resources_path,mode_metric)):
                f_type = p

            modifications = ['relabel']

            #logger_experiment.info(f"Read parameter settings for {cat} from {category_table_path}.")
            
            resources_path = os.path.join(resources_path,mode_metric,f_type)
        
            file_noise_shares.write(latex_name_dictionary[f'cat{cat[-1]}'])
            modif = 'relabel'

            for corpus in corpora:
                noise_shares = []
                seed_paths = [path for path in os.listdir(f"{resources_path}/{modif}/{corpus}") if len(path.split('.')) == 1]
                counts_keys = ['good_masked','bad_masked', 'good_relabel', 'bad_relabel']
                counts = { key:[] for key in counts_keys}
                for seed_path in seed_paths:
                    fname = os.path.join(f"{resources_path}/{modif}/{corpus}", seed_path, 'noise_f1.txt')

                    backup_data = os.path.join(f"{resources_path}/{modif}/{corpus}", 'noise_crowd_backup.train')
                    relabeled_data = os.path.join(f"{resources_path}/{modif}/{corpus}", seed_path, 'noise_crowd_relabeled.train')
                    masked_data = os.path.join(f"{resources_path}/mask/{corpus}", seed_path, 'noise_crowd_relabeled.train')
                    original_data = os.path.join(config['paths']['data_path'], corpus+config['paths']['train_filename_extension'])
                    print(relabeled_data)
                    print(masked_data)
                    print(original_data)
                    result_counts = count_errors(original_data, relabeled_data, masked_data)

                    print(result_counts)
                    for key in counts_keys:
                        counts[key].append(result_counts[key])

                    print(fname)
                    if os.path.isfile(fname):
                        with open(fname) as f:
                            lines = f.read().strip().split('\n')
                            noise = lines[0].split(' ')[0]
                            noise_shares.append(float(noise))
                            print(noise)

                score = 100 - float(np.mean(noise_shares))*100
                stdev = float(np.std(noise_shares))*100

                file_noise_shares.write(f",{score:.1f}\\small$\pm${stdev:.1f}")
                # file_noise_shares.write(f",{score:.1f}\\small$\pm${stdev:.1f}")
                file_error_numbers.write(f"{latex_name_dictionary[f'cat{cat[-1]}']},{latex_name_dictionary[corpus]}")
                for key in counts_keys:
                    print(counts[key])
                    if len(counts[key]) > 0:
                        if 'good' in key and np.mean(counts[key]) > 80:
                            file_error_numbers.write(f",\\underline{{{np.mean(counts[key]):.1f}}}")

                        else:
                            file_error_numbers.write(f",{np.mean(counts[key]):.1f}")
                        # file_error_numbers.write(f",{np.mean(counts[key]):.1f}\\small$\pm${np.std(counts[key]):.1f}")
                    else:
                        file_error_numbers.write(f",0.0\\small$\pm$0.0")
                file_error_numbers.write('\n')
            file_noise_shares.write('\n')

        file_noise_shares.close()
        file_error_numbers.close()

        resultsdf = pd.read_csv(results_file_path + os.sep + 'noise_shares_' + target_corpus + '.csv', header=0, index_col=0, delimiter=',')
        resultsdf_errors = pd.read_csv(results_file_path + os.sep + 'error_numbers_' + target_corpus + '.csv', header=[0,1], index_col=[0,1], delimiter=',')

        print(resultsdf_errors)
        print(resultsdf)
        input()
        def extract_mean(val):
            if val is None:
                return 0.0
            val_str = str(val).split('\\small$\\pm$')[0]
            if val_str == '':
                float_val = 0.0
            else:
                float_val = float(val_str)
            return float_val

        df_means = resultsdf.applymap(extract_mean)

        # Step 2: Bold the full string with highest mean per row
        def bold_max_in_row(row, original_row):
            max_idx = row.astype(float).idxmin()
            original_row[max_idx] = f"\\textbf{{{original_row[max_idx]}}}"
            return original_row

        df_bolded = df_means.combine(resultsdf, bold_max_in_row)

        df_bolded.T.to_latex(results_file_path + os.sep + 'noise_shares_' + target_corpus + '.tex', index=True, multicolumn=True,multicolumn_format='c', escape=False, column_format='llccc', label='', caption='')
        print(resultsdf_errors.columns)

        resultsdf_errors.drop(labels=r"\%$\times$", axis=1,level=1, inplace=True)
        
        corpora_order = resultsdf_errors.index.get_level_values(1).unique()

        resultsdf_errors = resultsdf_errors.unstack(level=0)

        print(resultsdf_errors)

        resultsdf_errors = resultsdf_errors.swaplevel(1,0,axis=1).swaplevel(2,1,axis=1).reindex(corpora_order)

        print(resultsdf_errors)

        resultsdf_errors.sort_index(axis=1,level=[0,1,2],ascending=[True,True,True], inplace=True)
        
        print(resultsdf_errors)
        input()

        resultsdf_errors.columns = resultsdf_errors.columns.set_levels(
            resultsdf_errors.columns.levels[1].map(lambda x: x.replace('_',' ')), level=1
        )

        resultsdf_errors.to_latex(results_file_path + os.sep + 'error_numbers_' + target_corpus + '.tex', index=True, multicolumn=True,multicolumn_format='c', escape=False, float_format = '%.1f', column_format='l'+'cccc'*len(categories), label='', caption='')
        temp = df_bolded.T
        temp.columns = pd.MultiIndex.from_arrays([['\% Noise shares'] * len(temp.columns),  temp.columns, [''] + ['relabel'] * (len(temp.columns) - 1),] )

        big_table = pd.concat([temp, resultsdf_errors], axis=1)
        big_table.to_latex(results_file_path + os.sep + 'errors_noise_shares' + target_corpus + '.tex', index=True, multicolumn=True,multicolumn_format='c', escape=False, float_format = '%.1f', column_format='l'+'c'*(len(big_table.columns)), label='', caption='')
