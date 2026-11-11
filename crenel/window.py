"""The window: one row per project, with Hidden / Read / Write beside each.

The rows are the folders directly under the projects folder. Each row's radio
buttons start out showing what the VM can see right now, read from the
kernel's mount table at startup and on every Refresh. Clicking a radio
button only *marks* a change: the row turns amber and bold, and nothing
happens until Apply. Apply sends every marked change to the root helper in
one request, so there is one password prompt however many folders change.

A row in conflict (something mounted in its place that Crenel would not have
put there) shows a ⚠ and no radio button. Choosing any mode for it makes
Apply replace whatever is there.

Below the list:
- Refresh re-reads the mounts (and asks before discarding marked changes).
- Show Commands previews what Apply will do, as shell commands.
- Hide All marks every project Hidden.
- Apply carries the changes out.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QBrush, QColor, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from windowchrome import apply_frame, apply_radios, apply_scrollbars, close_markdown_windows

from . import APP_NAME, messages
from .config import Settings, ensure_share_dir, folder_problems, load_settings
from .help import show_user_guide
from .privilege import HelperRunner
from .protocol import HelperReply, build_request
from .settings import show_settings, stray_text
from .shares import (
    CONFLICT,
    HIDDEN,
    MODES,
    READ,
    WRITE,
    Change,
    FolderState,
    needs_change,
    plan_changes,
    read_mountinfo,
    read_states,
    render_step,
    stage_setup_commands,
    stage_teardown_commands,
    stray_entries,
)
from .statusbar import BusyStatusBar
from .style import (
    CONFLICT_FG,
    CONTROL_BAR_PADDING,
    DANGER_BUTTON_BG,
    PENDING_FG,
    PRIMARY_BUTTON_BG,
    SCROLLBAR_HANDLE,
    SECONDARY_BUTTON_BG,
    action_button,
    menu_style,
    mono_font,
    table_style,
)

COL_NAME = 0
MODE_COLUMNS = {HIDDEN: 1, READ: 2, WRITE: 3}
HEADERS = ("Project", "Hidden", "Read", "Write")
MODE_IDS = {mode: index for index, mode in enumerate(MODES)}

# Room for a 22px radio indicator plus the item padding, and for the header text.
RADIO_COLUMN_WIDTH = 110
ROW_HEIGHT = 44

CONFLICT_MARK = "⚠ "

MODE_TIPS = {
    HIDDEN: "The VM cannot see this project at all",
    READ: "The VM can read this project but not change it",
    WRITE: "The VM can read and change this project",
}

MODE_WORDS = {
    HIDDEN: "hidden",
    READ: "read-only",
    WRITE: "writable",
    CONFLICT: "in conflict",
}

APPLY_TIP = "Make the marked changes (asks for your password once)"
REFRESH_TIP = "Read what is shared right now, dropping changes not yet applied"
COMMANDS_TIP = "Show what Apply will do, as the equivalent shell commands"
HIDE_ALL_TIP = "Mark every project Hidden (press Apply to carry it out)"


def printable(text: str) -> str:
    """`text` with undecodable filename bytes shown as U+FFFD (labels only)."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


