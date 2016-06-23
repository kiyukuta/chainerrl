from __future__ import division
from __future__ import unicode_literals
from __future__ import print_function
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()
import numpy as np


def select_action_epsilon_greedily(epsilon, random_action_func,
                                   greedy_action_func):
    if np.random.rand() < epsilon:
        return random_action_func()
    else:
        return greedy_action_func()


class LinearDecayEpsilonGreedy(object):

    def __init__(self, start_epsilon, end_epsilon,
                 decay_steps, random_action_func):
        self.start_epsilon = start_epsilon
        self.end_epsilon = end_epsilon
        self.decay_steps = decay_steps
        self.random_action_func = random_action_func

    def compute_epsilon(self, t):
        if t > self.decay_steps:
            return self.end_epsilon
        else:
            return self.start_epsilon + \
                (self.end_epsilon - self.start_epsilon) * (t / self.decay_steps)

    def select_action(self, t, greedy_action_func):
        self.epsilon = self.compute_epsilon(t)
        return select_action_epsilon_greedily(
            self.epsilon, self.random_action_func, greedy_action_func)

    def __repr__(self):
        return 'LinearDecayEpsilonGreedy(epsilon={})'.format(self.epsilon)
