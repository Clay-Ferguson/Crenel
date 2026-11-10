"""Shared fixtures: a private config, dialog capture, a fake helper runner.

Every tree is built in pytest's `tmp_path`. Nothing here mounts anything: the
window is given a `FakeRunner` in place of pkexec, and the real mounts are
exercised only by test_helper_namespace.py, inside a throwaway user+mount
namespace.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from crenel import config  # noqa: E402
from crenel.protocol import HelperReply  # noqa: E402


@pytest.fixture
def projects(tmp_path):
    """A projects folder with three projects (one a git repo), and a share folder.

    Returns (root, share) as real paths. The share folder does not exist
    yet: Crenel creates it.
    """
    root = tmp_path / "projects"
    for name in ("ABC", "DEF", "ghi"):
        (root / name).mkdir(parents=True)
    (root / "ABC" / ".git" / "hooks").mkdir(parents=True)
    (root / "ABC" / ".git" / "config").write_text("[core]\n")
    (root / ".hidden").mkdir()
    (root / "notes.txt").write_text("not a project")
    os.symlink(root / "DEF", root / "link-to-DEF")
    return os.path.realpath(root), os.path.realpath(tmp_path / "vm-share")


@pytest.fixture
def conf(tmp_path_factory, projects):
    """A config file of this test's own, pointing at `projects`.

    `config.CONFIG_PATH` is read at call time, so rebinding it keeps every
    test off the real ~/.config. Returns a function to save other settings.
    """
    original = config.CONFIG_PATH
    config.CONFIG_PATH = str(tmp_path_factory.mktemp("config") / "crenel-config.yaml")
    root, share = projects

    def save(projects_root=root, share_dir=share, protected=(".git",)):
        error = config.save_settings(
            config.Settings(projects_root=projects_root, share_dir=share_dir, protected=protected)
        )
        assert error is None
        return config.CONFIG_PATH

    save()
    yield save
    config.CONFIG_PATH = original


class _Dialogs(list):
    """The (title, message) of every dialog shown, and the answer to a question."""

    answer = QMessageBox.StandardButton.No


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    """Catch every QMessageBox instead of showing it (one would hang offscreen).

    A `question` is answered with `shown.answer`: No unless a test says Yes.
    """
    shown = _Dialogs()

    def capture(_parent, title, message, *args, **kwargs):
        shown.append((title, message))
        return QMessageBox.StandardButton.Ok

    def ask(_parent, title, message, *args, **kwargs):
        shown.append((title, message))
        return shown.answer

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(capture))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(capture))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(capture))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(ask))
    return shown


class FakeRunner(QObject):
    """Stands in for `privilege.HelperRunner`: records requests, replies on demand.

    `replies` is a queue of HelperReply objects (or callables taking the
    request and returning one). With `hold` set, start() leaves the request
    running until `finish()` is called, the way pkexec waits on a password.
    """

    finished = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[dict] = []
        self.replies: list = []
        self.hold = False
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, request: dict) -> str | None:
        self.requests.append(request)
        self._running = True
        if not self.hold:
            self.finish()
        return None

    def finish(self) -> None:
        request = self.requests[-1]
        reply = self.replies.pop(0) if self.replies else HelperReply(results=())
        if callable(reply):
            reply = reply(request)
        self._running = False
        self.finished.emit(reply)

    def wait(self, msecs: int = 0) -> None:
        pass


def ok_results(request: dict) -> HelperReply:
    """A reply saying every change in `request` worked."""
    return HelperReply(
        results=tuple(
            {"name": c["name"], "mode": c["mode"], "ok": True, "busy": False,
             "error": "", "warning": ""}
            for c in request["changes"]
        )
    )


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def window(qtbot, conf, runner):
    from crenel.window import MainWindow

    win = MainWindow(runner=runner)
    qtbot.addWidget(win)
    return win
