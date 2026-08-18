from __future__ import annotations

import asyncio
import hashlib

import structlog

from .k8s_client import K8sClient
from .models import Config, Target
from .registry import get_remote_digest, parse_image
from .state import State

logger = structlog.get_logger(__name__)


def _aggregate(per_container: list[tuple[str, str]]) -> str:
    """Aggregate container digests into one stable string."""
    h = hashlib.sha256()
    for image, digest in sorted(per_container):
        h.update(f"{image}={digest}\n".encode())
    return h.hexdigest()


async def _resolve_auths(k8s: K8sClient, target: Target) -> dict[str, dict[str, str]]:
    """Build a registry->auth map from the Deployment's imagePullSecrets."""
    auths: dict[str, dict[str, str]] = {}
    secret_names = await k8s.get_image_pull_secrets(target.namespace, target.deployment)
    for name in secret_names:
        try:
            cfg = await k8s.read_secret_dockerconfigjson(target.namespace, name)
            for host, entry in cfg.items():
                # entry: {"username":..., "password":..., "auth":...}
                auths[host] = entry
        except Exception as exc:  # noqa: BLE001 - conservative: skip one secret
            logger.warning(
                "failed to read imagePullSecret",
                namespace=target.namespace,
                secret=name,
                error=str(exc),
            )
    return auths


async def _check_target(k8s: K8sClient, target: Target, state: State) -> None:
    """One round of digest comparison for a single target."""
    images = await k8s.get_deployment_image_specs(target.namespace, target.deployment)
    if not images:
        logger.warning("deployment has no container images", target=target.key)
        return

    logger.debug("checking target", target=target.key, images=images)

    auths = await _resolve_auths(k8s, target)
    logger.debug("resolved registry auths", target=target.key, hosts=list(auths.keys()))

    per_container: list[tuple[str, str]] = []
    for image in images:
        ref = parse_image(image)
        if ref.is_pinned:
            # digest-pinned: cannot watch for change, use the pinned digest so
            # the aggregate stays stable without triggering a restart.
            per_container.append((image, ref.raw.split("@", 1)[1]))
            continue
        digest = await asyncio.to_thread(get_remote_digest, image, auths)
        if digest is None:
            # registry failure -> conservative skip, try next round
            logger.warning("skipping target round due to registry failure", target=target.key)
            return
        per_container.append((image, digest))

    aggregated = _aggregate(per_container)
    logger.debug("aggregated digest", target=target.key, digest=aggregated, prev=state.get(target.key))
    if not state.has(target.key):
        # cold-start baseline: record but do not restart
        state.set(target.key, aggregated)
        logger.info("bootstrap baseline recorded", target=target.key, digest=aggregated)
        return

    if state.get(target.key) != aggregated:
        logger.info("digest changed, restarting", target=target.key, digest=aggregated)
        await k8s.patch_restart_annotation(target.namespace, target.deployment)
        state.set(target.key, aggregated)


async def watch_target(k8s: K8sClient, target: Target, state: State, interval: int) -> None:
    """Long-running task: poll ``target`` every ``interval`` seconds."""
    while True:
        try:
            await _check_target(k8s, target, state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let one target die
            logger.error("unexpected error in target loop", target=target.key, error=str(exc))
        await asyncio.sleep(interval)


def start_watchers(k8s: K8sClient, config: Config, state: State) -> list[asyncio.Task[None]]:
    """Spawn one asyncio task per target."""
    tasks: list[asyncio.Task[None]] = []
    for target in config.targets:
        interval = target.interval or config.default_interval
        tasks.append(asyncio.create_task(watch_target(k8s, target, state, interval)))
    return tasks
