from __future__ import annotations

import asyncio
import base64

import pytest
import requests

from deplowly.registry import (
    _extract_creds,
    _parse_www_authenticate,
    get_remote_digest,
    parse_image,
)

CHALLENGE = (
    'Bearer realm="https://dockerauth.cn-hangzhou.aliyuncs.com/auth",'
    'service="registry.aliyuncs.com:cn-shenzhen:26842",'
    'scope="repository:eaphone-test/yf-crm:pull"'
)

# scripted fake state, reset by the autouse fixture
calls: list[tuple[object, ...]] = []
second_status = 200


@pytest.fixture(autouse=True)
def _reset_script() -> None:
    global second_status
    calls.clear()
    second_status = 200


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


def test_extract_creds_auth_base64() -> None:
    entry = {"auth": base64.b64encode(b"user:pass").decode()}
    assert _extract_creds(entry) == ("user", "pass")


def test_extract_creds_explicit_fields() -> None:
    assert _extract_creds({"username": "u", "password": "p"}) == ("u", "p")


def test_extract_creds_empty() -> None:
    assert _extract_creds({}) is None
    assert _extract_creds(None) is None


def test_parse_www_authenticate() -> None:
    params = _parse_www_authenticate(CHALLENGE)
    assert params["realm"] == "https://dockerauth.cn-hangzhou.aliyuncs.com/auth"
    assert params["service"] == "registry.aliyuncs.com:cn-shenzhen:26842"
    assert params["scope"] == "repository:eaphone-test/yf-crm:pull"


class FakeResp:
    def __init__(self, status_code: int, headers: dict[str, str], json_body: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers
        self._json = json_body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}", response=self)

    def json(self) -> dict:
        return self._json


class FakeSession:
    """Scripted session: first manifest HEAD 401s, then token GET, then HEAD."""

    def __init__(self, *a: object, **k: object) -> None:
        self.manifest_calls = 0

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def head(self, url: str, headers: dict | None = None, timeout: float = 0, auth: object = None) -> FakeResp:
        calls.append(("head", url, headers, auth))
        self.manifest_calls += 1
        if self.manifest_calls == 1:
            return FakeResp(401, {"WWW-Authenticate": CHALLENGE})
        if second_status == 401:
            return FakeResp(401, {"WWW-Authenticate": CHALLENGE + ',error="insufficient_scope"'})
        return FakeResp(200, {"Docker-Content-Digest": "sha256:deadbeef"})

    def get(self, url: str, params: dict | None = None, timeout: float = 0, auth: object = None) -> FakeResp:
        calls.append(("get", url, params, auth))
        return FakeResp(200, {}, {"token": "t0ken"})


@pytest.fixture
def fake_session() -> None:
    import deplowly.registry as reg

    orig = reg.requests.Session
    reg.requests.Session = FakeSession  # type: ignore[assignment]
    yield
    reg.requests.Session = orig  # type: ignore[assignment]


async def test_get_remote_digest_bearer_flow(fake_session: None) -> None:
    img = "registry.cn-shenzhen.aliyuncs.com/eaphone-test/yf-crm:latest"
    auths = {"registry.cn-shenzhen.aliyuncs.com": {"username": "u", "password": "p"}}

    digest = await asyncio.to_thread(get_remote_digest, img, auths)

    assert digest == "sha256:deadbeef"
    assert [c[0] for c in calls] == ["head", "get", "head"]
    token_call = calls[1]
    assert token_call[1] == "https://dockerauth.cn-hangzhou.aliyuncs.com/auth"
    assert token_call[2] == {
        "service": "registry.aliyuncs.com:cn-shenzhen:26842",
        "scope": "repository:eaphone-test/yf-crm:pull",
    }
    assert token_call[3] == ("u", "p")
    retry_headers = calls[2][2]
    assert retry_headers["Authorization"] == "Bearer t0ken"


async def test_get_remote_digest_insufficient_scope(fake_session: None) -> None:
    global second_status
    second_status = 401
    img = "registry.cn-shenzhen.aliyuncs.com/eaphone-test/yf-crm:latest"
    auths = {"registry.cn-shenzhen.aliyuncs.com": {"username": "u", "password": "p"}}

    assert await asyncio.to_thread(get_remote_digest, img, auths) is None


async def test_get_remote_digest_success() -> None:
    img = "registry.example.com/foo/bar:latest"

    class OkResp:
        status_code = 200
        headers = {"Docker-Content-Digest": "sha256:deadbeef"}

        def raise_for_status(self) -> None:
            pass

    class OkSession:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> OkSession:
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def head(
            self,
            url: str,
            headers: dict | None = None,
            timeout: float = 0,
            auth: object = None,
        ) -> OkResp:
            calls.append(("head", url, headers, auth))
            return OkResp()

    import deplowly.registry as reg

    orig = reg.requests.Session
    reg.requests.Session = OkSession  # type: ignore[assignment]
    try:
        digest = await asyncio.to_thread(get_remote_digest, img)
    finally:
        reg.requests.Session = orig  # type: ignore[assignment]
    assert digest == "sha256:deadbeef"
    assert calls and "/v2/foo/bar/manifests/latest" in str(calls[0][1])


async def test_get_remote_digest_failure_returns_none() -> None:
    img = "registry.example.com/foo/bar:latest"

    class ErrResp:
        status_code = 500
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            raise requests.HTTPError("boom", response=self)  # type: ignore[arg-type]

    class ErrSession:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> ErrSession:
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def head(
            self,
            url: str,
            headers: dict | None = None,
            timeout: float = 0,
            auth: object = None,
        ) -> ErrResp:
            return ErrResp()

    import deplowly.registry as reg

    orig = reg.requests.Session
    reg.requests.Session = ErrSession  # type: ignore[assignment]
    try:
        assert await asyncio.to_thread(get_remote_digest, img) is None
    finally:
        reg.requests.Session = orig  # type: ignore[assignment]
