"""Validate a deplowly configuration without starting the watcher.

``deplowly check`` is meant to run inside the cluster (e.g. a one-off
``kubectl run`` pod), so an operator can verify a config using only kubectl —
no local Python/uv install required. The check is purely local: it never talks
to the Kubernetes API.
"""

from __future__ import annotations

import argparse
import logging
import sys

import structlog

from .config import ConfigError, load_config
from .models import Config

# 容器内约定路径，与 deploy/deployment.yaml 保持一致。
_DEFAULT_PATH = "/etc/deplowly/config.yaml"


def describe(config: Config) -> str:
    """Render a human-readable summary of a validated config."""
    lines = [f"config OK: default_interval={config.default_interval}s, {len(config.targets)} target(s)"]
    for i, target in enumerate(config.targets, start=1):
        interval = target.interval or config.default_interval
        lines.append(f"  [{i}] {target.key} every {interval}s")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="deplowly check",
        description="校验 deplowly 配置文件（纯本地校验，不访问集群）",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=_DEFAULT_PATH,
        help=f"配置文件路径；'-' 表示从 stdin 读取（默认 {_DEFAULT_PATH}）",
    )
    args = parser.parse_args(argv)

    # check 只关心校验结果，把 structlog 降到 WARNING，保持输出干净。
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
    )

    try:
        text = sys.stdin.read() if args.path == "-" else None
        config = load_config(args.path, text=text)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    print(describe(config))
    return 0
