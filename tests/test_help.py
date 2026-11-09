"""The User Guide: that the menu reaches it, and every link in docs/ resolves."""

from __future__ import annotations

import re

import pytest
from windowchrome import heading_slugs
from windowchrome.markdowndialog import _WINDOWS, close_markdown_windows

from crenel.help import DOCS_DIR

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$", re.MULTILINE)


def documents():
    return sorted(DOCS_DIR.glob("*.md"))


@pytest.fixture(autouse=True)
def _close_help():
    yield
    close_markdown_windows()


def options_menu(window):
    for action in window.menuBar().actions():
        if action.text() == "&Options":
            return action.menu()
    raise AssertionError("no Options menu")


def test_the_guide_exists():
    assert (DOCS_DIR / "USER_GUIDE.md").is_file()


def test_every_relative_link_points_at_a_file_that_exists():
    missing = []
    for path in documents():
        for target in LINK.findall(path.read_text()):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (path.parent / target.split("#")[0]).exists():
                missing.append(f"{path.name} -> {target}")
    assert missing == []


def test_every_fragment_link_points_at_a_heading_that_exists():
    anchors = {
        path.name: set(heading_slugs([m.group(2) for m in HEADING.finditer(path.read_text())]))
        for path in documents()
    }
    broken = []
    for path in documents():
        for target in LINK.findall(path.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, fragment = target.partition("#")
            if fragment and fragment not in anchors.get(file_part or path.name, set()):
                broken.append(f"{path.name} -> {target}")
    assert broken == []


def test_the_options_menu(window):
    assert [a.text() for a in options_menu(window).actions()] == ["&Settings", "&User Guide"]


def test_user_guide_opens_and_closes_with_the_window(window):
    guide = options_menu(window).actions()[1]
    guide.trigger()
    guide.trigger()
    assert len(_WINDOWS) == 1
    dialog = next(iter(_WINDOWS.values()))
    assert dialog.windowTitle() == "Crenel — User Guide"
    assert "One-time VM setup" in dialog.view.document().toPlainText()
    window.close()
    assert _WINDOWS == {}
