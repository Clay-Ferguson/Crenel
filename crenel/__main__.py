"""Entry point: python -m crenel"""

from __future__ import annotations

import argparse
import sys
import traceback

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from . import APP_NAME, UI_POINT_SIZE, messages
from .config import ensure_config
from .style import tune_palette
from .window import MainWindow

TRACEBACK_LINES = 12


def _report_unhandled(kind, value, trace) -> None:
    """Show an exception that escaped a slot instead of letting PyQt abort.

    With no `sys.excepthook` installed, PyQt6 kills the process on an
    exception in a slot. That would show nothing at all when Crenel is
    started from a desktop icon.
    """
    text = "".join(traceback.format_exception(kind, value, trace))
    if sys.__stderr__ is not None:
        sys.__stderr__.write(text)
    if QApplication.instance() is None:
        return
    tail = "\n".join(text.rstrip().splitlines()[-TRACEBACK_LINES:])
    messages.critical(
        None,
        f"{APP_NAME} — internal error",
        f"Something went wrong inside {APP_NAME}. The window should still work.\n\n{tail}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="crenel", description="Choose which project folders a VM can see."
    )
    parser.parse_args()

    app = QApplication(sys.argv)
    sys.excepthook = _report_unhandled
    app.setApplicationName(APP_NAME)
    # Ties the window to crenel.desktop (dock icon, alt-tab name). As in
    # DupFinder, applicationDisplayName is not set, so titles aren't doubled.
    app.setDesktopFileName("crenel")

    font = app.font()
    font.setPointSize(UI_POINT_SIZE)
    app.setFont(font)
    tune_palette(app)

    ensure_config()

    window = MainWindow()
    window.show()
    window.activateWindow()
    window.raise_()

    # First run (or a folder that has gone): straight to Settings, over the
    # window, which says what is missing in its status bar meanwhile.
    if window.needs_setup():
        QTimer.singleShot(0, window.open_settings)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
