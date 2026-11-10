"""Turning model output into position weights."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SIGNAL_MODES = ("threshold", "proportional")


@dataclass(frozen=True)
class SignalPolicy:
    """How a probability of an up move becomes a long-only position weight.

    threshold:     weight 1 when prob_up >= long_threshold, otherwise 0.
    proportional:  weight ramps linearly from 0 at long_threshold to 1 at
                   full_threshold.

    When target_volatility is set, the weight is also scaled down by
    target_volatility / forecast_volatility (never up), which keeps risk roughly
    constant when the volatility forecast is high.
    """

    mode: str = "threshold"
    long_threshold: float = 0.55
    full_threshold: float = 0.65
    target_volatility: float | None = None
    max_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.mode not in SIGNAL_MODES:
            raise ValueError(f"mode must be one of {SIGNAL_MODES}")
        if not 0.0 < self.long_threshold < 1.0:
            raise ValueError("long_threshold must be in (0, 1)")
        if self.mode == "proportional" and not (
            self.long_threshold < self.full_threshold <= 1.0
        ):
            raise ValueError(
                "full_threshold must be above long_threshold and at most 1"
            )
        if self.target_volatility is not None and self.target_volatility <= 0.0:
            raise ValueError("target_volatility must be positive")
        if not 0.0 < self.max_weight <= 1.0:
            raise ValueError("max_weight must be in (0, 1]")


def probability_to_weight(
    prob_up: np.ndarray | float,
    policy: SignalPolicy | None = None,
    forecast_volatility: np.ndarray | float | None = None,
) -> np.ndarray:
    """Map probabilities of an up move to long-only weights in [0, max_weight].

    Missing (NaN) probabilities give a weight of 0: no signal means no position.
    """
    policy = policy or SignalPolicy()
    prob = np.atleast_1d(np.asarray(prob_up, dtype=float))
    valid = np.isfinite(prob)
    safe = np.where(valid, prob, 0.0)

    if policy.mode == "threshold":
        weight = np.where(safe >= policy.long_threshold, 1.0, 0.0)
    else:
        span = policy.full_threshold - policy.long_threshold
        weight = np.clip((safe - policy.long_threshold) / span, 0.0, 1.0)

    if policy.target_volatility is not None and forecast_volatility is not None:
        vol = np.broadcast_to(
            np.atleast_1d(np.asarray(forecast_volatility, dtype=float)), prob.shape
        )
        usable = np.isfinite(vol) & (vol > 0.0)
        scale = np.where(
            usable,
            np.minimum(1.0, policy.target_volatility / np.where(usable, vol, 1.0)),
            1.0,
        )
        weight = weight * scale

    weight = np.where(valid, weight, 0.0)
    return np.clip(weight, 0.0, policy.max_weight)


DEFAULT_TARGET_VOLATILITY = 0.12


def volatility_target_weight(
    forecast_volatility: np.ndarray | float,
    target_volatility: float = DEFAULT_TARGET_VOLATILITY,
    max_weight: float = 1.0,
) -> np.ndarray:
    """Position sized so the expected volatility of the holding stays near a target.

    The weight is target / forecast, capped at max_weight (the engine is long-only
    and unlevered). A missing forecast gives no position.
    """
    if target_volatility <= 0.0:
        raise ValueError("target_volatility must be positive")
    if not 0.0 < max_weight <= 1.0:
        raise ValueError("max_weight must be in (0, 1]")
    vol = np.atleast_1d(np.asarray(forecast_volatility, dtype=float))
    usable = np.isfinite(vol) & (vol > 0.0)
    weight = np.where(usable, target_volatility / np.where(usable, vol, 1.0), 0.0)
    return np.clip(weight, 0.0, max_weight)
