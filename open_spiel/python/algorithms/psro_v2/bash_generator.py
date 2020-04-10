import shutil
import os
import numpy as np
import itertools
import copy

def copy_file(original, target):
    shutil.copyfile(original, target)
    # print("Copy file from: ", original)
    # print("Copy file to: ", target)

def delete_last_line(file):
    # Move the pointer (similar to a cursor in a text editor) to the end of the file
    file.seek(0, os.SEEK_END)

    # This code means the following code skips the very last character in the file -
    # i.e. in the case the last line is null we delete the last line
    # and the penultimate one
    pos = file.tell() - 1

    # Read each character in the file one at a time from the penultimate
    # character going backwards, searching for a newline character
    # If we find a new line, exit the search
    while pos > 0 and file.read(1) != "\n":
        pos -= 1
        file.seek(pos, os.SEEK_SET)

    # So long as we're not at the start of the file, delete all the characters ahead
    # of this position
    if pos > 0:
        file.seek(pos, os.SEEK_SET)
        file.truncate()

def write_line(file, line):
    file.write(line)

def random_search(num_output, param_range_dict, param_dtype_dict):
    output = {}
    for key in param_range_dict:
        if param_dtype_dict[key] == "int":
            sample = list(np.random.choice(range(*param_range_dict[key]), size=num_output, replace=False))
        elif param_dtype_dict[key] == "float":
            sample = np.random.uniform(*param_range_dict[key], size=num_output)
            sample = list(np.round(sample, decimals=2))
        else:
            raise NotImplementedError
        output[key] = sample
    return output

def grid_search(param_dict):
    return list(itertools.product(*param_dict.values()))

TARGET_DIR = os.getcwd() + '/slurm_scripts/'
ORIGIN = os.getcwd() + '/base_slurm.sh'
COMMAND = "python psro_v2_example.py --oracle_type=BR --quiesce=False --gpsro_iterations=150 --number_training_episodes=100000 --sbatch_run=True"

def bash_factory(num_files=10, grid_search_flag=True):
    param_dict = {'ars_learning_rate': list(np.round(np.arange(0.01, 0.1, 0.01), decimals=2)),
                  'noise': list(np.round(np.arange(0.01, 0.1, 0.01), decimals=2))}
    if grid_search_flag:
        params = grid_search(param_dict)
    else:
        raise NotImplementedError
    for i, item in enumerate(params):
        nick_name = ''
        for value in item:
            nick_name += "_" + str(value)
        target = TARGET_DIR + str(i) + nick_name + '.sh'
        copy_file(ORIGIN, target)
        new_command = copy.copy(COMMAND)
        for j, key in enumerate(param_dict.keys()):
            arg = ' --' + key + ' ' + str(item[j])
            new_command += arg
        with open(target, 'a') as file:
            write_line(file, new_command)

bash_factory()


