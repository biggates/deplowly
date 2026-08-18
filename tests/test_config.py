from __future__ import annotations

from pathlib import Path

import pytest

from deplowly.config import ConfigError, load_config

GOOD = """
default_interval: 60
targets:
  - namespace: prod
    deployment: web
  - namespace: prod
    deployment: worker
    interval: 120
"""

MISSING_INTERVAL = """
targets:
  - namespace: prod
    deployment: web
"""

BAD_INTERVAL = """
default_interval: 60
targets:
  - namespace: prod
    deployment: web
    interval: 0
"""

NO_TARGETS = """
default_interval: 60
"""

MISSING_FIELD = """
default_interval: 60
targets:
  - namespace: prod
"""


def _write(tmp_path: Path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_load_config_ok(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, GOOD))
    assert cfg.default_interval == 60
    assert len(cfg.targets) == 2
    assert cfg.targets[1].interval == 120
    assert cfg.targets[0].interval is None
    assert cfg.targets[0].key == "prod/web"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "nope.yaml"))


def test_missing_interval(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, MISSING_INTERVAL))


def test_bad_interval(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, BAD_INTERVAL))


def test_no_targets(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, NO_TARGETS))


def test_missing_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, MISSING_FIELD))
