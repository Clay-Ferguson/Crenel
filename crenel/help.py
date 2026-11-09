"""The help window, and where its content lives.

The user guide is a markdown file under `docs/`, rendered by
`windowchrome.show_markdown()`. The same file is the repository's
documentation and the app's, as in DupFinder.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QWidget
from windowchrome import MarkdownDialog, apply_scrollbars, show_markdown

from . import APP_NAME
from .style import action_button

# `docs/` sits beside the package. `packaging/build-deb.sh` installs the whole
# tree into /usr/lib/crenel beside the package for this reason.
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


def _open(name: str, title: str, parent: QWidget | None) -> MarkdownDialog:
    dialog = show_markdown(
        DOCS_DIR / name,
        parent,
        title=f"{APP_NAME} — {title}",
        button_factory=lambda text: action_button(text, uniform=True),
    )
    apply_scrollbars(dialog.view)
    return dialog


def show_user_guide(parent: QWidget | None = None) -> None:
    """The whole user guide, the same one that ships in `docs/`."""
    _open("USER_GUIDE.md", "User Guide", parent)
