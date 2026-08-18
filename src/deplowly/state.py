from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class State:
    """In-memory baseline store keyed by ``namespace/deployment``.

    Only the per-deployment *aggregated* digest (a stable hash of all container
    digests) is kept, so a process restart may miss at most one offline change.
    """

    # key -> aggregated digest string
    last_seen: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.last_seen.get(key)

    def set(self, key: str, digest: str) -> None:
        self.last_seen[key] = digest

    def has(self, key: str) -> bool:
        return key in self.last_seen
