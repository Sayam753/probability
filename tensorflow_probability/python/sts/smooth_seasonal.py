# Copyright 2018 The TensorFlow Probability Authors.
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
# ============================================================================
"""Smooth Seasonal Model."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# Dependency imports
import tensorflow as tf
import numpy as np

from tensorflow_probability.python import bijectors as tfb
from tensorflow_probability.python import distributions as tfd
from tensorflow_probability.python.internal import dtype_util

from tensorflow_probability.python.sts.internal import util as sts_util
from tensorflow_probability.python.sts.structural_time_series import Parameter
from tensorflow_probability.python.sts.structural_time_series import StructuralTimeSeries


class SmoothSeasonalStateSpaceModel(tfd.LinearGaussianStateSpaceModel):
  """State space model for a smooth seasonal effect."""

  def __init__(self,
               num_timesteps,
               period,
               frequency_multipliers,
               drift_scale,
               initial_state_prior,
               observation_noise_scale=0.,
               initial_step=0,
               validate_args=False,
               allow_nan_stats=True,
               name=None):
    """Build a smooth seasonal state space model."""

    with tf.compat.v1.name_scope(
        name, 'SmoothSeasonalStateSpaceModel', values=[drift_scale]) as name:

      dtype = dtype_util.common_dtype(
          [period, frequency_multipliers, drift_scale, initial_state_prior])

      period = tf.convert_to_tensor(
          value=period, name='period', dtype=dtype)

      frequency_multipliers = tf.convert_to_tensor(
          value=frequency_multipliers,
          name='frequency_multipliers',
          dtype=dtype)

      drift_scale = tf.convert_to_tensor(
          value=drift_scale, name='drift_scale', dtype=dtype)

      observation_noise_scale = tf.convert_to_tensor(
          value=observation_noise_scale,
          name='observation_noise_scale',
          dtype=dtype)

      num_frequencies = static_num_frequencies(frequency_multipliers)

      observation_matrix = tf.tile(
          input=tf.constant([[1., 0.]], dtype=dtype),
          multiples=[1, num_frequencies])

      transition_matrix = build_smooth_seasonal_transition_matrix(
          period=period,
          frequency_multipliers=frequency_multipliers,
          dtype=dtype)

      self._drift_scale = drift_scale
      self._observation_noise_scale = observation_noise_scale
      self._period = period
      self._frequency_multipliers = frequency_multipliers

      super(SmoothSeasonalStateSpaceModel, self).__init__(
          num_timesteps=num_timesteps,
          transition_matrix=transition_matrix,
          transition_noise=tfd.MultivariateNormalDiag(
              scale_diag=(drift_scale[..., tf.newaxis] *
                          tf.ones([2 * num_frequencies], dtype=dtype)),
              name='transition_noise'),
          observation_matrix=observation_matrix,
          observation_noise=tfd.MultivariateNormalDiag(
              scale_diag=observation_noise_scale[..., tf.newaxis],
              name='observation_noise'),
          initial_state_prior=initial_state_prior,
          initial_step=initial_step,
          allow_nan_stats=allow_nan_stats,
          validate_args=validate_args,
          name=name)

  @property
  def drift_scale(self):
    """Standard deviation of the drift in the cyclic effects."""
    return self._drift_scale

  @property
  def observation_noise_scale(self):
    """Standard deviation of the observation noise."""
    return self._observation_noise_scale

  @property
  def period(self):
    """The seasonal period."""
    return self._period

  @property
  def frequency_multipliers(self):
    """Multipliers of the fundamental frequency."""
    return self._frequency_multipliers


def build_smooth_seasonal_transition_matrix(period,
                                            frequency_multipliers,
                                            dtype):
  """Build the transition matrix for a SmoothSeasonalStateSpaceModel"""

  two_pi = tf.constant(2. * np.pi, dtype=dtype)
  frequencies = two_pi * frequency_multipliers / period
  num_frequencies = static_num_frequencies(frequency_multipliers)

  sin_frequencies = tf.sin(frequencies)
  cos_frequencies = tf.cos(frequencies)

  trigonometric_values = tf.stack(
      [cos_frequencies, sin_frequencies, -sin_frequencies, cos_frequencies],
      axis=-1)

  transition_matrix = tf.linalg.LinearOperatorBlockDiag(
      [
          tf.linalg.LinearOperatorFullMatrix(
              matrix=tf.reshape(trigonometric_values[i], [2, 2]),
              is_square=True
          )
          for i in range(num_frequencies)
      ]
  )

  return transition_matrix


def static_num_frequencies(frequency_multipliers):
  """Statically known number of frequencies. Raises if not possible"""

  frequency_multipliers = tf.convert_to_tensor(
      frequency_multipliers, name="frequency_multipliers")

  num_frequencies = tf.compat.dimension_value(frequency_multipliers.shape[0])

  if num_frequencies is None:
    raise ValueError('The number of frequencies must be statically known. Saw '
                     '`frequency_multipliers` with shape {}'.format(
                         frequency_multipliers.shape))

  return num_frequencies


class SmoothSeasonal(StructuralTimeSeries):
  """Formal representation of a smooth seasonal effects model."""

  def __init__(self,
               period,
               frequency_multipliers,
               drift_scale_prior=None,
               initial_state_prior=None,
               observed_time_series=None,
               name=None):
    """Specify a smooth seasonal effects model."""

    with tf.compat.v1.name_scope(
        name, 'SmoothSeasonal', values=[observed_time_series]) as name:

      _, observed_stddev, observed_initial = (
          sts_util.empirical_statistics(observed_time_series)
          if observed_time_series is not None else (0., 1., 0.))

      latent_size = 2 * static_num_frequencies(frequency_multipliers)

      # Heuristic default priors. Overriding these may dramatically
      # change inference performance and results.
      if drift_scale_prior is None:
        drift_scale_prior = tfd.LogNormal(
            loc=tf.math.log(.01 * observed_stddev), scale=3.)

      if initial_state_prior is None:
        initial_state_scale = (
            tf.abs(observed_initial) + observed_stddev)[..., tf.newaxis]
        ones = tf.ones([latent_size], dtype=drift_scale_prior.dtype)
        initial_state_prior = tfd.MultivariateNormalDiag(
            scale_diag=initial_state_scale * ones)

      self._initial_state_prior = initial_state_prior
      self._period = period
      self._frequency_multipliers = frequency_multipliers

      super(SmoothSeasonal, self).__init__(
          parameters=[
              Parameter('drift_scale', drift_scale_prior,
                        tfb.Chain([tfb.AffineScalar(scale=observed_stddev),
                                   tfb.Softplus()])),
          ],
          latent_size=latent_size,
          name=name)

  @property
  def period(self):
    """The seasonal period."""
    return self._period

  @property
  def frequency_multipliers(self):
    """Multipliers of the fundamental frequency."""
    return self._frequency_multipliers

  @property
  def initial_state_prior(self):
    """Prior distribution on the initial latent state (cyclic effects)."""
    return self._initial_state_prior

  def _make_state_space_model(self,
                              num_timesteps,
                              param_map,
                              initial_state_prior=None,
                              initial_step=0):

    if initial_state_prior is None:
      initial_state_prior = self.initial_state_prior

    return SmoothSeasonalStateSpaceModel(
        num_timesteps=num_timesteps,
        period=self.period,
        frequency_multipliers=self.frequency_multipliers,
        initial_state_prior=initial_state_prior,
        initial_step=initial_step,
        **param_map)
