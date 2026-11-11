"""The window end to end, with a fake helper runner in place of pkexec."""

from __future__ import annotations

import os

from PyQt6.QtWidgets import QMessageBox
from windowchrome import WindowFrame

from crenel import window as window_module
from crenel.protocol import HelperReply
from crenel.shares import CONFLICT, HIDDEN, READ, WRITE, FolderState

from conftest import ok_results


def test_lists_the_projects_all_hidden(window, projects):
    root, share = projects
    assert window.names() == ["ABC", "DEF", "ghi"]
    assert all(window.state(n).mode == HIDDEN for n in window.names())
    assert window.radio("ABC", HIDDEN).isChecked()
    assert os.path.isdir(share)  # created on the first refresh
    assert "3 projects · 0 shared" in window.status_text()
    assert not window.apply_button.isEnabled()
    assert root in window.paths_label.text()


def test_clicking_marks_a_change(window):
    window.radio("ABC", WRITE).click()
    assert window.desired("ABC") == WRITE
    assert [c.name for c in window.pending()] == ["ABC"]
    assert window.apply_button.isEnabled()
    assert "1 change not applied yet" in window.status_text()
    window.radio("ABC", HIDDEN).click()
    assert window.pending() == []


def test_apply_sends_one_request_and_refreshes(window, runner):
    window.choose("ABC", WRITE)
    window.choose("DEF", READ)
    runner.replies.append(ok_results)
    window.apply()
    assert len(runner.requests) == 1
    changes = runner.requests[0]["changes"]
    assert changes == [{"name": "ABC", "mode": "write"}, {"name": "DEF", "mode": "read"}]
    assert runner.requests[0]["force"] is False
    # The fake changed nothing, so the refresh shows the real state again.
    assert window.pending() == []


def test_controls_are_locked_while_applying(window, runner):
    runner.hold = True
    window.choose("ABC", READ)
    window.apply()
    assert not window.apply_button.isEnabled() and not window.refresh_button.isEnabled()
    window.choose("DEF", WRITE)  # ignored while the helper runs
    assert window.desired("DEF") == HIDDEN
    runner.finish()
    assert window.refresh_button.isEnabled()


def test_a_cancelled_password_keeps_the_changes_marked(window, runner, dialogs):
    window.choose("ABC", READ)
    runner.replies.append(HelperReply(error="The password prompt was cancelled."))
    window.apply()
    assert "cancelled" in dialogs[-1][1]
    assert [c.name for c in window.pending()] == ["ABC"]


def test_busy_folders_offer_a_forced_retry(window, runner, dialogs):
    window.choose("DEF", HIDDEN)
    window.choose("ABC", READ)

    def busy(request):
        return HelperReply(results=({"name": "ABC", "mode": "read", "ok": False,
                                     "busy": True, "error": "in use", "warning": ""},))

    runner.replies.extend([busy, ok_results])
    dialogs.answer = QMessageBox.StandardButton.Yes
    window.apply()
    # Only busy folders: no summary, straight to the question. The forced
    # retry then gets its own summary.
    assert [title for title, _ in dialogs] == ["Crenel — folders in use", "Crenel — Applied"]
    assert "in use" in dialogs[0][1]
    assert len(runner.requests) == 2
    assert runner.requests[1]["force"] is True
    assert runner.requests[1]["changes"] == [{"name": "ABC", "mode": "read"}]


def applied_states(monkeypatch, states):
    """Make the refresh after Apply see `states`, as if the helper had made them."""
    monkeypatch.setattr(window_module, "read_states", lambda root, share, protected: states)


def test_a_successful_apply_is_confirmed(window, runner, dialogs, monkeypatch):
    window.choose("ABC", WRITE)
    window.choose("DEF", READ)
    git = (".git",)
    applied_states(monkeypatch, {
        "ABC": FolderState("ABC", WRITE, git, git),
        "DEF": FolderState("DEF", READ),
        "ghi": FolderState("ghi", HIDDEN),
    })
    runner.replies.append(ok_results)
    window.apply()
    title, text = dialogs[-1]
    assert title == "Crenel — Applied"
    assert text.startswith("All 2 changes were applied.")
    assert "✓  ABC: now writable, protected: .git" in text
    assert "✓  DEF: now read-only" in text
    assert "Last Apply: All 2 changes were applied." in window.status_text()


def test_one_change_reads_naturally(window, runner, dialogs, monkeypatch):
    window.choose("DEF", READ)
    applied_states(monkeypatch, {"DEF": FolderState("DEF", READ)})
    runner.replies.append(ok_results)
    window.apply()
    assert dialogs[-1][1].startswith("The change was applied.")


def test_a_partial_apply_is_a_warning(window, runner, dialogs):
    window.choose("ABC", READ)
    window.choose("DEF", READ)
    runner.replies.append(HelperReply(results=(
        {"name": "ABC", "mode": "read", "ok": True, "busy": False, "error": "", "warning": ""},
        {"name": "DEF", "mode": "read", "ok": False, "busy": False,
         "error": "Permission denied", "warning": ""},
    )))
    window.apply()
    title, text = dialogs[-1]
    assert title == "Crenel — Apply"
    assert text.startswith("1 of 2 changes applied.")
    assert "✗  DEF: Permission denied" in text


def test_a_refresh_clears_the_last_result(window, runner, monkeypatch):
    window.choose("DEF", READ)
    applied_states(monkeypatch, {"DEF": FolderState("DEF", READ)})
    runner.replies.append(ok_results)
    window.apply()
    assert "Last Apply" in window.status_text()
    window.refresh()
    assert "Last Apply" not in window.status_text()


