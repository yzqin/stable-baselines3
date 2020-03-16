from typing import Type, Union, Callable, Optional, Dict, Any

import torch as th

from torchy_baselines.common.base_class import OffPolicyRLModel
from torchy_baselines.common.type_aliases import GymEnv, MaybeCallback
from torchy_baselines.common.noise import ActionNoise
from torchy_baselines.td3.td3 import TD3, TD3Policy
from torchy_baselines.cem_rl.cem import CEM


class CEMRL(TD3):
    """
    Implementation of CEM-RL, in fact CEM combined with TD3.

    Paper: https://arxiv.org/abs/1810.01222
    Code: https://github.com/apourchot/CEM-RL

    :param policy: (TD3Policy or str) The policy model to use (MlpPolicy, CnnPolicy, ...)
    :param env: (GymEnv or str) The environment to learn from (if registered in Gym, can be str)
    :param learning_rate: (float or callable) learning rate for adam optimizer,
        the same learning rate will be used for all networks (Q-Values, Actor and Value function)
        it can be a function of the current progress (from 1 to 0)
    :param buffer_size: (int) size of the replay buffer
    :param learning_starts: (int) how many steps of the model to collect transitions for before learning starts
    :param batch_size: (int) Minibatch size for each gradient update
    :param tau: (float) the soft update coefficient ("polyak update", between 0 and 1)
    :param gamma: (float) the discount factor
    :param n_episodes_rollout: (int) Update the model every ``n_episodes_rollout`` episodes.
    :param action_noise: (ActionNoise) the action noise type (None by default), this can help
        for hard exploration problem. Cf common.noise for the different action noise type.
    :param policy_delay: (int) Policy and target networks will only be updated once every policy_delay steps
        per training steps. The Q values will be updated policy_delay more often (update every training step).
    :param target_policy_noise: (float) Standard deviation of Gaussian noise added to target policy
        (smoothing noise)
    :param target_noise_clip: (float) Limit for absolute value of target policy smoothing noise.
    :param sigma_init: (float) Initial standard deviation of the population distribution
    :param pop_size: (int) Number of individuals in the population
    :param damping_init: (float)  Initial value of damping for preventing from early convergence.
    :param damping_final: (float) Final value of damping
    :param elitism: (bool) Keep the best known individual in the population
    :param n_grad: (int) Number of individuals that will receive a gradient update.
        Half of the population size in the paper.
    :param update_style: (str) Update style for the individual that will use the gradient:
        - original: original implementation (actor_steps // n_grad steps for the critic
        and actor_steps gradient steps per individual)
        - original_td3: same as before but the target networks are only update afterward
        - td3_like: use policy delay and `actor_steps` steps for both the critic and the individual
        - other: `2 * (actor_steps // self.n_grad)` for the critic and the individual
    :param create_eval_env: (bool) Whether to create a second environment that will be
        used for evaluating the agent periodically. (Only available when passing string for the environment)
    :param policy_kwargs: (dict) additional arguments to be passed to the policy on creation
    :param verbose: (int) the verbosity level: 0 no output, 1 info, 2 debug
    :param seed: (int) Seed for the pseudo random generators
    :param device: (str or th.device) Device (cpu, cuda, ...) on which the code should be run.
        Setting it to auto, the code will be run on the GPU if possible.
    :param _init_setup_model: (bool) Whether or not to build the network at the creation of the instance
    """
    def __init__(self, policy: Union[str, Type[TD3Policy]],
                 env: Union[GymEnv, str],
                 learning_rate: Union[float, Callable] = 1e-3,
                 buffer_size: int = int(1e6),
                 learning_starts: int = 100,
                 batch_size: int = 100,
                 tau: float = 0.005,
                 gamma: float = 0.99,
                 n_episodes_rollout: int = 1,
                 action_noise: Optional[ActionNoise] = None,
                 policy_delay: int = 2,
                 target_policy_noise: float = 0.2,
                 target_noise_clip: float = 0.5,
                 sigma_init: float = 1e-3,
                 pop_size: int = 10,
                 damping_init: float = 1e-3,
                 damping_final: float = 1e-5,
                 elitism: bool = False,
                 n_grad: int = 5,
                 update_style: str = 'original',
                 tensorboard_log: Optional[str] = None,
                 create_eval_env: bool = False,
                 policy_kwargs: Dict[str, Any] = None,
                 verbose: int = 0,
                 seed: Optional[int] = None,
                 device: Union[th.device, str] = 'auto',
                 _init_setup_model: bool = True):

        super(CEMRL, self).__init__(policy, env,
                                    buffer_size=buffer_size, learning_rate=learning_rate, seed=seed, device=device,
                                    action_noise=action_noise, target_policy_noise=target_policy_noise,
                                    target_noise_clip=target_noise_clip, learning_starts=learning_starts,
                                    n_episodes_rollout=n_episodes_rollout, tau=tau, gamma=gamma,
                                    policy_kwargs=policy_kwargs, verbose=verbose,
                                    policy_delay=policy_delay, batch_size=batch_size,
                                    create_eval_env=create_eval_env, tensorboard_log=tensorboard_log,
                                    _init_setup_model=False)

        # Evolution strategy method that follows cma-es interface (ask-tell)
        # for now, only CEM is implemented
        self.es = None  # type: Optional[CEM]
        self.sigma_init = sigma_init
        self.pop_size = pop_size
        self.damping_init = damping_init
        self.damping_final = damping_final
        self.elitism = elitism
        self.n_grad = n_grad
        self.es_params = None
        self.update_style = update_style
        self.fitnesses = []

        if _init_setup_model:
            self._setup_model()

    def _setup_model(self) -> None:
        super(CEMRL, self)._setup_model()
        params_vector = self.actor.parameters_to_vector()
        self.es = CEM(len(params_vector), mu_init=params_vector,
                      sigma_init=self.sigma_init, damping_init=self.damping_init, damping_final=self.damping_final,
                      pop_size=self.pop_size, antithetic=not self.pop_size % 2, parents=self.pop_size // 2,
                      elitism=self.elitism)

    def learn(self,
              total_timesteps: int,
              callback: MaybeCallback = None,
              log_interval: int = 4,
              eval_env: Optional[GymEnv] = None,
              eval_freq: int = -1,
              n_eval_episodes: int = 5,
              tb_log_name: str = "CEMRL",
              eval_log_path: Optional[str] = None,
              reset_num_timesteps: bool = True) -> OffPolicyRLModel:

        episode_num, obs, callback = self._setup_learn(eval_env, callback, eval_freq,
                                                       n_eval_episodes, eval_log_path, reset_num_timesteps)
        actor_steps = 0
        continue_training = True

        callback.on_training_start(locals(), globals())

        while self.num_timesteps < total_timesteps:

            self.fitnesses = []
            self.es_params = self.es.ask(self.pop_size)

            if self.num_timesteps > 0:
                # self.train(episode_timesteps)
                # Gradient steps for half of the population
                for i in range(self.n_grad):
                    # set params
                    self.actor.load_from_vector(self.es_params[i])
                    self.actor_target.load_from_vector(self.es_params[i])
                    self.actor.optimizer = th.optim.Adam(self.actor.parameters(),
                                                         lr=self.lr_schedule(self._current_progress))

                    # In the paper: 2 * actor_steps // self.n_grad
                    # In the original implementation: actor_steps // self.n_grad
                    # Difference with TD3 implementation:
                    # the target critic is updated in the train_critic()
                    # instead of the train_actor() and no policy delay
                    # Issue with this update style: the bigger the population, the slower the code
                    if self.update_style == 'original':
                        self.train_critic(actor_steps // self.n_grad, tau=self.tau)
                        self.train_actor(actor_steps, tau_actor=self.tau, tau_critic=0.0)
                    elif self.update_style == 'original_td3':
                        self.train_critic(actor_steps // self.n_grad, tau=0.0)
                        self.train_actor(actor_steps, tau_actor=self.tau, tau_critic=self.tau)
                    else:
                        # Closer to td3: with policy delay
                        if self.update_style == 'td3_like':
                            n_training_steps = actor_steps
                        else:
                            # scales with a bigger population
                            # but less training steps per agent
                            n_training_steps = 2 * (actor_steps // self.n_grad)
                        for it in range(n_training_steps):
                            # Sample replay buffer
                            replay_data = self.replay_buffer.sample(self.batch_size, env=self._vec_normalize_env)
                            self.train_critic(replay_data=replay_data)

                            # Delayed policy updates
                            if it % self.policy_delay == 0:
                                self.train_actor(replay_data=replay_data, tau_actor=self.tau, tau_critic=self.tau)

                    # Get the params back in the population
                    self.es_params[i] = self.actor.parameters_to_vector()

            actor_steps = 0
            # evaluate all actors
            for params in self.es_params:
                self.actor.load_from_vector(params)

                rollout = self.collect_rollouts(self.env, n_episodes=self.n_episodes_rollout,
                                                n_steps=-1, action_noise=self.action_noise,
                                                callback=callback,
                                                learning_starts=self.learning_starts,
                                                replay_buffer=self.replay_buffer,
                                                obs=obs, episode_num=episode_num,
                                                log_interval=log_interval)

                # Unpack
                episode_reward, episode_timesteps, n_episodes, obs, continue_training = rollout

                if continue_training is False:
                    break

                episode_num += n_episodes
                actor_steps += episode_timesteps
                self.fitnesses.append(episode_reward)

            if continue_training is False:
                break

            self._update_current_progress(self.num_timesteps, total_timesteps)
            self.es.tell(self.es_params, self.fitnesses)

        callback.on_training_end()

        return self
