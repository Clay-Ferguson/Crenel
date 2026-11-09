"""Shared look: the button styling, the palette, the font. DupFinder's, trimmed.

Kept apart from `window.py` so the settings dialog can match the main
window's controls without importing it. Nothing here imports from the rest of
the package except the point size, which keeps it at the bottom of the
import graph.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication, QPushButton

from . import UI_POINT_SIZE

# How far the window surface is lightened away from the panes on it (a
# QColor.lighter() percentage), and the separation below which two surfaces
# read as one. See `tune_palette`.
WINDOW_LIGHTEN = 140
MIN_SEPARATION = 6

# PRIMARY is a muted green for the main action (Apply, Save). SECONDARY is
# the neutral gray of anything beside one. DANGER is the brick red of Hide All,
# which takes access away from everything at once.
PRIMARY_BUTTON_BG = "#4a6f45"
SECONDARY_BUTTON_BG = "#5a5a5a"
DANGER_BUTTON_BG = "#8a3b36"
BUTTON_FG = "#f0f2ef"

# A row whose choice has not been applied yet, and a row in conflict. Amber
# and red, readable on a light theme and a dark one.
PENDING_FG = "#d49a2a"
CONFLICT_FG = "#d0574f"

ACTION_BUTTON_PADDING = "8px 24px"
CONTROL_BAR_PADDING = "5px 16px"

# The widest label on a uniform button, which sets the size the others match.
ACTION_BUTTON_TEXT = "Cancel"

MENU_BAR_ITEM_PADDING = "8px 16px"
MENU_ITEM_PADDING = "10px 32px"
MENU_BORDER = "1px solid #9a9a9a"

# Room around each row of the folder list. Rows are tall anyway (they hold
# radio buttons), so this mostly keeps the names off the pane's left edge.
TABLE_ITEM_PADDING = "4px 10px"


def action_button_style(background: str, padding: str = "0px") -> str:
    """Qt stylesheet for a button of the given background color.

    A stylesheet opts the button out of native drawing, hover and pressed
    feedback included, so those states (and disabled) are restated here,
    derived from `background`.
    """
    base = QColor(background)
    return f"""
        QPushButton {{
            background-color: {background};
            color: {BUTTON_FG};
            border: 1px solid {base.darker(125).name()};
            border-radius: 4px;
            padding: {padding};
            font-weight: bold;
        }}
        QPushButton:hover {{ background-color: {base.lighter(115).name()}; }}
        QPushButton:pressed {{ background-color: {base.darker(115).name()}; }}
        QPushButton:disabled {{
            background-color: {base.darker(150).name()};
            color: {QColor(BUTTON_FG).darker(180).name()};
            border-color: {base.darker(160).name()};
        }}
    """


def _text_button_size(padding: str) -> QSize:
    """The size a styled button carrying `ACTION_BUTTON_TEXT` would take."""
    probe = QPushButton(ACTION_BUTTON_TEXT)
    probe.setStyleSheet(action_button_style(PRIMARY_BUTTON_BG, padding))
    return probe.sizeHint()


def action_button_size() -> QSize:
    """The shared size of a full-size button, for other controls to match."""
    return _text_button_size(ACTION_BUTTON_PADDING)


def match_action_button(button: QPushButton) -> None:
    """Size `button` like the others in its row (never narrower than its text)."""
    reference = action_button_size()
    button.setFixedSize(max(reference.width(), button.sizeHint().width()), reference.height())


def action_button(
    text: str,
    background: str = SECONDARY_BUTTON_BG,
    padding: str = ACTION_BUTTON_PADDING,
    *,
    uniform: bool = False,
) -> QPushButton:
    """A button wearing the app's look: the one place a button is made.

    `setAutoDefault(False)` so Enter runs the window's or dialog's own
    default action rather than whichever button has focus. `uniform` freezes
    the button to the shared size, for a row of them.
    """
    button = QPushButton(text)
    button.setStyleSheet(action_button_style(background, padding))
    button.setAutoDefault(False)
    if uniform:
        match_action_button(button)
    return button


def table_style() -> str:
    """Qt stylesheet giving the folder list room around each cell.

    Styling `::item` takes the rows out of native painting, so the selected
    row's colors are restated in `palette()` terms.
    """
    return f"""
        QTableWidget::item {{ padding: {TABLE_ITEM_PADDING}; }}
        QTableWidget::item:selected {{
            background: palette(highlight);
            color: palette(highlighted-text);
        }}
    """


def menu_style() -> str:
    """Qt stylesheet for the menu bar and its drop-downs: bigger targets, an edge."""
    body = QApplication.palette().color(QPalette.ColorRole.Window).name()
    return f"""
        QMenuBar::item {{
            padding: {MENU_BAR_ITEM_PADDING};
            background: transparent;
        }}
        QMenuBar::item:selected, QMenuBar::item:pressed {{
            background: palette(highlight);
            color: palette(highlighted-text);
        }}
        QMenu {{
            padding: 6px;
            background: {body};
            border: {MENU_BORDER};
        }}
        QMenu::item {{ padding: {MENU_ITEM_PADDING}; }}
        QMenu::item:selected {{
            background: palette(highlight);
            color: palette(highlighted-text);
        }}
    """


# The status bar while the helper is running: DupFinder's busy green.
STATUS_BUSY_BG = "#3f6b39"
STATUS_BUSY_FG = "#f0f2ef"
# Contents margins, not a stylesheet padding, which a QStatusBar ignores.
STATUS_BAR_MARGINS = (10, 6, 10, 6)
STATUS_BAR_BORDER = "1px solid #9a9a9a"


def status_style(busy: bool) -> str:
    """Qt stylesheet for the status bar, idle or busy."""
    if busy:
        background, foreground = STATUS_BUSY_BG, STATUS_BUSY_FG
    else:
        palette = QApplication.palette()
        background = palette.color(QPalette.ColorRole.Window).name()
        foreground = palette.color(QPalette.ColorRole.WindowText).name()
    return f"""
        QStatusBar {{
            background: {background};
            border-top: {STATUS_BAR_BORDER};
        }}
        QStatusBar::item {{ border: none; }}
        QStatusBar QLabel {{
            background: transparent;
            color: {foreground};
        }}
    """


def tune_palette(app: QApplication) -> None:
    """Lighten the window surface when the theme gives it the list's own color.

    Yaru-dark through Fusion hands Qt the same color for `Window` and `Base`,
    so the list has no edge. The change is applied only when it actually
    increases the separation, so a light theme is left alone.
    """
    palette = app.palette()
    window = palette.color(QPalette.ColorRole.Window)
    base = palette.color(QPalette.ColorRole.Base)
    before = abs(window.lightness() - base.lightness())
    if before >= MIN_SEPARATION:
        return
    lightened = window.lighter(WINDOW_LIGHTEN)
    if abs(lightened.lightness() - base.lightness()) <= before:
        return
    palette.setColor(QPalette.ColorRole.Window, lightened)
    app.setPalette(palette)


def mono_font() -> QFont:
    """The system's fixed-width font at the app's point size (for paths)."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(UI_POINT_SIZE)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font
