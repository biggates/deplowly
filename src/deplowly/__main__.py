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
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
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
