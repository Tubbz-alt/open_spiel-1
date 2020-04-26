# Copyright 2019 DeepMind Technologies Ltd. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python3
"""An Oracle for any RL algorithm.

An Oracle for any RL algorithm following the OpenSpiel Policy API.
"""

import numpy as np

from open_spiel.python.algorithms.psro_v2 import optimization_oracle
from open_spiel.python.algorithms.psro_v2 import utils
from open_spiel.python.rl_environment import TimeStep
from open_spiel.python.rl_environment import StepType
from open_spiel.python.algorithms.psro_v2.ars_ray.utils import rollout_rewards_combinator
from open_spiel.python.rl_environment import TimeStep
from open_spiel.python.rl_environment import StepType

from open_spiel.python.algorithms.psro_v2.ars_ray.utils import rollout_rewards_combinator

from tqdm import tqdm
import sys
import ray

from open_spiel.python.algorithms.psro_v2.ars_ray.shared_noise import *
from open_spiel.python.algorithms.psro_v2.ars_ray.workers import Worker


def update_episodes_per_oracles(episodes_per_oracle, played_policies_indexes):
  """Updates the current episode count per policy.

  Args:
    episodes_per_oracle: List of list of number of episodes played per policy.
      One list per player.
    played_policies_indexes: List with structure (player_index, policy_index) of
      played policies whose count needs updating.

  Returns:
    Updated count.
  """
  for player_index, policy_index in played_policies_indexes:
    episodes_per_oracle[player_index][policy_index] += 1
  return episodes_per_oracle


def freeze_all(policies_per_player):
  """Freezes all policies within policy_per_player.

  Args:
    policies_per_player: List of list of number of policies.
  """
  for policies in policies_per_player:
    for pol in policies:
      pol.freeze()

def random_count_weighted_choice(count_weight):
  """Returns a randomly sampled index i with P ~ 1 / (count_weight[i] + 1).

  Allows random sampling to prioritize indexes that haven't been sampled as many
  times as others.

  Args:
    count_weight: A list of counts to sample an index from.

  Returns:
    Randomly-sampled index.
  """
  indexes = list(range(len(count_weight)))
  p = np.array([1 / (weight + 1) for weight in count_weight])
  p /= np.sum(p)
  chosen_index = np.random.choice(indexes, p=p)
  return chosen_index

def sample_episode1(state, policies):
    """Samples an episode using policies, starting from state.

    Args:
      state: Pyspiel state representing the current state.
      policies: List of policy representing the policy executed by each player.

    Returns:
      The result of the call to returns() of the final state in the episode.
          Meant to be a win/loss integer.
    """
    if state.is_terminal():
      observations = {"info_state": [], "legal_actions": [], "current_player": []}
      rewards = []
      step_type = StepType.LAST
      cur_rewards = state.rewards()
      for player_id in range(2):
        rewards.append(cur_rewards[player_id])
        observations["info_state"].append(state.information_state_tensor(player_id))
        observations["legal_actions"].append(state.legal_actions(player_id))
      observations["current_player"] = state.current_player()
      time_step = TimeStep(observations=observations,rewards=rewards,discounts=1,step_type=step_type)
      for agent in policies:
        agent.step(time_step)
      return np.array(state.returns(), dtype=np.float32)
    if state.is_simultaneous_node():
      actions = [None] * state.num_players()
      for player in range(state.num_players()):
        state_policy = policies[player].action_probabilities(state, player, is_evaluation=False)
        outcomes, probs = zip(*state_policy.items())
        actions[player] = utils.random_choice(outcomes, probs)
      state.apply_actions(actions)
      return sample_episode1(state, policies)

    if state.is_chance_node():
      outcomes, probs = zip(*state.chance_outcomes())
    else:
      player = state.current_player()
      state_policy = policies[player].action_probabilities(state,is_evaluation=False)
      outcomes, probs = zip(*state_policy.items())

    state.apply_action(utils.random_choice(outcomes, probs))
    return sample_episode1(state, policies)


