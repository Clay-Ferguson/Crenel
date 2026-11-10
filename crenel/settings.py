"""The settings dialog: the two folders, and the protected names.

DupFinder's dialog layout: a stack of labelled sections in a QVBoxLayout,
sized to its contents, with Save and Cancel pinned at the bottom. Adding a
setting is one more `_add_folder` (or any widget) call, a `Settings` field
and a `config.KEYS` row.

There are no command fields, on purpose. Everything run as root is fixed in
the helper (see config.py and helper.py for why).
"""

from __future__ import annotations

from html import escape

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from windowchrome import apply_scrollbars

from . import APP_NAME
from .config import (
    CONFIG_PATH,
    Settings,
    ensure_share_dir,
    folder_problems,
    is_comment,
    load_settings,
    protected_problems,
    real_path,
    save_settings,
)
from .shares import read_mountinfo, stray_entries
from .style import (
    PRIMARY_BUTTON_BG,
    SECONDARY_BUTTON_BG,
    action_button,
    action_button_size,
    mono_font,
)

SECTION_SPACING = 14
LABEL_SPACING = 4
MIN_WIDTH = 640

ROOT_LABEL = "Projects folder (every folder directly inside it is listed):"
SHARE_LABEL = "VM share folder (point the VM's virtiofs share here):"
PROTECTED_LABEL = (
    "Read-only in projects shared Write (one name per line, e.g. .git; "
    "a line starting with # is ignored):"
)
PROTECTED_TIP = (
    "Folders or files directly inside each project shared Write that the VM\n"
    "can read but not change, rename, remove or replace. Names only, not\n"
    "paths: list .vscode rather than .vscode/tasks.json. A line starting\n"
    "with # is a comment, kept but ignored: \"# .envrc\" turns .envrc off.\n\n"
    "With .git listed, an agent cannot plant a git hook (or a .git of its own)\n"
    "that would run on the host the next time you use git there. Git in the VM\n"
    "can then only read: commit, fetch and the rest on the host."
)
# How many lines the protected names box shows before it scrolls.
PROTECTED_ROWS = 4

# How many stray items a message names before it just counts the rest.
STRAY_LISTED = 10


def stray_text(share: str, names: list[str]) -> str:
    """Why a share folder holding `names` (see `shares.stray_entries`) is refused."""
    shown = [n.encode("utf-8", "surrogateescape").decode("utf-8", "replace") for n in names]
    listed = "\n".join(shown[:STRAY_LISTED])
    if len(names) > STRAY_LISTED:
        listed += f"\n…and {len(names) - STRAY_LISTED:,} more"
    return (
        f"The share folder {share} has things in it that Crenel did not put there, "
        f"and the VM can see (and change) all of them:\n\n{listed}\n\n"
        "Move them out of the share folder, or choose an empty one. "
        "Only empty folders and Crenel's own shares belong in it."
    )