class CommandsDialog(QDialog):
    """A read-only, monospaced view of the commands Apply would run."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Commands")
        frame = apply_frame(self)
        self.resize(900, 420 + frame.title_bar.height())
        layout = QVBoxLayout(self)
        self.view = QPlainTextEdit(text)
        self.view.setReadOnly(True)
        self.view.setFont(mono_font())
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        apply_scrollbars(self.view, handle=SCROLLBAR_HANDLE)
        layout.addWidget(self.view, 1)
        close = action_button("Close", SECONDARY_BUTTON_BG, uniform=True)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)


class MainWindow(QMainWindow):
    def __init__(self, runner: HelperRunner | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        frame = apply_frame(self)
        self.resize(820, 640 + frame.title_bar.height())

        # The runner is a parameter so tests can hand in a fake one: the real
        # one starts pkexec.
        self.runner = runner if runner is not None else HelperRunner(self)
        self.runner.finished.connect(self._on_reply)

        self._settings = Settings()
        # What the mounts say, what each row's radio buttons say (None: no
        # choice yet, a conflict row), and the request being applied.
        self._states: dict[str, FolderState] = {}
        self._desired: dict[str, str | None] = {}
        self._groups: dict[str, QButtonGroup] = {}
        self._rows: dict[str, int] = {}
        self._applying: dict[str, str] = {}
        # What is in the share folder that Crenel did not put there.
        self._stray: list[str] = []
        # The headline of the last Apply's summary, shown in the status bar
        # until the next Apply or a Refresh you ask for.
        self._last_result = ""
        self._problem = ""
        self._commands_dialog: CommandsDialog | None = None

        self._build_menus()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.paths_label = QLabel()
        self.paths_label.setWordWrap(True)
        self.paths_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.paths_label)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setStyleSheet(table_style())
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        for mode, column in MODE_COLUMNS.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, RADIO_COLUMN_WIDTH)
            self.table.horizontalHeaderItem(column).setToolTip(MODE_TIPS[mode])
        self.table.horizontalHeaderItem(COL_NAME).setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        apply_scrollbars(self.table, handle=SCROLLBAR_HANDLE)
        layout.addWidget(self.table, 1)

        self.refresh_button = action_button("Refresh", SECONDARY_BUTTON_BG, CONTROL_BAR_PADDING)
        self.refresh_button.setToolTip(REFRESH_TIP)
        self.refresh_button.clicked.connect(lambda: self.refresh())
        self.commands_button = action_button(
            "Show Commands", SECONDARY_BUTTON_BG, CONTROL_BAR_PADDING
        )
        self.commands_button.setToolTip(COMMANDS_TIP)
        self.commands_button.clicked.connect(self.show_commands)
        self.hide_all_button = action_button("Hide All", DANGER_BUTTON_BG, CONTROL_BAR_PADDING)
        self.hide_all_button.setToolTip(HIDE_ALL_TIP)
        self.hide_all_button.clicked.connect(self.hide_all)
        self.apply_button = action_button("Apply", PRIMARY_BUTTON_BG, CONTROL_BAR_PADDING)
        self.apply_button.setToolTip(APPLY_TIP)
        self.apply_button.clicked.connect(self.apply)

        control_bar = QHBoxLayout()
        control_bar.addWidget(self.refresh_button)
        control_bar.addWidget(self.commands_button)
        control_bar.addWidget(self.hide_all_button)
        control_bar.addStretch(1)
        control_bar.addWidget(self.apply_button)
        layout.addLayout(control_bar)

        self._status = BusyStatusBar()
        self.setStatusBar(self._status)

        self.refresh(ask=False)

    # -- menus --------------------------------------------------------------

    def _build_menus(self) -> None:
        bar = self.menuBar()
        bar.setStyleSheet(menu_style())

        file_menu = bar.addMenu("&File")
        refresh_action = QAction("&Refresh", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.setStatusTip("Read what is shared right now")
        refresh_action.triggered.connect(lambda: self.refresh())
        file_menu.addAction(refresh_action)
        exit_action = QAction("E&xit", self)
        exit_action.setStatusTip(f"Close {APP_NAME}")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        options = bar.addMenu("&Options")
        self.settings_action = QAction("&Settings", self)
        self.settings_action.setStatusTip("The projects folder, the share folder, protected names")
        self.settings_action.triggered.connect(self.open_settings)
        options.addAction(self.settings_action)
        guide_action = QAction("&User Guide", self)
        guide_action.setStatusTip(f"Everything {APP_NAME} does, and the one-time VM setup")
        guide_action.triggered.connect(lambda: show_user_guide(self))
        options.addAction(guide_action)

    # -- reading the state -------------------------------------------------------

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def problem(self) -> str:
        """Why no folders are listed (settings missing or wrong), or ""."""
        return self._problem

    def refresh(self, ask: bool = True, keep: dict[str, str] | None = None) -> bool:
        """Re-read the settings and the mounts, and rebuild the list.

        With `ask`, marked changes are only thrown away after a Yes. `keep`
        re-marks some choices on top of the fresh state: after an Apply,
        the folders that were busy or not authorized stay marked, so
        pressing Apply again retries them. Returns False if nothing was done.
        """
        if self.runner.running:
            return False
        pending = self.pending()
        if ask and pending:
            answer = messages.question(
                self,
                f"{APP_NAME} — Refresh",
                f"Discard {plural(len(pending), 'change')} not yet applied?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        if ask:
            # A Refresh you asked for shows the state from now on, not news
            # of the last Apply.
            self._last_result = ""

        settings, _error = load_settings()
        self._settings = settings
        self._show_paths()
        problems = folder_problems(settings)
        if not problems:
            share_error = ensure_share_dir(settings)
            if share_error:
                problems = [share_error]
        states: dict[str, FolderState] = {}
        stray: list[str] = []
        if not problems:
            try:
                states = read_states(
                    settings.root_path, settings.share_path, settings.protected_names
                )
                stray = stray_entries(settings.share_path, read_mountinfo())
            except OSError as exc:
                problems = [f"Could not read the mount table: {exc.strerror or exc}"]
        self._problem = problems[0] if problems else ""
        self._states = states
        self._stray = stray
        self._desired = {n: (s.mode if s.mode != CONFLICT else None) for n, s in states.items()}
        for name, mode in (keep or {}).items():
            if name in self._desired:
                self._desired[name] = mode
        self._rebuild()
        self._update()
        return True

    def _show_paths(self) -> None:
        root = self._settings.root_path or "(not set)"
        share = self._settings.share_path or "(not set)"
        self.paths_label.setText(f"Projects: {root}\nVM share: {share}")

    # -- the list -------------------------------------------------------------------

    def _rebuild(self) -> None:
        self.table.setRowCount(0)
        for group in self._groups.values():
            group.deleteLater()
        self._groups.clear()
        self._rows.clear()
        self.table.setRowCount(len(self._states))
        for row, name in enumerate(self._states):
            item = QTableWidgetItem(printable(name))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, COL_NAME, item)
            group = QButtonGroup(self)
            for mode in MODES:
                radio = QRadioButton()
                radio.setToolTip(MODE_TIPS[mode])
                radio.setAccessibleName(f"{printable(name)}: {mode}")
                apply_radios(radio)
                group.addButton(radio, MODE_IDS[mode])
                cell = QWidget()
                cell_layout = QHBoxLayout(cell)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_layout.addWidget(radio)
                self.table.setCellWidget(row, MODE_COLUMNS[mode], cell)
            group.idClicked.connect(lambda mode_id, n=name: self.choose(n, MODES[mode_id]))
            self._groups[name] = group
            self._rows[name] = row
            self._sync_radios(name)
            self._paint(name)

    def radio(self, name: str, mode: str) -> QRadioButton:
        """The radio button for `name` in `mode` (for tests and accessibility)."""
        button = self._groups[name].button(MODE_IDS[mode])
        assert isinstance(button, QRadioButton)
        return button

    def names(self) -> list[str]:
        return list(self._states)

    def state(self, name: str) -> FolderState:
        return self._states[name]

    def desired(self, name: str) -> str | None:
        return self._desired[name]

    def _sync_radios(self, name: str) -> None:
        group = self._groups[name]
        desired = self._desired[name]
        if desired is None:
            # An exclusive group refuses to uncheck its last checked button.
            group.setExclusive(False)
            for button in group.buttons():
                button.setChecked(False)
            group.setExclusive(True)
        else:
            button = group.button(MODE_IDS[desired])
            if button is not None:
                button.setChecked(True)

    def _paint(self, name: str) -> None:
        """The row's label: amber and bold if marked, ⚠ if in conflict."""
        item = self.table.item(self._rows[name], COL_NAME)
        if item is None:
            return
        state = self._states[name]
        pending = self.is_pending(name)
        prefix = CONFLICT_MARK if state.mode == CONFLICT else ""
        item.setText(prefix + printable(name))
        font = item.font()
        font.setBold(pending)
        item.setFont(font)
        if pending:
            item.setForeground(QBrush(QColor(PENDING_FG)))
        elif state.mode == CONFLICT:
            item.setForeground(QBrush(QColor(CONFLICT_FG)))
        else:
            item.setData(Qt.ItemDataRole.ForegroundRole, None)
        tip = f"Now: {MODE_WORDS[state.mode]}"
        if state.protections:
            tip += f" (read-only: {', '.join(map(printable, state.protections))})"
        if state.detail:
            tip += f"\n{state.detail}"
        desired = self._desired[name]
        if pending and desired is not None:
            tip += f"\nAfter Apply: {MODE_WORDS[desired]}"
        item.setToolTip(tip)

    # -- choosing ---------------------------------------------------------------

    def is_pending(self, name: str) -> bool:
        desired = self._desired.get(name)
        if desired is None:
            return False
        return needs_change(self._states[name], desired)

    def pending(self) -> list[Change]:
        """The changes Apply would make, in list order."""
        return [Change(name, mode) for name, mode in self._desired.items()
                if mode is not None and self.is_pending(name)]

    def choose(self, name: str, mode: str) -> None:
        """Mark `name` to become `mode` (what a radio button click does)."""
        if self.runner.running:
            self._sync_radios(name)
            return
        self._desired[name] = mode
        self._sync_radios(name)
        self._paint(name)
        self._update()

    def hide_all(self) -> None:
        for name in self._desired:
            self._desired[name] = HIDDEN
            self._sync_radios(name)
            self._paint(name)
        self._update()

    # -- status --------------------------------------------------------------------

    def status_text(self) -> str:
        if self._problem:
            return self._problem
        modes = [s.mode for s in self._states.values()]
        read, write, conflicts = modes.count(READ), modes.count(WRITE), modes.count(CONFLICT)
        text = (
            f"{plural(len(modes), 'project')} · {read + write} shared "
            f"({write} writable, {read} read-only)"
        )
        if conflicts:
            text += f" · {conflicts} in conflict"
        if self._stray:
            text += f" · ⚠ {plural(len(self._stray), 'stray item')} in the share folder"
        pending = len(self.pending())
        if pending:
            text += f" · {plural(pending, 'change')} not applied yet"
        if self._last_result:
            text += f" · Last Apply: {self._last_result}"
        return text

    def _update(self) -> None:
        running = self.runner.running
        pending = bool(self.pending())
        self.apply_button.setEnabled(pending and not running)
        self.commands_button.setEnabled(pending and not running)
        self.hide_all_button.setEnabled(
            not running and any(m != HIDDEN for m in self._desired.values())
        )
        self.refresh_button.setEnabled(not running)
        self.settings_action.setEnabled(not running)
        self.table.setEnabled(not running)
        if not running:
            self._status.set_status(self.status_text())

    # -- show commands ----------------------------------------------------------------

    def commands_text(self) -> str:
        """What Apply will do, folder by folder, as shell commands."""
        changes = self.pending()
        if not changes:
            return "Nothing to apply."
        settings = self._settings
        plans = plan_changes(
            changes,
            settings.root_path,
            settings.share_path,
            settings.protected_names,
            read_mountinfo(),
        )
        lines = [
            "# What Apply will do, as the equivalent commands. Crenel's helper makes",
            "# these as system calls itself, as root, after one password prompt.",
        ]
        staging = any(plan.uses_stage for plan in plans)
        if staging:
            lines.append("")
            lines.append("# A private scratch spot where each share is built before it is")
            lines.append("# moved into the share folder, so the VM only ever sees it finished.")
            lines.extend(stage_setup_commands())
        for plan in plans:
            change = plan.change
            before = MODE_WORDS[self._states[change.name].mode]
            lines.append("")
            lines.append(f"# {printable(change.name)}: {before} → {MODE_WORDS[change.mode]}")
            lines.extend(render_step(step) for step in plan.steps)
            if not plan.steps:
                lines.append("# (nothing to do)")
        if staging:
            lines.append("")
            lines.append("# Remove the scratch spot.")
            lines.extend(stage_teardown_commands())
        return "\n".join(lines)

    def show_commands(self) -> None:
        dialog = CommandsDialog(self.commands_text(), self)
        self._commands_dialog = dialog
        dialog.open()

    # -- applying -----------------------------------------------------------------------

    def apply(self) -> None:
        if self.runner.running:
            return
        changes = self.pending()
        if changes:
            self._start(changes, force=False)

    def _start(self, changes: list[Change], force: bool) -> None:
        # Nothing is shared into a share folder holding things Crenel did not
        # put there, since the VM sees those too. Hiding always works.
        if any(c.mode != HIDDEN for c in changes):
            share = self._settings.share_path
            try:
                stray = stray_entries(share, read_mountinfo())
            except OSError:
                stray = []  # the helper reads the table itself and fails on its own
            if stray:
                self._stray = stray
                self._update()
                messages.warning(
                    self,
                    f"{APP_NAME} — share folder not empty",
                    stray_text(share, stray) + "\n\nHiding projects still works.",
                )
                return
        request = build_request(self._settings, changes, force)
        # Recorded before starting: a runner may reply before start() returns.
        self._applying = {c.name: c.mode for c in changes}
        self._last_result = ""
        error = self.runner.start(request)
        if error:
            self._applying = {}
            messages.warning(self, APP_NAME, error)
            return
        if self.runner.running:
            self._status.set_status(
                f"Applying {plural(len(changes), 'change')} — waiting for authorization…",
                busy=True,
            )
            self._update()

    def apply_summary(self, reply: HelperReply) -> str:
        """The message shown after an Apply: a headline, then one line per folder.

        A folder that worked is described from the state just re-read from the
        mount table (which the helper has also checked), so the summary says
        what the VM can see now, not merely what was asked for.
        """
        results = reply.results
        done = sum(1 for r in results if r.get("ok"))
        if done == len(results):
            headline = (
                "The change was applied." if done == 1
                else f"All {done} changes were applied."
            )
        else:
            headline = f"{done} of {plural(len(results), 'change')} applied."
        lines = []
        for result in results:
            name = result["name"]
            if result.get("ok"):
                lines.append(f"✓  {printable(name)}: now {self._describe(name, result['mode'])}")
            elif result.get("busy"):
                lines.append(f"…  {printable(name)}: in use, not changed yet")
            else:
                lines.append(f"✗  {printable(name)}: {result.get('error') or 'failed'}")
        lines += [f"Note: {warning}" for warning in reply.warnings]
        return headline + "\n\n" + "\n".join(lines)

    def _describe(self, name: str, requested: str) -> str:
        """`name`'s current state in words, e.g. "writable, protected: .git"."""
        state = self._states.get(name)
        if state is None:
            # A hidden project that no longer exists on the host drops out
            # of the list altogether.
            return MODE_WORDS[requested]
        text = MODE_WORDS[state.mode]
        if state.mode == WRITE and state.protections:
            text += f", protected: {', '.join(map(printable, state.protections))}"
        return text

    def _on_reply(self, reply: HelperReply) -> None:
        requested, self._applying = self._applying, {}
        self._status.set_status("", busy=False)
        if reply.error:
            self.refresh(ask=False, keep=requested)
            messages.warning(self, f"{APP_NAME} — Apply", reply.error)
            return
        busy = {name: requested[name] for name in reply.busy if name in requested}
        self.refresh(ask=False, keep=busy)
        # A reply that is nothing but busy folders goes straight to the
        # question below; everything else gets a summary first.
        if len(busy) < len(reply.results):
            text = self.apply_summary(reply)
            self._last_result = text.split("\n", 1)[0]
            self._update()
            if reply.failures:
                messages.warning(self, f"{APP_NAME} — Apply", text)
            else:
                messages.information(self, f"{APP_NAME} — Applied", text)
        if busy:
            listed = "\n".join(printable(name) for name in busy)
            answer = messages.question(
                self,
                f"{APP_NAME} — folders in use",
                f"These folders are in use, probably by something running in the VM:"
                f"\n\n{listed}\n\nDetach them anyway? They disappear from the VM at once, "
                "but files already open there stay usable until they are closed. To cut "
                "off access completely, stop the agent (or the VM) first.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._start([Change(name, mode) for name, mode in busy.items()], force=True)

    # -- settings ---------------------------------------------------------------------

    def open_settings(self) -> None:
        if self.runner.running:
            return
        if show_settings(self):
            self.refresh(ask=False)

    def needs_setup(self) -> bool:
        """Whether the settings are missing or wrong (checked at startup)."""
        return bool(folder_problems(load_settings()[0]))

    # -- lifecycle ----------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self.runner.running:
            # Closing now would orphan pkexec mid-change. Let it finish.
            self._status.set_status("Wait for Apply to finish before closing.", busy=True)
            event.ignore()
            return
        self._status.stop()
        close_markdown_windows()
        super().closeEvent(event)