class RLOracle(optimization_oracle.AbstractOracle):
  """Oracle handling Approximate Best Responses computation."""

  def __init__(self,
               env,
               best_response_class,
               best_response_kwargs,
               number_training_episodes=1e4,
               self_play_proportion=0.0,
               num_workers=16,
               ars_parallel=False,
               **kwargs):
    """Init function for the RLOracle.

    Args:
      env: rl_environment instance.
      best_response_class: class of the best response.
      best_response_kwargs: kwargs of the best response.
      number_training_episodes: (Minimal) number of training episodes to run
        each best response through. May be higher for some policies.
      self_play_proportion: Float, between 0 and 1. Defines the probability that
        a non-currently-training player will actually play (one of) its
        currently training strategy (Which will be trained as well).
      **kwargs: kwargs
    """
    self._env = env

    self._best_response_class = best_response_class
    self._best_response_kwargs = best_response_kwargs

    self._self_play_proportion = self_play_proportion
    self._number_training_episodes = number_training_episodes

    # Initialization for ARS parallel
    self._ars_parallel = ars_parallel
    if ars_parallel:
      ray.init(temp_dir='./ars_temp_dir/')
      self._num_workers = num_workers
      deltas_id = create_shared_noise.remote()
      self.deltas = SharedNoiseTable(ray.get(deltas_id), seed=216)
      self.workers = [Worker.remote(env=self._env,
                                    env_seed=7 * i,
                                    deltas=deltas_id) for i in range(num_workers)]

    super(RLOracle, self).__init__(**kwargs)
          
