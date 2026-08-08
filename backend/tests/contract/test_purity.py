"""Prove slackline.core and slackline.schedule.index are pure.

Imports them with sockets monkeypatched to raise, then verifies that no
network module, no source adapter, and no llm module was pulled in. This is
the CI teeth behind the boundary rule: Scheduler, Auditor, Navigator and the
ScheduleIndex loader run keyless and offline, always.
"""

import pathlib
import socket
import sys

import pytest

_SLACKLINE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "slackline"


def _pure_module_names() -> list[str]:
    """Every module in slackline/core plus slackline/schedule/index.py."""
    names = [
        f"slackline.core.{p.stem}"
        for p in sorted((_SLACKLINE_ROOT / "core").glob("*.py"))
        if p.stem != "__init__"
    ]
    if (_SLACKLINE_ROOT / "schedule" / "index.py").exists():
        names.append("slackline.schedule.index")
    return names


CORE_MODULES = _pure_module_names()

FORBIDDEN_IMPORTS = [
    "slackline.sources",
    "slackline.llm",
    "requests",
    "httpx",
    "urllib.request",
    "aiohttp",
]


def _boom(*args, **kwargs):  # pragma: no cover - must never run
    raise AssertionError("network access attempted from a pure module")


def test_core_imports_with_sockets_disabled(monkeypatch):
    # Drop any previously imported slackline modules so import side effects
    # re-run under the socket ban.
    for name in list(sys.modules):
        if name.startswith("slackline"):
            del sys.modules[name]

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    for mod in CORE_MODULES:
        __import__(mod)

    for forbidden in FORBIDDEN_IMPORTS:
        assert forbidden not in sys.modules, (
            f"pure module chain imported {forbidden}"
        )


def test_core_does_not_import_sources_or_llm_statically():
    """Static check independent of import order: core files never mention
    the network packages."""
    pure_files = list((_SLACKLINE_ROOT / "core").glob("*.py"))
    index_py = _SLACKLINE_ROOT / "schedule" / "index.py"
    if index_py.exists():
        pure_files.append(index_py)
    import re

    banned_pattern = re.compile(
        r"^\s*(?:import|from)\s+(slackline\.sources|slackline\.llm|requests"
        r"|httpx|urllib\.request|aiohttp|socket)\b",
        re.MULTILINE,
    )
    assert pure_files, "expected pure modules to exist"
    for f in pure_files:
        text = f.read_text(encoding="utf-8")
        match = banned_pattern.search(text)
        assert match is None, f"{f.name} imports {match.group(1) if match else ''}"
