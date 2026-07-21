"""Small deterministic numerical helpers shared by core modules."""
from __future__ import annotations

import math
import random
from typing import List

from .config import SERVICE_SIZE

def lognormal_size(rng: random.Random, service_type: str) -> float:
    mean, sigma = SERVICE_SIZE[service_type]
    # Convert a target mean into the mu parameter of a log-normal distribution.
    mu = math.log(mean) - 0.5 * sigma * sigma
    return max(2.0, rng.lognormvariate(mu, sigma))


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    rank = (len(sorted_values) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[int(rank)]
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight
