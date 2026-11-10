"""Starting the root helper through pkexec, without freezing the window.

`HelperRunner.start()` launches `pkexec <helper>` as a QProcess and writes the
request to its stdin. pkexec puts up the desktop's password dialog, and the
window keeps painting while it waits. When the helper exits, `finished`
carries a `protocol.HelperReply`.

Which helper runs:

- Installed from the .deb, it is `/usr/lib/crenel/crenel-helper`: root-owned,
  with a polkit action (`packaging/com.github.clayferguson.crenel.policy`)
  that names it, so the prompt says what Crenel is doing. The password is
  remembered for a few minutes (auth_admin_keep).
- Run from a checkout (`./start.sh`), it is `/usr/bin/python3 -I` importing
  `crenel.helper` from this tree, through pkexec's generic action. That asks
  every time. It is your own code, run as root after you authenticate.

Never a shell: the argv is a fixed list.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from .protocol import HelperReply, encode_request, parse_reply

INSTALLED_LIB = Path("/usr/lib/crenel")
INSTALLED_HELPER = INSTALLED_LIB / "crenel-helper"
PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIR.parent
SYSTEM_PYTHON = "/usr/bin/python3"

# What the system python runs in a checkout: put the tree on the path (argv[1])
# and call the helper. -I keeps PYTHONPATH and ~/.local out of a root process,
# and -B stops it writing root-owned .pyc files into the checkout.
BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from crenel.helper import main; sys.exit(main())"
)


def helper_argv() -> list[str] | None:
    """The command that runs the helper as root, or None without pkexec."""
    pkexec = shutil.which("pkexec")
    if pkexec is None:
        return None
    if PACKAGE_DIR.is_relative_to(INSTALLED_LIB) and os.path.exists(INSTALLED_HELPER):
        return [pkexec, str(INSTALLED_HELPER)]
    return [pkexec, SYSTEM_PYTHON, "-I", "-B", "-c", BOOTSTRAP, str(SOURCE_ROOT)]


class HelperRunner(QObject):
    """Runs one helper request at a time and reports the reply."""

    finished = pyqtSignal(object)  # HelperReply

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None

    @property
    def running(self) -> bool:
        return self._process is not None

    def start(self, request: dict) -> str | None:
        """Start the helper on `request`. An error message, or None."""
        if self._process is not None:
            return "Crenel is already applying a change."
        argv = helper_argv()
        if argv is None:
            return "pkexec is not installed, so Crenel cannot mount folders. (apt install pkexec)"
        process = QProcess(self)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start(argv[0], argv[1:])
        process.write(encode_request(request))
        process.closeWriteChannel()
        return None

    def _on_error(self, error: QProcess.ProcessError) -> None:
        # FailedToStart never reaches `finished`; every other error does.
        if error == QProcess.ProcessError.FailedToStart and self._process is not None:
            self._process = None
            self.finished.emit(HelperReply(error="pkexec could not be started."))

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        stdout = bytes(process.readAllStandardOutput().data())
        stderr = bytes(process.readAllStandardError().data())
        process.deleteLater()
        if status != QProcess.ExitStatus.NormalExit:
            reply = HelperReply(error="Crenel's helper crashed. Refresh to see what changed.")
        else:
            reply = parse_reply(exit_code, stdout, stderr)
        self.finished.emit(reply)

    def wait(self, msecs: int = 30000) -> None:
        """Block until a running helper exits (used when the window closes)."""
        if self._process is not None:
            self._process.waitForFinished(msecs)
