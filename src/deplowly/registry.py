from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
import structlog

logger = structlog.get_logger(__name__)

# repo@sha256:... -> digest is pinned, nothing to watch.
SHA256_IMAGE_RE = re.compile(r"@sha256:[0-9a-f]{64}$", re.IGNORECASE)

# Bearer realm="...",service="...",scope="..." -> {"realm": ..., ...}
WWW_AUTH_PARAM_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


@dataclass
class ImageRef:
    registry: str
    repo: str
    tag: str
    raw: str

    @property
    def is_pinned(self) -> bool:
        return bool(SHA256_IMAGE_RE.search(self.raw))


def parse_image(image: str) -> ImageRef:
    """Parse an image string into registry/repo/tag components.

    Examples::

        nginx:latest                       -> docker.io / library/nginx / latest
        registry.example.com/foo/bar:v1    -> registry.example.com / foo/bar / v1
    """
    raw = image
    # strip digest if present (caller should skip pinned images beforehand)
    if "@" in image:
        image = image.split("@", 1)[0]

    first = image.split("/", 1)[0]
    if "/" in image and ("." in first or ":" in first or image.startswith("localhost")):
        registry_host, remainder = image.split("/", 1)
    else:
        registry_host, remainder = "docker.io", image

    if registry_host == "docker.io" and "/" not in remainder:
        remainder = f"library/{remainder}"

    if ":" in remainder:
        repo, tag = remainder.rsplit(":", 1)
    else:
        repo, tag = remainder, "latest"

    return ImageRef(registry=registry_host, repo=repo, tag=tag, raw=raw)


def _registry_scheme_host(registry: str) -> str:
    if registry in ("docker.io",):
        # docker.io v2 API is served via registry-1.docker.io
        return "https://registry-1.docker.io"
    return f"https://{registry}"


def _extract_creds(auth: Mapping[str, object] | None) -> tuple[str, str] | None:
    """Extract (username, password) from a dockerconfigjson auth entry.

    Entries may carry explicit username/password fields (kubectl style), or
    the combined base64 "auth" field written by `docker login`. Returns None
    when the entry carries no usable credentials.
    """
    if not auth:
        return None
    username = str(auth.get("username") or "")
    password = str(auth.get("password") or "")
    if username or password:
        return username, password
    b64 = auth.get("auth")
    if isinstance(b64, str) and b64:
        try:
            decoded = base64.b64decode(b64).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            logger.warning("failed to decode docker config auth field")
            return None
        user, _, pwd = decoded.partition(":")
        if user or pwd:
            return user, pwd
    return None


def _parse_www_authenticate(header: str) -> dict[str, str]:
    """Parse a WWW-Authenticate challenge into its parameters.

    Example::

        Bearer realm="https://.../auth",service="...",scope="..."  ->
        {"realm": ..., "service": ..., "scope": ...}
    """
    return {k: v for k, v in WWW_AUTH_PARAM_RE.findall(header)}


def _head_manifest(
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    bearer: str | None = None,
    basic: tuple[str, str] | None = None,
) -> requests.Response:
    req_headers = dict(headers)
    if bearer:
        req_headers["Authorization"] = f"Bearer {bearer}"
    return session.head(url, headers=req_headers, timeout=15.0, auth=basic)


def _fetch_token(
    session: requests.Session,
    challenge: str,
    base: str,
    creds: tuple[str, str] | None,
    ref: ImageRef,
) -> str | None:
    """Resolve a bearer token following the Docker Registry v2 token flow.

    Extracts the realm / service / scope from the WWW-Authenticate challenge,
    requests a token from the realm (sending basic credentials when
    available), and returns the token on success. Returns None when the
    challenge has no realm or the token request fails.
    """
    params = _parse_www_authenticate(challenge)
    realm = params.get("realm")
    if not realm:
        logger.warning("WWW-Authenticate challenge has no realm", registry=ref.registry, challenge=challenge)
        return None
    if realm.startswith("/"):
        realm = urljoin(base, realm)
    query = {
        "service": params.get("service", ref.registry),
        "scope": params.get("scope", f"repository:{ref.repo}:pull"),
    }
    try:
        resp = session.get(realm, params=query, timeout=15.0, auth=creds)
        resp.raise_for_status()
        payload = resp.json()
        token = payload.get("token") or payload.get("access_token")
        if not token:
            logger.error("token response has no token field", registry=ref.registry, realm=realm)
            return None
        return token
    except requests.RequestException as exc:
        logger.error("token fetch failed", registry=ref.registry, realm=realm, error=str(exc))
        return None


def get_remote_digest(
    image: str,
    auths: dict[str, dict[str, str]] | None = None,
) -> str | None:
    """Resolve the ``Docker-Content-Digest`` for an image reference.

    Returns the digest string (``sha256:...``) or ``None`` on unrecoverable
    failure. Callers should treat ``None`` conservatively (do not restart).
    """
    ref = parse_image(image)
    if ref.is_pinned:
        logger.warning("image is digest-pinned, skipping", image=image)
        return None

    auths = auths or {}
    base = _registry_scheme_host(ref.registry)
    auth = auths.get(ref.registry) or auths.get(_normalize_registry(ref.registry))
    creds = _extract_creds(auth) if auth else None
    logger.debug(
        "registry auth lookup",
        image=image,
        registry=ref.registry,
        creds_found=bool(creds),
    )

    headers: dict[str, str] = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}
    manifest_url = f"{base}/v2/{ref.repo}/manifests/{ref.tag}"

    with requests.Session() as session:
        try:
            resp = _head_manifest(session, manifest_url, headers, basic=creds)
            if resp.status_code == 401:
                challenge = resp.headers.get("WWW-Authenticate", "")
                logger.debug("registry returned 401 challenge", image=image, challenge=challenge)
                scheme = challenge.split(" ", 1)[0].strip().lower() if challenge else ""
                match scheme:
                    case "bearer":
                        token = _fetch_token(session, challenge, base, creds, ref)
                        if token:
                            resp = _head_manifest(session, manifest_url, headers, bearer=token)
                        else:
                            logger.warning(
                                "could not obtain bearer token, keeping original 401 response",
                                image=image,
                            )
                    case "basic":
                        if not creds:
                            logger.warning(
                                "registry requires basic auth but no credentials available",
                                image=image,
                            )
                    case "":
                        logger.warning("registry returned 401 without WWW-Authenticate", image=image)
                    case _:
                        logger.warning("unsupported auth challenge scheme", image=image, scheme=scheme)
            if resp.status_code == 401 and "insufficient_scope" in resp.headers.get("WWW-Authenticate", ""):
                logger.error(
                    "registry authentication failed (insufficient_scope): imagePullSecret credentials "
                    "missing, wrong, or lacking pull access",
                    image=image,
                    registry=ref.registry,
                )
                return None
            resp.raise_for_status()
            digest = resp.headers.get("Docker-Content-Digest")
            if not digest:
                logger.error("registry returned no digest", image=image, status=resp.status_code)
                return None
            return digest
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            logger.error("registry http error", image=image, status=status)
            return None
        except requests.RequestException as exc:
            logger.error("registry request failed", image=image, error=str(exc))
            return None


def _normalize_registry(registry: str) -> str:
    """Map short hostnames to canonical forms for auth lookup."""
    if registry in ("docker.io", "registry-1.docker.io"):
        return "https://index.docker.io/v1/"
    return registry
