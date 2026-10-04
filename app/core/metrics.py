from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Metrics:
    counters: Counter[str] = field(default_factory=Counter)
    gauges: dict[str, int | float] = field(default_factory=dict)

    def inc(self, key: str, value: int = 1) -> None:
        self.counters[key] += value

    def set(self, key: str, value: int | float) -> None:
        self.gauges[key] = value

    def snapshot(self) -> dict[str, int | float]:
        data: dict[str, int | float] = dict(self.counters)
        data.update(self.gauges)
        return data

    def prometheus(self) -> str:
        lines: list[str] = []
        for key, value in self.snapshot().items():
            safe = key.replace("-", "_")
            lines.append(f"{safe} {value}")
        return "\n".join(lines) + "\n"
