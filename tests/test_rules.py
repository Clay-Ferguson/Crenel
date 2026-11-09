"""AGENTS.md rules that can be checked by reading the source.

The source is parsed rather than grepped, so a mention in a comment or a
docstring is not a hit.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "crenel"
SOURCES = sorted(PACKAGE.glob("*.py"))

# Modules the root helper loads, or the window uses without a display. They
# must import nothing but the standard library (and PyYAML, for config).
NON_WIDGET = ["__init__", "shares", "helper", "protocol", "config"]
# What the root helper itself may load: the standard library and these.
HELPER_CLOSURE = {"crenel", "crenel.shares", "crenel.helper"}


def _calls(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            yield node


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return f"{_name(func.value)}.{func.attr}"
    return ""


def test_the_package_has_sources():
    assert len(SOURCES) > 8


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_every_button_comes_from_style(path):
    if path.name == "style.py":
        return
    built = [call.lineno for call in _calls(path) if _name(call.func).endswith("QPushButton")]
    assert built == [], f"QPushButton(...) built directly at {path.name} lines {built}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_nothing_runs_through_a_shell(path):
    offenders = []
    for call in _calls(path):
        if _name(call.func) in ("os.system", "os.popen"):
            offenders.append(call.lineno)
        for keyword in call.keywords:
            shell_on = not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            )
            if keyword.arg == "shell" and shell_on:
                offenders.append(call.lineno)
    assert offenders == [], f"shell use in {path.name} at lines {offenders}"


def test_the_helper_runs_no_programs():
    """The helper makes system calls itself; it never spawns anything."""
    source = (PACKAGE / "helper.py").read_text()
    tree = ast.parse(source)
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "subprocess" not in imported
    assert not [c for c in _calls(PACKAGE / "helper.py") if _name(c.func).startswith("os.exec")]


def test_the_non_widget_modules_never_import_qt():
    """And the helper loads nothing from crenel beyond shares."""
    code = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        f"[__import__('crenel.' + m if m != '__init__' else 'crenel') for m in {NON_WIDGET!r}];"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in "
        "('PyQt6', 'crenel', 'windowchrome'))))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", code, str(REPO)], capture_output=True, text=True, check=True
    ).stdout
    assert "PyQt6" not in loaded and "windowchrome" not in loaded

    helper_only = (
        "import sys, json; sys.path.insert(0, sys.argv[1]); import crenel.helper;"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('crenel'))))"
    )
    loaded = subprocess.run(
        [sys.executable, "-I", "-c", helper_only, str(REPO)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert set(__import__("json").loads(loaded)) == HELPER_CLOSURE
