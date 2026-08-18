from __future__ import annotations

import re
from dataclasses import dataclass

import requests
import structlog

logger = structlog.get_logger(__name__)

# repo@sha256:... -> digest is pinned, nothing to watch.
SHA256_IMAGE_RE = re.compile(r"@sha256:[0-9a-f]{64}$", re.IGNORECASE)


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

    headers: dict[str, str] = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}
    creds: tuple[str, str] | None = None
    if auth:
        creds = (str(auth.get("username", "")), str(auth.get("password", "")))

    with requests.Session() as session:
        manifest_url = f"{base}/v2/{ref.repo}/manifests/{ref.tag}"
        try:
            resp = session.head(manifest_url, headers=headers, timeout=15.0, auth=creds)
            if resp.status_code == 401 and not creds:
                token = _fetch_token(session, base, ref)
                if token:
                    resp = session.head(
                        manifest_url,
                        headers={**headers, "Authorization": f"Bearer {token}"},
                        timeout=15.0,
                    )
            resp.raise_for_status()
            digest = resp.headers.get("Docker-Content-Digest")
            if not digest:
                logger.error("registry returned no digest", image=image, status=resp.status_code)
                return None
            return digest
        except requests.HTTPError as exc:
            logger.error(
                "registry http error",
                image=image,
                status=exc.response.status_code,  # type: ignore[union-attr]
            )
            return None
        except requests.RequestException as exc:
            logger.error("registry request failed", image=image, error=str(exc))
            return None


def _fetch_token(session: requests.Session, base: str, ref: ImageRef) -> str | None:
    """Fetch a bearer token using the v2 token endpoint."""
    params = {
        "service": ref.registry,
        "scope": f"repository:{ref.repo}:pull",
    }
    try:
        resp = session.get(f"{base}/v2/token", params=params, timeout=15.0)
        resp.raise_for_status()
        return resp.json().get("token")
    except requests.RequestException as exc:
        logger.error("token fetch failed", registry=ref.registry, error=str(exc))
        return None


def _normalize_registry(registry: str) -> str:
    """Map short hostnames to canonical forms for auth lookup."""
    if registry in ("docker.io", "registry-1.docker.io"):
        return "https://index.docker.io/v1/"
    return registry
