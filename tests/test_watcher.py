from __future__ import annotations

from unittest.mock import patch

from deplowly.models import Target
from deplowly.state import State
from deplowly.watcher import _aggregate, _check_target

TARGET = Target(namespace="prod", deployment="web")


class FakeK8s:
    def __init__(
        self,
        images: list[str],
        secrets: list[str] | None = None,
        digests: dict[str, str] | None = None,
    ):
        self._images = images
        self._secrets = secrets or []
        self._digests = digests or {}
        self.restarted: list[tuple[str, str]] = []

    async def get_deployment_image_specs(self, ns: str, dep: str) -> list[str]:
        return self._images

    async def get_image_pull_secrets(self, ns: str, dep: str) -> list[str]:
        return self._secrets

    async def read_secret_dockerconfigjson(self, ns: str, name: str) -> dict:
        return {}

    async def patch_restart_annotation(self, ns: str, dep: str) -> None:
        self.restarted.append((ns, dep))


def _mock_digest(k8s: FakeK8s):
    """Patch registry.get_remote_digest to return digests from FakeK8s."""

    def _fake(image: str, auths=None) -> str | None:
        return k8s._digests.get(image)

    return patch("deplowly.watcher.get_remote_digest", new=_fake)


async def test_baseline_recorded_no_restart() -> None:
    k8s = FakeK8s(images=["nginx:latest"], digests={"nginx:latest": "sha256:aaa"})
    state = State()
    with _mock_digest(k8s):
        await _check_target(k8s, TARGET, state)
    assert state.has("prod/web")
    assert k8s.restarted == []


async def test_digest_change_triggers_restart() -> None:
    k8s = FakeK8s(images=["nginx:latest"], digests={"nginx:latest": "sha256:aaa"})
    state = State()
    with _mock_digest(k8s):
        await _check_target(k8s, TARGET, state)
        k8s._digests = {"nginx:latest": "sha256:bbb"}
        await _check_target(k8s, TARGET, state)
    assert k8s.restarted == [("prod", "web")]


async def test_no_restart_when_unchanged() -> None:
    k8s = FakeK8s(images=["nginx:latest"], digests={"nginx:latest": "sha256:aaa"})
    state = State()
    with _mock_digest(k8s):
        await _check_target(k8s, TARGET, state)
        await _check_target(k8s, TARGET, state)
    assert k8s.restarted == []


async def test_pinned_image_stable() -> None:
    pinned = "registry.example.com/foo/bar@sha256:" + "a" * 64
    k8s = FakeK8s(images=[pinned])
    state = State()
    with _mock_digest(k8s):
        await _check_target(k8s, TARGET, state)
        await _check_target(k8s, TARGET, state)
    assert k8s.restarted == []


async def test_registry_failure_skips_round() -> None:
    k8s = FakeK8s(images=["nginx:latest"], digests={})  # no digest -> None

    def _none(image: str, auths=None) -> str | None:
        return None

    state = State()
    with patch("deplowly.watcher.get_remote_digest", new=_none):
        await _check_target(k8s, TARGET, state)
    assert not state.has("prod/web")
    assert k8s.restarted == []


async def test_multiple_containers_aggregated() -> None:
    k8s = FakeK8s(
        images=["nginx:latest", "redis:7"],
        digests={"nginx:latest": "sha256:aaa", "redis:7": "sha256:ccc"},
    )
    state = State()
    with _mock_digest(k8s):
        await _check_target(k8s, TARGET, state)
        k8s._digests = {"nginx:latest": "sha256:aaa", "redis:7": "sha256:ddd"}
        await _check_target(k8s, TARGET, state)
    assert k8s.restarted == [("prod", "web")]


def test_aggregate_deterministic() -> None:
    a = _aggregate([("x", "sha256:1"), ("y", "sha256:2")])
    b = _aggregate([("y", "sha256:2"), ("x", "sha256:1")])
    assert a == b
