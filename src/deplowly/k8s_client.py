from __future__ import annotations

import asyncio
from typing import Any

import structlog
from kubernetes import client
from kubernetes.config import ConfigException, load_incluster_config

logger = structlog.get_logger(__name__)

RESTART_ANNOTATION = "kubectl.kubernetes.io/restartedAt"


class K8sClient:
    """Thin wrapper around the sync kubernetes client.

    All blocking API calls are dispatched through ``asyncio.to_thread`` so the
    event loop is never stalled by a slow apiserver call.
    """

    def __init__(self) -> None:
        try:
            load_incluster_config()
        except ConfigException as exc:  # pragma: no cover - depends on runtime env
            logger.error("failed to load in-cluster config", error=str(exc))
            raise
        self._apps = client.AppsV1Api()
        self._core = client.CoreV1Api()

    async def get_deployment_image_specs(self, namespace: str, deployment: str) -> list[str]:
        """Return every container image (init + regular) of a Deployment."""

        def _sync() -> list[str]:
            dep = self._apps.read_namespaced_deployment(deployment, namespace)
            spec = dep.spec.template.spec
            images: list[str] = []
            for c in spec.init_containers or []:
                if c.image:
                    images.append(c.image)
            for c in spec.containers:
                if c.image:
                    images.append(c.image)
            return images

        return await asyncio.to_thread(_sync)

    async def get_image_pull_secrets(self, namespace: str, deployment: str) -> list[str]:
        """Return the PodSpec-level imagePullSecret names of a Deployment."""

        def _sync() -> list[str]:
            dep = self._apps.read_namespaced_deployment(deployment, namespace)
            spec = dep.spec.template.spec
            if not spec.image_pull_secrets:
                return []
            return [s.name for s in spec.image_pull_secrets]

        return await asyncio.to_thread(_sync)

    async def read_secret_dockerconfigjson(self, namespace: str, name: str) -> dict[str, Any]:
        """Read a Secret and return the parsed ``.dockerconfigjson`` auths map."""

        def _sync() -> dict[str, Any]:
            secret = self._core.read_namespaced_secret(name, namespace)
            data = secret.data or {}
            raw = data.get(".dockerconfigjson")
            if not raw:
                raise KeyError(f"secret {namespace}/{name} has no .dockerconfigjson")
            import base64

            decoded = base64.b64decode(raw).decode("utf-8")
            import json

            return json.loads(decoded).get("auths", {})

        return await asyncio.to_thread(_sync)

    async def patch_restart_annotation(self, namespace: str, deployment: str) -> None:
        """Trigger a rollout restart by patching the restartedAt annotation."""

        import datetime

        now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _sync() -> None:
            body = {"spec": {"template": {"metadata": {"annotations": {RESTART_ANNOTATION: now}}}}}
            self._apps.patch_namespaced_deployment(name=deployment, namespace=namespace, body=body)

        await asyncio.to_thread(_sync)
        logger.info("triggered rollout restart", namespace=namespace, deployment=deployment)
