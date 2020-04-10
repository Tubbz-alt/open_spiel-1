#!/bin/bash

##SBATCH --job-name=egta_kuhn_poker_dqn
#SBATCH --job-name=egta_kuhn_poker_alpharank
#SBATCH --mail-user=yongzhao_wang@outlook.com
#SBATCH --mail-type=BEGIN,END,FAIL
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --mem-per-cpu=5g
#SBATCH --time=02-00:00:00
#SBATCH --account=wellman1
#SBATCH --partition=standard


module load python3.6-anaconda/5.2.0
cd $(dirname "${SLURM_SUBMIT_DIR}")
python psro_v2_example.py --oracle_type=BR --quiesce=False --gpsro_iterations=150 --number_training_episodes=100000 --sbatch_run=True --ars_learning_rate 0.01 --noise 0.01