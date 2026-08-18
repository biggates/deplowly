from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Target:
    namespace: str
    deployment: str
    interval: int | None = None

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.deployment}"


@dataclass
class Config:
    default_interval: int
    targets: list[Target] = field(default_factory=list)
