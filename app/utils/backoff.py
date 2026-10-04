from __future__ import annotations

import random


def backoff_delay(attempt: int, maximum: float = 60.0) -> float:
    base = min(maximum, float(2 ** max(0, attempt)))
    return min(maximum, base + random.uniform(0, min(1.0, base * 0.2)))
