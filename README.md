# Sample metrics

This repository contains the code to run the token metrics experiments. 

The metric calculation and logging is in ``sequence_tagger_model_extended.py``. 

The main script to run experiments is ``run.py``:

``python run.py -c test_config_parameter.json -g 0``

For more example configs, with which we ran our experiments look at the ``configs_est``, ``configs_german`` and ``configs_noisebench`` folders.
