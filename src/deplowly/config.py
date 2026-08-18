from __future__ import annotations

import sys
from pathlib import Path

import structlog
import yaml

from .models import Config, Target

logger = structlog.get_logger(__name__)


class ConfigError(Exception):
    """Raised when the configuration file is invalid."""


def load_config(path: str | Path) -> Config:
    """Load and validate the YAML configuration file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("top-level config must be a mapping")

    default_interval = raw.get("default_interval")
    if not isinstance(default_interval, int) or default_interval <= 0:
        raise ConfigError("default_interval must be a positive integer")

    raw_targets = raw.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigError("targets must be a non-empty list")

    targets: list[Target] = []
    for i, item in enumerate(raw_targets):
        if not isinstance(item, dict):
            raise ConfigError(f"targets[{i}] must be a mapping")
        namespace = item.get("namespace")
        deployment = item.get("deployment")
        if not namespace or not deployment:
            raise ConfigError(f"targets[{i}] requires namespace and deployment")
        interval = item.get("interval")
        if interval is not None and (not isinstance(interval, int) or interval <= 0):
            raise ConfigError(f"targets[{i}].interval must be a positive integer")
        targets.append(
            Target(namespace=str(namespace), deployment=str(deployment), interval=interval)
        )

    config = Config(default_interval=default_interval, targets=targets)
    logger.info(
        "config loaded",
        path=str(path),
        default_interval=default_interval,
        target_count=len(targets),
    )
    return config


def interval_for(target: Target, default_interval: int) -> int:
    return target.interval or default_interval


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    load_config(path)
    print("config OK")


if __name__ == "__main__":
    main()
