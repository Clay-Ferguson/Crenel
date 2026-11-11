# Notes to AI Agents

## What this is

Crenel is a PyQt6 desktop app that controls which project folders a QEMU/KVM VM can see. The VM's virtiofs share points at one *share folder* (default `~/vm-share`). Crenel bind-mounts each chosen project from the *projects folder* into it, as **Hidden** (not mounted), **Read** (bind mount remounted read-only) or **Write** (read-write, with each *protected name* that the project has, `.git` by default, self-bound read-only). The state lives only in the kernel's mount table, so a reboot hides everything.

The checkout directory is `access-controller`; the app, package and everything installed are named `crenel`.

## Running

```bash
./start.sh
```

uv-run, `package = false`, no install step. `windowchrome` is a sibling checkout (`../windowchrome`, editable), as in DupFinder, which is the pattern for everything here (scripts, config design, settings dialog, help, packaging). From it Crenel takes the help viewer, the radio buttons, the wide scroll bars (always with `handle=style.SCROLLBAR_HANDLE`, the GTK handle gray, §9), the window frame (`apply_frame()`, §13) and the menus (`apply_menus(bar)` with no arguments, §14: the gray edges and roomy items in the palette's highlight; `style.STATUS_BAR_BORDER` is windowchrome's `DIVIDER_BORDER`, the same gray). There is no menu stylesheet in `style.py`, and there should not be one again: a menu look that needs changing is changed in windowchrome. Installed from the `.deb` it runs `/usr/lib/crenel` under `python3 -I`. See `packaging/build-deb.sh`.

## Layout

- `shares.py`: **the model**, pure and stdlib-only. Parses `/proc/self/mountinfo`, lists projects, classifies a folder (`HIDDEN`/`READ`/`WRITE`/`CONFLICT` plus its protections), and plans the `Step`s that change it (`plan_folder`). It also renders steps as commands for the preview. It is shared by the window and the root helper, so both agree on what a state means.
- `helper.py`: **the root helper**, the only code that runs as root. It is run through `pkexec`, reads a JSON request on stdin, and writes a JSON reply on stdout. It validates everything, sets up and tears down the scratch spot, makes the system calls itself (ctypes `mount`/`umount2`, through `/proc/self/fd/N` descriptors opened with O_NOFOLLOW), re-reads mountinfo after each folder, and hides a folder that ended up more open than allowed.
- `protocol.py`: the window's side of the protocol: `build_request`, `parse_reply` (including pkexec's exit codes 126/127), and `HelperReply`.
- `privilege.py`: `HelperRunner`, a QProcess running `pkexec` without blocking the GUI. It uses the installed `/usr/lib/crenel/crenel-helper` when running from `/usr/lib/crenel`, otherwise `/usr/bin/python3 -I -c <bootstrap> <checkout>`.
- `config.py`: the YAML config, using DupFinder's `KEYS` design: `projects_root`, `share_dir`, `protected` (a list of names; an old `protect_git` bool is migrated). `Settings.root_path`/`share_path` give real paths. Also `folder_problems`, `protected_problems`, `ensure_share_dir`.
- `window.py`: `MainWindow`. A `QTableWidget` with one row per project and a `QButtonGroup` of three radios per row. `_desired` holds the choices (None for an untouched conflict row) and `pending()` diffs them against `_states`. The control bar has Refresh, Show Commands, Hide All and Apply. Busy folders are re-marked after Apply and offered a forced (lazy) retry.
- `settings.py`: Options ▸ Settings: two folder fields with browse buttons, and the protected names text area (one name per line).
- `messages.py`: framed drop-ins for `QMessageBox.critical/warning/information/question`, same arguments and return value. Call them as `messages.warning(...)`, through the module: `tests/conftest.py` replaces them there.
- `statusbar.py`, `style.py`, `help.py`, `__main__.py`: DupFinder's, adapted.

## Rules

- **The helper is the security boundary.** Never weaken its validation or its descriptor-based opening, and never make it run a program or a shell (`test_rules.py` checks that it imports no `subprocess`). It may import only the standard library and `crenel.shares` (checked). Keep `shares.py` and `crenel/__init__.py` free of Qt and third-party imports.
- **No user-editable commands.** Nothing in the config may decide what runs as root. This was a deliberate decision with the user. The Show Commands preview (`shares.render_step`) is the transparency mechanism.
- **Protected names are single names directly inside a project, never paths** (`shares.valid_name`, checked by config, Settings and the helper). A nested `a/b` could be got around by renaming the writable `a`. This was decided with the user; making paths safe would need read-write self-binds on every parent.
- **Every share is built in the scratch spot and moved in complete.** The host's `/` is a shared mount and virtiofsd holds a slave copy of the share folder. A mount's copy in the VM is made when it arrives, with the flags it has *then*, and remounts never propagate. A plain bind also stays in `/`'s peer group, so anything mounted inside it is mirrored onto the real project. So `plan_folder` binds into `shares.STAGE_DIR` (a private tmpfs the helper creates and removes per Apply), makes it private, adds read-only and the protected names, then `MS_MOVE`s it into the share folder. Any change to an existing share is a full re-share, never an in-place remount. `test_helper_namespace.py` checks all of this with `/` made rshared and a slave "VM view" namespace. Never test mount behavior without both.
- **The share folder holds only** Crenel's mounts and empty folders (`shares.stray_entries`), because the VM sees all of it. Settings refuses to save one with anything else in it, and the window refuses Apply for anything but Hidden while strays are there.
- **No shells** anywhere (guarded), and **every button comes from `style.action_button()`** (guarded).
- **Only widget modules import Qt** (`window`, `settings`, `messages`, `privilege`, `statusbar`, `style`, `help`, `__main__`). The `.deb` smoke test imports the others without Qt.
- **Config loading must never raise.** Saving rewrites the whole file via temp file plus `os.replace`.
- **Every window Crenel opens is in windowchrome's frame** (README §13 there): one `apply_frame()` per window, before it is first shown (for the User Guide, on the dialog `show_markdown()` returns). Message boxes go through `crenel.messages`, never `QMessageBox`'s statics (guarded). The folder picker stays GNOME's native one, unframed: never `DontUseNativeDialog`. The frame owns each framed window's contents margins, so margins go on its layout; an initial `resize()` adds `frame.title_bar.height()` to the height. Do not draw any other title bar, keep the window title just "Crenel", and do not restyle the rendered help.
- **`docs/` is runtime content** shipped in the `.deb`; `tests/test_help.py` checks every link and anchor.
- Do not commit to git, or offer to. Only the human developer commits. Never add a dependency without asking.

## Testing

```bash
./tests/run.sh                        # everything
./tests/run.sh tests/test_shares.py   # one file
./lint.sh                             # ruff, pyright, bash -n
```

`tests/test_helper_namespace.py` runs the real helper against real bind mounts inside `unshare -rm` (an unprivileged user and mount namespace), with `/` made rshared like a real host and a nested slave namespace standing in for virtiofsd, so mount behavior, including what the VM can write, is tested without sudo. See `tests/README.md`.
