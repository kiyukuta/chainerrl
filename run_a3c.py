from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals
from __future__ import absolute_import
from builtins import range
from builtins import open
from builtins import str
from future import standard_library
standard_library.install_aliases()
import argparse
import copy
import multiprocessing as mp
import os
import sys
import statistics
import tempfile
import time

import chainer
from chainer import links as L
from chainer import functions as F
import cv2
import numpy as np

from agents import a3c
import random_seed
import async
from prepare_output_dir import prepare_output_dir


def eval_performance(process_idx, make_env, model, phi, n_runs, greedy=False):
    assert n_runs > 1, 'Computing stdev requires at least two runs'
    scores = []

    for i in range(n_runs):
        model.reset_state()
        env = make_env(process_idx, test=True)
        if hasattr(env, 'spec'):
            timestep_limit = env.spec.timestep_limit
        else:
            timestep_limit = None
        obs = env.reset()
        done = False
        test_r = 0
        t = 0
        while not done:
            s = chainer.Variable(np.expand_dims(phi(obs), 0))
            pout, _ = model.pi_and_v(s)
            if greedy:
                a = pout.most_probable_actions.data[0]
            else:
                a = pout.sampled_actions.data[0]
            obs, r, done, info = env.step(a)
            test_r += r
            t += 1
            if timestep_limit is not None and t >= timestep_limit:
                break
        scores.append(test_r)
        print('test_{}:'.format(i), test_r)
    mean = statistics.mean(scores)
    median = statistics.median(scores)
    stdev = statistics.stdev(scores)
    return mean, median, stdev


def train_loop(process_idx, counter, make_env, max_score, eval_frequency,
               eval_n_runs, agent, env, start_time, steps, outdir):
    try:

        total_r = 0
        episode_r = 0
        global_t = 0
        local_t = 0
        obs = env.reset()
        r = 0
        done = False
        base_lr = agent.optimizer.lr

        while True:

            # Get and increment the global counter
            with counter.get_lock():
                counter.value += 1
                global_t = counter.value
            local_t += 1

            if global_t > steps:
                break

            agent.optimizer.lr = (steps - global_t - 1) / steps * base_lr

            total_r += r
            episode_r += r

            a = agent.act(obs, r, done)

            if done:
                if process_idx == 0:
                    print('{} global_t:{} local_t:{} lr:{} r:{}'.format(
                        outdir, global_t, local_t, agent.optimizer.lr,
                        episode_r))
                episode_r = 0
                obs = env.reset()
                r = 0
                done = False
            else:
                obs, r, done, info = env.step(a)

            if global_t % eval_frequency == 0:
                # Evaluation

                # We must use a copy of the model because test runs can change
                # the hidden states of the model
                test_model = copy.deepcopy(agent.model)
                test_model.reset_state()

                mean, median, stdev = eval_performance(
                    process_idx, make_env, test_model, agent.phi,
                    eval_n_runs)
                with open(os.path.join(outdir, 'scores.txt'), 'a+') as f:
                    elapsed = time.time() - start_time
                    record = (global_t, elapsed, mean, median, stdev)
                    print('\t'.join(str(x) for x in record), file=f)
                with max_score.get_lock():
                    if mean > max_score.value:
                        # Save the best model so far
                        print('The best score is updated {} -> {}'.format(
                            max_score.value, mean))
                        filename = os.path.join(
                            outdir, '{}.h5'.format(global_t))
                        agent.save_model(filename)
                        print('Saved the current best model to {}'.format(
                            filename))
                        max_score.value = mean

    except KeyboardInterrupt:
        if process_idx == 0:
            # Save the current model before being killed
            agent.save_model(os.path.join(
                outdir, '{}_keyboardinterrupt.h5'.format(global_t)))
            print('Saved the current model to {}'.format(
                outdir), file=sys.stderr)
        raise

    if global_t == steps + 1:
        # Save the final model
        agent.save_model(
            os.path.join(outdir, '{}_finish.h5'.format(steps)))
        print('Saved the final model to {}'.format(outdir))


def train_loop_with_profile(process_idx, counter, make_env, max_score,
                            eval_frequency, eval_n_runs, agent, env,
                            start_time, steps, outdir):
    import cProfile
    cmd = 'train_loop(process_idx, counter, make_env, max_score, ' \
        'eval_frequency, eval_n_runs, agent, env, start_time, steps, ' \
        'outdir)'
    cProfile.runctx(cmd, globals(), locals(),
                    'profile-{}.out'.format(os.getpid()))


def run_a3c(processes, make_env, model_opt, phi, t_max=1, beta=1e-2,
            profile=False, steps=8 * 10 ** 7, eval_frequency=10 ** 6,
            eval_n_runs=10, use_terminal_state_value=False, gamma=0.99,
            args={}):

    # Prevent numpy from using multiple threads
    os.environ['OMP_NUM_THREADS'] = '1'

    outdir = prepare_output_dir(args, None)

    print('Output files are saved in {}'.format(outdir))

    n_actions = 20 * 20

    model, opt = model_opt()

    shared_params = async.share_params_as_shared_arrays(model)
    shared_states = async.share_states_as_shared_arrays(opt)

    max_score = mp.Value('f', np.finfo(np.float32).min)
    counter = mp.Value('l', 0)
    start_time = time.time()

    # Write a header line first
    with open(os.path.join(outdir, 'scores.txt'), 'a+') as f:
        column_names = ('steps', 'elapsed', 'mean', 'median', 'stdev')
        print('\t'.join(column_names), file=f)

    def run_func(process_idx):
        env = make_env(process_idx, test=False)
        model, opt = model_opt()
        async.set_shared_params(model, shared_params)
        async.set_shared_states(opt, shared_states)

        agent = a3c.A3C(model, opt, t_max, gamma, beta=beta,
                        process_idx=process_idx, phi=phi,
                        use_terminal_state_value=use_terminal_state_value)

        if profile:
            train_loop_with_profile(process_idx, counter, make_env, max_score,
                                    eval_frequency, eval_n_runs, agent, env,
                                    start_time, steps, outdir=outdir)
        else:
            train_loop(process_idx, counter, make_env, max_score,
                       eval_frequency, eval_n_runs, agent, env, start_time,
                       steps, outdir=outdir)

    async.run_async(processes, run_func)

    return model, opt
