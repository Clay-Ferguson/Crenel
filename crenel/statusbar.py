"""The status bar: what is shared, and whether the helper is still working.

A message on the left with a text spinner in front of it, which turns (and
the bar goes green) while an Apply is waiting on the password prompt or the
helper. DupFinder's ScanStatusBar, renamed.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel, QStatusBar, QWidget

from .style import STATUS_BAR_MARGINS, mono_font, status_style

SPINNER_FRAMES = "|/-\\"
SPINNER_INTERVAL_MS = 120
STATUS_READY = "Ready"


class BusyStatusBar(QStatusBar):
    """The window's status bar, with a spinner and a busy state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spinner_frame = 0
        self._busy = False

        self.spinner = QLabel()
        self.spinner.setFont(mono_font())
        self.spinner.setFixedWidth(self.spinner.fontMetrics().horizontalAdvance("M"))
        self.message = QLabel(STATUS_READY)

        self.setSizeGripEnabled(False)
        self.setContentsMargins(*STATUS_BAR_MARGINS)
        self.addWidget(self.spinner)
        self.addWidget(self.message, 1)
        self.setStyleSheet(status_style(False))

        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(SPINNER_INTERVAL_MS)
        self._spinner_timer.timeout.connect(self._tick)

    def _set_busy(self, busy: bool) -> None:
        # Idempotent, so repeated status updates don't restart the spinner.
        if busy == self._busy:
            return
        self._busy = busy
        self.setStyleSheet(status_style(busy))
        if busy:
            self._spinner_frame = 0
            self.spinner.setText(SPINNER_FRAMES[0])
            self._spinner_timer.start()
        else:
            self._spinner_timer.stop()
            self.spinner.clear()

    def _tick(self) -> None:
        self._spinner_frame = (self._spinner_frame + 1) % len(SPINNER_FRAMES)
        self.spinner.setText(SPINNER_FRAMES[self._spinner_frame])

    def set_status(self, message: str, busy: bool = False) -> None:
        self.message.setText(message)
        self._set_busy(busy)

    @property
    def busy(self) -> bool:
        return self._busy

    def stop(self) -> None:
        """Stop the spinner for good, as the window closes."""
        self._spinner_timer.stop()
