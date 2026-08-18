from __future__ import annotations

import asyncio
import base64
import datetime
import json
import os
from typing import Any

import structlog
from kubernetes_lite.client import DynamicClient

logger = structlog.get_logger(__name__)


class K8sConfigError(RuntimeError):
    """Raised when the Kubernetes configuration cannot be loaded."""


RESTART_ANNOTATION = "kubectl.kubernetes.io/restartedAt"
# Strategic merge patch, the same patch type the official client uses by default.
PATCH_TYPE = "application/strategic-merge-patch+json"


class K8sClient:
    """Thin wrapper around the kubernetes_lite dynamic client.

    All blocking API calls are dispatched through ``asyncio.to_thread`` so the
    event loop is never stalled by a slow apiserver call.
    """

    def __init__(self) -> None:
        # DynamicClient() without explicit config uses controller-runtime's
        # GetConfig, which loads the in-cluster config when running inside a
        # pod and falls back to the local kubeconfig otherwise.
        try:
            self._client = DynamicClient()
        except RuntimeError as exc:
            message = str(exc)
            if "no configuration has been provided" in message:
                raise K8sConfigError(
                    "无法加载 Kubernetes 配置。请确认已在集群内运行（自动读取 in-cluster "
                    "config），或在集群外通过 KUBECONFIG 环境变量 / 默认 kubeconfig 文件 "
                    "提供有效的配置。"
                ) from exc
            raise
        self._apps = self._client.resource("apps/v1", "deployments")
        self._core = self._client.resource("v1", "secrets")
        logger.debug(
            "k8s client initialized",
            in_cluster=bool(os.environ.get("KUBERNETES_SERVICE_HOST")),
        )

    async def get_deployment_image_specs(self, namespace: str, deployment: str) -> list[str]:
        """Return every container image (init + regular) of a Deployment."""

        def _sync() -> list[str]:
            logger.debug("get deployment image specs", namespace=namespace, deployment=deployment)
            dep = self._apps.get(name=deployment, namespace=namespace)
            spec = dep["spec"]["template"]["spec"]
            images: list[str] = []
            for c in spec.get("initContainers") or []:
                if c.get("image"):
                    images.append(c["image"])
            for c in spec.get("containers") or []:
                if c.get("image"):
                    images.append(c["image"])
            logger.debug(
                "got deployment image specs",
                namespace=namespace,
                deployment=deployment,
                image_count=len(images),
            )
            return images

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001 - surface as debug for triage
            logger.debug(
                "get deployment image specs failed",
                namespace=namespace,
                deployment=deployment,
                error=str(exc),
            )
            raise

    async def get_image_pull_secrets(self, namespace: str, deployment: str) -> list[str]:
        """Return the PodSpec-level imagePullSecret names of a Deployment."""

        def _sync() -> list[str]:
            logger.debug("get imagePullSecrets", namespace=namespace, deployment=deployment)
            dep = self._apps.get(name=deployment, namespace=namespace)
            spec = dep["spec"]["template"]["spec"]
            secrets = spec.get("imagePullSecrets") or []
            names = [s["name"] for s in secrets] if secrets else []
            logger.debug(
                "got imagePullSecrets",
                namespace=namespace,
                deployment=deployment,
                secret_count=len(names),
            )
            return names

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001 - surface as debug for triage
            logger.debug(
                "get imagePullSecrets failed",
                namespace=namespace,
                deployment=deployment,
                error=str(exc),
            )
            raise

    async def read_secret_dockerconfigjson(self, namespace: str, name: str) -> dict[str, Any]:
        """Read a Secret and return the parsed ``.dockerconfigjson`` auths map."""

        def _sync() -> dict[str, Any]:
            logger.debug("read secret .dockerconfigjson", namespace=namespace, name=name)
            secret = self._core.get(name=name, namespace=namespace)
            data = secret.get("data") or {}
            raw = data.get(".dockerconfigjson")
            if not raw:
                raise KeyError(f"secret {namespace}/{name} has no .dockerconfigjson")
            decoded = base64.b64decode(raw).decode("utf-8")
            auths = json.loads(decoded).get("auths", {})
            logger.debug(
                "read secret .dockerconfigjson ok",
                namespace=namespace,
                name=name,
                host_count=len(auths),
            )
            return auths

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001 - surface as debug for triage
            logger.debug(
                "read secret .dockerconfigjson failed",
                namespace=namespace,
                name=name,
                error=str(exc),
            )
            raise

    async def patch_restart_annotation(self, namespace: str, deployment: str) -> None:
        """Trigger a rollout restart by patching the restartedAt annotation."""

        now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _sync() -> None:
            body = {"spec": {"template": {"metadata": {"annotations": {RESTART_ANNOTATION: now}}}}}
            logger.debug(
                "patch restart annotation",
                namespace=namespace,
                deployment=deployment,
                patch_type=PATCH_TYPE,
                body=body,
            )
            self._apps.patch(
                name=deployment,
                namespace=namespace,
                patch_type=PATCH_TYPE,
                patch_data=body,
            )

        await asyncio.to_thread(_sync)
        logger.info("triggered rollout restart", namespace=namespace, deployment=deployment)
