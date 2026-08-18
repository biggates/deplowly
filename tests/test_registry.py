from __future__ import annotations

import asyncio

from deplowly.registry import get_remote_digest, parse_image


def test_parse_image_docker_hub_default() -> None:
    ref = parse_image("nginx:latest")
    assert ref.registry == "docker.io"
    assert ref.repo == "library/nginx"
    assert ref.tag == "latest"
    assert not ref.is_pinned


def test_parse_image_with_registry() -> None:
    ref = parse_image("registry.example.com/foo/bar:v1")
    assert ref.registry == "registry.example.com"
    assert ref.repo == "foo/bar"
    assert ref.tag == "v1"


def test_parse_image_pinned() -> None:
    img = "registry.example.com/foo/bar@sha256:" + "a" * 64
    ref = parse_image(img)
    assert ref.is_pinned


async def test_get_remote_digest_pinned_is_none() -> None:
    img = "registry.example.com/foo/bar@sha256:" + "a" * 64
    assert get_remote_digest(img) is None


async def test_get_remote_digest_success() -> None:
    img = "registry.example.com/foo/bar:latest"
    calls: list[object] = []

    class FakeResp:
        status_code = 200
        headers = {"Docker-Content-Digest": "sha256:deadbeef"}

        def raise_for_status(self) -> None:
            pass

    class FakeSession:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def head(
            self,
            url: str,
            headers: dict | None = None,
            timeout: float = 0,
            auth: object = None,
        ) -> FakeResp:
            calls.append(url)
            return FakeResp()

    import deplowly.registry as reg

    orig = reg.requests.Session
    reg.requests.Session = FakeSession  # type: ignore[assignment]
    try:
        digest = await asyncio.to_thread(get_remote_digest, img)
    finally:
        reg.requests.Session = orig  # type: ignore[assignment]
    assert digest == "sha256:deadbeef"
    assert calls and "/v2/foo/bar/manifests/latest" in calls[0]


async def test_get_remote_digest_failure_returns_none() -> None:
    img = "registry.example.com/foo/bar:latest"

    class FakeResp:
        status_code = 500
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            import requests

            raise requests.HTTPError("boom", response=self)  # type: ignore[arg-type]

    class FakeSession:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def head(
            self,
            url: str,
            headers: dict | None = None,
            timeout: float = 0,
            auth: object = None,
        ) -> FakeResp:
            return FakeResp()

    import deplowly.registry as reg

    orig = reg.requests.Session
    reg.requests.Session = FakeSession  # type: ignore[assignment]
    try:
        assert await asyncio.to_thread(get_remote_digest, img) is None
    finally:
        reg.requests.Session = orig  # type: ignore[assignment]
