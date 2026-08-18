"""deplowly: in-cluster Kubernetes image-watchdog."""

try:
    from importlib.metadata import version

    __version__ = version("deplowly")
except Exception:  # pragma: no cover - package not installed
    __version__ = "unknown"
