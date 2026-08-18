from __future__ import annotations

import asyncio
import os
import signal
import sys

import structlog

from .config import ConfigError, load_config
from .k8s_client import K8sClient, K8sConfigError
from .state import State
from .watcher import start_watchers

logger = structlog.get_logger(__name__)

# 已知的子命令。非子命令参数（如配置文件路径）走原 "启动 watcher" 逻辑，保持兼容。
_SUBCOMMANDS = {"rbac"}


def _configure_logging() -> None:
    import logging

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


async def run(config_path: str) -> None:
    _configure_logging()
    config = load_config(config_path)
    k8s = K8sClient()
    state = State()
    tasks = start_watchers(k8s, config, state)

    stop = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("received shutdown signal, cancelling watchers")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:  # pragma: no cover - windows
            signal.signal(sig, lambda *_: _handle_signal())

    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("all watchers stopped, exiting")


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in _SUBCOMMANDS:
        if argv[0] == "rbac":
            from . import rbac

            sys.exit(rbac.main(argv[1:]))

    # 兼容旧用法：deplowly [config_path]，直接启动 watcher
    config_path = argv[0] if argv else "config.yaml"
    try:
        asyncio.run(run(config_path))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(1)
    except K8sConfigError as exc:
        print(f"kubernetes config error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:  # pragma: no cover
        pass


if __name__ == "__main__":
    main()
