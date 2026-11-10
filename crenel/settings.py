"""The settings dialog: the two folders, and git protection.

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
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from windowchrome import apply_checkboxes

from . import APP_NAME
from .config import (
    CONFIG_PATH,
    Settings,
    ensure_share_dir,
    folder_problems,
    load_settings,
    real_path,
    save_settings,
)
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
PROTECT_LABEL = "Make .git read-only in projects shared Write"


class SettingsDialog(QDialog):
    """Edit the projects folder, the share folder and git protection."""

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
                "folder, or contain it."
            ),
        )

        self._layout.addSpacing(SECTION_SPACING - LABEL_SPACING)
        self.protect_check = QCheckBox(PROTECT_LABEL)
        self.protect_check.setChecked(settings.protect_git)
        self.protect_check.setToolTip(
            "Stops an agent planting a git hook, or swapping in a .git of its own,\n"
            "that would run on the host the next time you use git there. Inside\n"
            "the VM git can then only read: commits, fetches and anything else that\n"
            "writes to .git fail for those projects. Do them on the host."
        )
        apply_checkboxes(self.protect_check)
        self._layout.addWidget(self.protect_check)

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
        """The settings as the fields stand now."""
        return Settings(
            projects_root=self.root_edit.text().strip(),
            share_dir=self.share_edit.text().strip(),
            protect_git=self.protect_check.isChecked(),
        )

    def _save(self) -> None:
        """Check the folders, create the share folder, write the file, close."""
        settings = self.candidate()
        problems = folder_problems(settings)
        if problems:
            QMessageBox.warning(self, f"{APP_NAME} — Settings", "\n\n".join(problems))
            return
        error = ensure_share_dir(settings) or save_settings(settings)
        if error:
            QMessageBox.warning(self, f"{APP_NAME} — Settings", error)
            return
        self.accept()


def show_settings(parent: QWidget | None = None) -> bool:
    """Open the settings dialog modally. True if it was saved."""
    return SettingsDialog(parent).exec() == QDialog.DialogCode.Accepted