def test_busy_without_retry_stays_marked(window, runner, dialogs):
    window.choose("ABC", READ)
    runner.replies.append(lambda r: HelperReply(results=(
        {"name": "ABC", "mode": "read", "ok": False, "busy": True, "error": "", "warning": ""},)))
    window.apply()
    assert len(runner.requests) == 1
    assert [c.name for c in window.pending()] == ["ABC"]


def test_failures_are_reported(window, runner, dialogs):
    window.choose("ABC", READ)
    runner.replies.append(HelperReply(results=(
        {"name": "ABC", "mode": "read", "ok": False, "busy": False,
         "error": "mount --bind: Permission denied", "warning": ""},)))
    window.apply()
    assert "ABC: mount --bind: Permission denied" in dialogs[-1][1]


def test_hide_all(window, monkeypatch, projects):
    states = {
        "ABC": FolderState("ABC", WRITE, (".git",), (".git",)),
        "DEF": FolderState("DEF", READ),
    }
    monkeypatch.setattr(window_module, "read_states", lambda root, share, protected: states)
    window.refresh(ask=False)
    assert "2 shared (1 writable, 1 read-only)" in window.status_text()
    window.hide_all()
    assert [c.name for c in window.pending()] == ["ABC", "DEF"]
    assert not window.hide_all_button.isEnabled()


def test_refresh_asks_before_dropping_changes(window, dialogs):
    window.choose("ABC", READ)
    assert window.refresh() is False  # the fixture answers No
    assert window.pending()
    dialogs.answer = QMessageBox.StandardButton.Yes
    assert window.refresh() is True
    assert window.pending() == []


def test_a_conflict_row(window, monkeypatch):
    states = {"ABC": FolderState("ABC", CONFLICT, detail="mounted from elsewhere")}
    monkeypatch.setattr(window_module, "read_states", lambda root, share, protected: states)
    window.refresh(ask=False)
    assert window.desired("ABC") is None
    assert not any(window.radio("ABC", m).isChecked() for m in (HIDDEN, READ, WRITE))
    assert "1 in conflict" in window.status_text()
    window.choose("ABC", HIDDEN)
    assert window.pending()  # choosing anything replaces what is there


def test_the_protected_names_mark_write_folders(window, monkeypatch, conf):
    def read_states(_root, _share, protected):
        found = tuple(n for n in (".envrc", ".git") if n in protected)
        return {"ABC": FolderState("ABC", WRITE, (".git",), found)}

    monkeypatch.setattr(window_module, "read_states", read_states)
    window.refresh(ask=False)
    assert window.pending() == []
    assert "(read-only: .git)" in window.table.item(window._rows["ABC"], 0).toolTip()
    conf(protected=(".git", ".envrc"))
    window.refresh(ask=False)
    assert [c.name for c in window.pending()] == ["ABC"]  # .envrc is missing
    conf(protected=(".git", "# .envrc"))
    window.refresh(ask=False)
    assert window.pending() == []  # a commented name is not wanted
    conf(protected=())
    window.refresh(ask=False)
    assert [c.name for c in window.pending()] == ["ABC"]  # .git is no longer wanted


def test_the_window_is_framed_with_the_menu_bar_below_the_title_bar(qtbot, window):
    frame = window.findChild(WindowFrame)
    assert frame is not None and len(frame.title_bar.buttons) == 3  # minimize, maximize, close
    window.show()
    qtbot.waitExposed(window)
    assert window.menuBar().geometry().top() >= frame.title_bar.geometry().bottom()


def test_show_commands(window):
    assert window.commands_text() == "Nothing to apply."
    window.choose("DEF", READ)
    text = window.commands_text()
    assert "# DEF: hidden → read-only" in text
    assert "mount -o remount,bind,ro" in text


def test_the_commands_dialog_is_framed_with_a_close_button_only(window):
    window.choose("DEF", READ)
    window.show_commands()
    dialog = window._commands_dialog
    assert dialog is not None
    frame = dialog.findChild(WindowFrame)
    assert frame is not None and frame.title_bar.buttons == [frame.title_bar.close_button]
    # The title bar is inside the window, so the dialog is taller by it.
    assert dialog.height() == 420 + frame.title_bar.height()
    dialog.close()


def test_missing_settings_are_reported_not_listed(qtbot, conf, runner):
    conf(projects_root="")
    win = window_module.MainWindow(runner=runner)
    qtbot.addWidget(win)
    assert win.names() == []
    assert "No projects folder" in win.status_text()
    assert win.needs_setup()


def test_close_waits_for_apply(window, runner):
    runner.hold = True
    window.choose("ABC", READ)
    window.apply()
    assert window.close() is False
    runner.finish()
    assert window.close() is True


def test_stray_items_block_sharing_but_not_hiding(window, runner, dialogs, monkeypatch, projects):
    _root, share = projects
    with open(os.path.join(share, "notes.txt"), "w") as handle:
        handle.write("x")
    states = {"ABC": FolderState("ABC", WRITE), "DEF": FolderState("DEF", HIDDEN)}
    monkeypatch.setattr(window_module, "read_states", lambda root, share, protected: states)
    window.refresh(ask=False)
    assert "⚠ 1 stray item in the share folder" in window.status_text()
    window.choose("DEF", READ)
    window.apply()
    assert runner.requests == []
    title, text = dialogs[-1]
    assert title == "Crenel — share folder not empty" and "notes.txt" in text
    window.choose("DEF", HIDDEN)
    window.choose("ABC", HIDDEN)
    window.apply()
    assert [c["name"] for c in runner.requests[-1]["changes"]] == ["ABC"]

