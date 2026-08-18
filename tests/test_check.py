from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from deplowly.check import describe, main
from deplowly.config import load_config

GOOD = """
default_interval: 60
targets:
  - namespace: prod
    deployment: web
  - namespace: prod
    deployment: worker
    interval: 120
"""

BAD = """
default_interval: 60
targets:
  - namespace: prod
"""


def _check_from_stdin(monkeypatch: pytest.MonkeyPatch, content: str, capsys: pytest.CaptureFixture[str]) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(content))
    code = main(["-"])
    capsys.readouterr()  # 清空输出，后续按需断言
    return code


def test_check_ok_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(GOOD, encoding="utf-8")
    assert main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "config OK" in out
    assert "prod/web every 60s" in out
    assert "prod/worker every 120s" in out


def test_check_ok_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(GOOD))
    assert main(["-"]) == 0
    assert "config OK" in capsys.readouterr().out


def test_check_bad_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code = _check_from_stdin(monkeypatch, BAD, capsys)
    assert code == 1


def test_check_bad_stdin_message(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(BAD))
    assert main(["-"]) == 1
    err = capsys.readouterr().err
    assert "config error" in err


def test_check_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "nope.yaml")]) == 1
    assert "config error" in capsys.readouterr().err


def test_describe_uses_default_interval(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(GOOD, encoding="utf-8")
    cfg = load_config(str(p))
    assert "every 60s" in describe(cfg)
