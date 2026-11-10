"""The settings dialog: what it shows, and what Save writes or refuses."""

from __future__ import annotations

import os

from PyQt6.QtWidgets import QLabel, QPlainTextEdit

from crenel import config
from crenel.config import load_settings
from crenel.settings import SettingsDialog


def make(qtbot):
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    return dialog


def test_it_shows_the_file(qtbot, conf, projects):
    root, share = projects
    conf(protected=(".git", ".envrc"))
    dialog = make(qtbot)
    assert dialog.root_edit.text() == root
    assert dialog.share_edit.text() == share
    assert dialog.protected_edit.toPlainText() == ".git\n.envrc"
    assert isinstance(dialog.protected_edit, QPlainTextEdit)


def test_save_writes_and_creates_the_share_folder(qtbot, conf, projects):
    root, share = projects
    assert not os.path.exists(share)
    dialog = make(qtbot)
    dialog.protected_edit.setPlainText("  .git \n\n.vscode/\n.envrc\n.git\n")
    dialog._save()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert os.path.isdir(share)
    assert load_settings()[0].protected == (".git", ".vscode", ".envrc")


def test_comment_lines_are_saved_but_ignored(qtbot, conf):
    dialog = make(qtbot)
    dialog.protected_edit.setPlainText(".git\n  # .envrc\n#.vscode/tasks.json/\n")
    dialog._save()
    assert dialog.result() == dialog.DialogCode.Accepted  # a commented path is fine
    settings = load_settings()[0]
    assert settings.protected == (".git", "# .envrc", "#.vscode/tasks.json/")
    assert settings.protected_names == (".git",)
    assert make(qtbot).protected_edit.toPlainText() == ".git\n# .envrc\n#.vscode/tasks.json/"


def test_an_empty_list_protects_nothing(qtbot, conf):
    dialog = make(qtbot)
    dialog.protected_edit.setPlainText("\n  \n")
    dialog._save()
    assert load_settings()[0].protected == ()


def test_a_path_is_refused(qtbot, conf, dialogs):
    dialog = make(qtbot)
    dialog.protected_edit.setPlainText(".git\n.vscode/tasks.json\n")
    dialog._save()
    assert dialog.result() != dialog.DialogCode.Accepted
    assert ".vscode/tasks.json is a path" in dialogs[-1][1]
    assert load_settings()[0].protected == (".git",)


def test_the_list_is_about_four_lines_tall(qtbot, conf):
    edit = make(qtbot).protected_edit
    line = edit.fontMetrics().lineSpacing()
    assert 4 * line <= edit.height() < 5 * line


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


def test_a_share_folder_with_strays_is_refused(qtbot, conf, projects, dialogs):
    _root, share = projects
    os.makedirs(os.path.join(share, "left-over"))
    dialog = make(qtbot)
    dialog.protected_edit.setPlainText("")
    dialog._save()
    assert dialog.result() == dialog.DialogCode.Accepted  # empty folders are fine
    with open(os.path.join(share, "notes.txt"), "w") as handle:
        handle.write("x")
    dialog = make(qtbot)
    dialog.protected_edit.setPlainText(".git")
    dialog._save()
    assert dialog.result() != dialog.DialogCode.Accepted
    assert "notes.txt" in dialogs[-1][1] and "left-over" not in dialogs[-1][1]
    assert load_settings()[0].protected == ()  # the refused save wrote nothing