#  def sample_episode(self,unused_time_step, agents, is_evaluation=False):
#    state = self._env._game.new_initial_state()
#    return sample_episode1(state,agents)

  def sample_episode(self, unused_time_step, agents, is_evaluation=False):
    time_step = self._env.reset()
    cumulative_rewards = 0.0
    while not time_step.last():
      if time_step.is_simultaneous_move():
        action_list = []
        for agent in agents:
          output = agent.step(time_step, is_evaluation=is_evaluation)
          action_list.append(output.action)
        time_step = self._env.step(action_list)
        cumulative_rewards += np.array(time_step.rewards)
      else:
        player_id = time_step.observations["current_player"]

        # is_evaluation is a boolean that, when False, lets policies train. The
        # setting of PSRO requires that all policies be static aside from those
        # being trained by the oracle. is_evaluation could be used to prevent
        # policies from training, yet we have opted for adding frozen attributes
        # that prevents policies from training, for all values of is_evaluation.
        # Since all policies returned by the oracle are frozen before being
        # returned, only currently-trained policies can effectively learn.
        agent_output = agents[player_id].step(time_step, is_evaluation=is_evaluation)
        action_list = [agent_output.action]
        time_step = self._env.step(action_list)
        cumulative_rewards += np.array(time_step.rewards)
    if not is_evaluation:
      for agent in agents:
        agent.step(time_step)
    return cumulative_rewards

  def _has_terminated(self, episodes_per_oracle):
    # The oracle has terminated when all policies have at least trained for
    # self._number_training_episodes. Given the stochastic nature of our
    # training, some policies may have more training episodes than that value.
    return np.all(
        episodes_per_oracle.reshape(-1) > self._number_training_episodes)

  def sample_policies_for_episode(self, new_policies, training_parameters,
                                  episodes_per_oracle, strategy_sampler):
    """Randomly samples a set of policies to run during the next episode.
    If ARS is the policies that needs to be trained, then keep the trained against policy for num_of_directions times

    Note : sampling is biased to select players & strategies that haven't
    trained as much as the others.

    Args:
      new_policies: The currently training policies, list of list, one per
        player.
      training_parameters: List of list of training parameters dictionaries, one
        list per player, one dictionary per training policy.
      episodes_per_oracle: List of list of integers, computing the number of
        episodes trained on by each policy. Used to weight the strategy
        sampling.
      strategy_sampler: Sampling function that samples a joint strategy given
        probabilities.

    Returns:
      Sampled list of policies (One policy per player), index of currently
      training policies in the list.
    """
    num_players = len(training_parameters)

    # Prioritizing players that haven't had as much training as the others.
    episodes_per_player = [sum(episodes) for episodes in episodes_per_oracle]
    chosen_player = random_count_weighted_choice(episodes_per_player)

    # Uniformly choose among the sampled player.
    agent_chosen_ind = np.random.randint(
        0, len(training_parameters[chosen_player]))
    agent_chosen_dict = training_parameters[chosen_player][agent_chosen_ind]
    new_policy = new_policies[chosen_player][agent_chosen_ind]

    # Sample other players' policies.
    total_policies = agent_chosen_dict["total_policies"]
    probabilities_of_playing_policies = agent_chosen_dict[
        "probabilities_of_playing_policies"]

    if type(new_policy._policy).__name__ == 'ARS':
      if not hasattr(self,'_ARS_episodes'):
        self._ARS_episodes = {}
        for i in range(num_players):
          self._ARS_episodes[i] = [0,None]
      ARS_nb_dir_pol = new_policy._policy._nb_directions * 2
      current_count = self._ARS_episodes[chosen_player][0]
      if current_count % ARS_nb_dir_pol == 0:
        episode_policies = strategy_sampler(total_policies, probabilities_of_playing_policies)
        self._ARS_episodes[chosen_player] = [current_count+1, episode_policies]
      else:
        episode_policies = self._ARS_episodes[chosen_player][1]
        self._ARS_episodes[chosen_player][0] += 1
    else:
      episode_policies = strategy_sampler(total_policies,
                                        probabilities_of_playing_policies)

    live_agents_player_index = [(chosen_player, agent_chosen_ind)]

    for player in range(num_players):
      if player == chosen_player:
        episode_policies[player] = new_policy
        assert not new_policy.is_frozen()
      else:
        # Sample a bernoulli with parameter 'self_play_proportion' to determine
        # whether we do self-play with 'player'.
        if np.random.binomial(1, self._self_play_proportion):
          # If we are indeed doing self-play on that round, sample among the
          # trained strategies of current_player, with priority given to less-
          # selected agents.
          assert not episode_policies[player].is_frozen()
          agent_index = random_count_weighted_choice(
              episodes_per_oracle[player])
          self_play_agent = new_policies[player][agent_index]
          episode_policies[player] = self_play_agent
          live_agents_player_index.append((player, agent_index))
        else:
          assert episode_policies[player].is_frozen()

    return episode_policies, live_agents_player_index

  def _rollout(self, game, agents, **oracle_specific_execution_kwargs):
    return self.sample_episode(None, agents, is_evaluation=False)

  def generate_new_policies(self, training_parameters):
    """Generates new policies to be trained into best responses.

    Args:
      training_parameters: list of list of training parameter dictionaries, one
        list per player.

    Returns:
      List of list of the new policies, following the same structure as
      training_parameters.
    """
    new_policies = []
    for player in range(len(training_parameters)):
      player_parameters = training_parameters[player]
      new_pols = []
      for param in player_parameters:
        current_pol = param["policy"]
        if isinstance(current_pol, self._best_response_class):
          new_pol = current_pol.copy_with_noise(self._kwargs.get("sigma", 0.0))
        else:
          new_pol = self._best_response_class(self._env, player,
                                              **self._best_response_kwargs)
        new_pols.append(new_pol)
      new_policies.append(new_pols)
    return new_policies

  def __call__(self,
               game,
               training_parameters,
               strategy_sampler=utils.sample_strategy,
               **oracle_specific_execution_kwargs):
    """Call method for oracle, returns best responses against a set of policies.

    Args:
      game: The game on which the optimization process takes place.
      training_parameters: A list of list of dictionaries (One list per player),
        each dictionary containing the following fields :
        - policy : the policy from which to start training.
        - total_policies: A list of all policy.Policy strategies used for
          training, including the one for the current player.
        - current_player: Integer representing the current player.
        - probabilities_of_playing_policies: A list of arrays representing, per
          player, the probabilities of playing each policy in total_policies for
          the same player.
      strategy_sampler: Callable that samples strategies from total_policies
        meta_games = [meta_games, -meta_games]
        using probabilities_of_playing_policies. It only samples one joint
        set of policies for all players. Implemented to be able to take into
        account joint probabilities of action (For Alpharank)
      **oracle_specific_execution_kwargs: Other set of arguments, for
        compatibility purposes. Can for example represent whether to Rectify
        Training or not.

    Returns:
      A list of list, one for each member of training_parameters, of (epsilon)
      best responses.
    """
    episodes_per_oracle = [[0
                            for _ in range(len(player_params))]
                           for player_params in training_parameters]
    episodes_per_oracle = np.array(episodes_per_oracle)

    new_policies = self.generate_new_policies(training_parameters)
    # TODO(author4): Look into multithreading.

    reward_trace = [[] for _ in range(game.num_players())]
    tot = self._number_training_episodes*game.num_players()
    pbar = tqdm(total=tot, file=sys.stdout, miniters=tot//4, leave=False)
    while not self._has_terminated(episodes_per_oracle):
      agents, indexes = self.sample_policies_for_episode(
          new_policies, training_parameters, episodes_per_oracle,
          strategy_sampler)

      if self._ars_parallel:
        # No reward trace for ARS.
        rollout_rewards, deltas_idx = self.deploy_workers(agents, indexes)
        self.update_ars_agent(rollout_rewards, deltas_idx, agents, indexes)
      else:
        reward = self._rollout(game, agents, **oracle_specific_execution_kwargs)
        reward_trace[indexes[0][0]].append(reward[indexes[0][0]])

      episodes_per_oracle = update_episodes_per_oracles(episodes_per_oracle,
                                                        indexes)
    #   pbar.update(1)
    # pbar.close()

    for i in range(len(reward_trace)):
        reward_trace[i] = utils.lagging_mean(reward_trace[i])
    # Freeze the new policies to keep their weights static. This allows us to
    # later not have to make the distinction between static and training
    # policies in training iterations.
    freeze_all(new_policies)

    # Specified written for ARS aligning same opponent strategies for directions
    if hasattr(self,'_ARS_episodes'):
      delattr(self,'_ARS_episodes')
    return new_policies, reward_trace

    #####################################################
    ############# Parallem Implementation of ARS ########
    #####################################################

  def deploy_workers(self, agents, indexes):
    """
    Running workers and collecting returns of noisy policies for updaing ARS agents.
    :param agents: a list of policies, one per player.
    :param indexes: live agent index.
    :return: returns of noisy policies and corresponding noise indices.
    """
    # put policy weights in the object store
    #TODO: Be careful about the Tensorflow agents. Check if it works.
    agents_id = ray.put(agents)

    nb_directions = agents[indexes[0][0]]._policy._nb_directions
    num_rollouts = int(nb_directions / self._num_workers)

    # parallel generation of rollouts
    rollout_ids_one = [worker.do_sample_episode.remote(agents_id,
                                                 num_rollouts=num_rollouts,
                                                 is_evaluate=False) for worker in self.workers]

    rollout_ids_two = [worker.do_sample_episode.remote(agents_id,
                                                 num_rollouts=1,
                                                 is_evaluate=False) for worker in
                       self.workers[:(nb_directions % self._num_workers)]]

    results_one = ray.get(rollout_ids_one)
    results_two = ray.get(rollout_ids_two)

    rollout_rewards = [[] for _ in agents]
    deltas_idx = []

    for result in results_one:
      deltas_idx += result['deltas_idx']
      rollout_rewards = rollout_rewards_combinator(rollout_rewards, result['rollout_rewards'])

    for result in results_two:
      deltas_idx += result['deltas_idx']
      rollout_rewards = rollout_rewards_combinator(rollout_rewards, result['rollout_rewards'])

    deltas_idx = np.array(deltas_idx)
    # Only pick the rollout_rewards for agent being trained.
    rollout_rewards = np.array(rollout_rewards[indexes[0][0]], dtype=np.float64)

    return rollout_rewards, deltas_idx

  def update_ars_agent(self, rollout_rewards, deltas_idx, agents, indexes):
    """
    Update the active agent.
    """
    agents[indexes[0][0]]._policy._pi_update(rollout_rewards, deltas_idx)
