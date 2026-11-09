"""The settings dialog: what it shows, and what Save writes or refuses."""

from __future__ import annotations

import os

from PyQt6.QtWidgets import QLabel

from crenel import config
from crenel.config import load_settings
from crenel.settings import SettingsDialog


def make(qtbot):
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    return dialog


def test_it_shows_the_file(qtbot, conf, projects):
    root, share = projects
    conf(protect_git=False)
    dialog = make(qtbot)
    assert dialog.root_edit.text() == root
    assert dialog.share_edit.text() == share
    assert not dialog.protect_check.isChecked()


def test_save_writes_and_creates_the_share_folder(qtbot, conf, projects):
    root, share = projects
    assert not os.path.exists(share)
    dialog = make(qtbot)
    dialog.protect_check.setChecked(False)
    dialog._save()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert os.path.isdir(share)
    assert load_settings()[0].protect_git is False


def test_overlapping_folders_are_refused(qtbot, conf, projects, dialogs):
    root, _share = projects
    dialog = make(qtbot)
    dialog.share_edit.setText(os.path.join(root, "ABC"))
    dialog._save()
    assert "inside the projects folder" in dialogs[-1][1]
    assert load_settings()[0].share_dir != os.path.join(root, "ABC")


def test_an_unreadable_file_is_announced(qtbot, conf):
    with open(config.CONFIG_PATH, "w") as handle:
        handle.write("folders: [broken\n")
    dialog = make(qtbot)
    assert any("could not be read" in label.text() for label in dialog.findChildren(QLabel))


def test_there_are_no_command_fields(qtbot, conf):
    dialog = make(qtbot)
    labels = " ".join(label.text() for label in dialog.findChildren(QLabel)).lower()
    assert "command" not in labels