class SettingsDialog(QDialog):
    """Edit the projects folder, the share folder and the protected names."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Settings")
        self.setMinimumWidth(MIN_WIDTH)

        settings, error = load_settings()

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(LABEL_SPACING)

        if error:
            warning = QLabel(
                f"<b>{escape(CONFIG_PATH)} could not be read</b><br>"
                f"{escape(error)}<br>"
                "The fields below show the defaults; saving replaces the file."
            )
            warning.setWordWrap(True)
            self._layout.addWidget(warning)

        self.root_edit = self._add_folder(
            ROOT_LABEL,
            settings.projects_root,
            "Choose the projects folder",
            tooltip="Crenel lists the folders directly inside this one, never deeper.",
        )
        self.share_edit = self._add_folder(
            SHARE_LABEL,
            settings.share_dir,
            "Choose the VM share folder",
            tooltip=(
                "Shared projects are mounted in here. Point the VM's virtiofs share\n"
                "at this folder instead of the projects folder (see the User Guide).\n"
                "Created if it does not exist. It must not be inside the projects\n"
                "folder, or contain it, and must hold nothing but empty folders and\n"
                "Crenel's own shares, since the VM sees everything in it."
            ),
        )

        self.protected_edit = self._add_protected(settings.protected)

        self._layout.addStretch(1)
        self._layout.addSpacing(SECTION_SPACING - LABEL_SPACING)
        self._layout.addLayout(self._button_row())

    # -- construction ------------------------------------------------------

    def _add_folder(self, label: str, value: str, title: str, tooltip: str) -> QLineEdit:
        """A labelled folder field with a "…" browse button beside it."""
        if self._layout.count():
            self._layout.addSpacing(SECTION_SPACING - LABEL_SPACING)
        caption = QLabel(label)
        caption.setWordWrap(True)
        caption.setToolTip(tooltip)
        self._layout.addWidget(caption)

        height = action_button_size().height()
        edit = QLineEdit(value)
        edit.setFont(mono_font())
        edit.setToolTip(tooltip)
        edit.setFixedHeight(height)
        browse = action_button("…", SECONDARY_BUTTON_BG, "0px")
        browse.setFixedSize(height, height)
        browse.setToolTip(title)
        browse.clicked.connect(lambda: self._browse(edit, title))

        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(browse)
        self._layout.addLayout(row)
        return edit

    def _add_protected(self, names: tuple[str, ...]) -> QPlainTextEdit:
        """The protected names: a few lines tall, scrolling past that."""
        self._layout.addSpacing(SECTION_SPACING - LABEL_SPACING)
        caption = QLabel(PROTECTED_LABEL)
        caption.setWordWrap(True)
        caption.setToolTip(PROTECTED_TIP)
        self._layout.addWidget(caption)

        edit = QPlainTextEdit("\n".join(names))
        edit.setFont(mono_font())
        edit.setToolTip(PROTECTED_TIP)
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        apply_scrollbars(edit)
        margin = 2 * (edit.document().documentMargin() + edit.frameWidth())
        edit.setFixedHeight(round(edit.fontMetrics().lineSpacing() * PROTECTED_ROWS + margin))
        self._layout.addWidget(edit)
        return edit

    def _button_row(self) -> QHBoxLayout:
        save = action_button("Save", PRIMARY_BUTTON_BG, uniform=True)
        save.setDefault(True)
        save.clicked.connect(self._save)
        cancel = action_button("Cancel", SECONDARY_BUTTON_BG, uniform=True)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)
        return row

    # -- actions -----------------------------------------------------------

    def _browse(self, edit: QLineEdit, title: str) -> None:
        start = real_path(edit.text()) if edit.text().strip() else ""
        chosen = QFileDialog.getExistingDirectory(self, f"{APP_NAME} — {title}", start)
        if chosen:
            edit.setText(chosen)

    def candidate(self) -> Settings:
        """The settings as the fields stand now.

        The protected names are the non-blank lines, each once, names without
        a trailing "/" and comments as written. Ones that are not plain names
        are kept for `_save` to refuse.
        """
        lines = (line.strip() for line in self.protected_edit.toPlainText().split("\n"))
        lines = (line if is_comment(line) else line.rstrip("/") for line in lines)
        return Settings(
            projects_root=self.root_edit.text().strip(),
            share_dir=self.share_edit.text().strip(),
            protected=tuple(dict.fromkeys(line for line in lines if line)),
        )

    def _save(self) -> None:
        """Check the fields, create the share folder, write the file, close."""
        settings = self.candidate()
        problems = folder_problems(settings) + protected_problems(settings.protected)
        if problems:
            QMessageBox.warning(self, f"{APP_NAME} — Settings", "\n\n".join(problems))
            return
        try:
            stray = stray_entries(settings.share_path, read_mountinfo())
        except OSError:
            # Without the mount table Crenel's own shares cannot be told from
            # strays. The window says the table is unreadable, and lists nothing.
            stray = []
        if stray:
            QMessageBox.warning(
                self, f"{APP_NAME} — Settings", stray_text(settings.share_path, stray)
            )
            return
        error = ensure_share_dir(settings) or save_settings(settings)
        if error:
            QMessageBox.warning(self, f"{APP_NAME} — Settings", error)
            return
        self.accept()


def show_settings(parent: QWidget | None = None) -> bool:
    """Open the settings dialog modally. True if it was saved."""
    return SettingsDialog(parent).exec() == QDialog.DialogCode.Accepted
