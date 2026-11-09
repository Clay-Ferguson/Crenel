# Testing notes

Run with `./tests/run.sh` (see `AGENTS.md`). Arguments go straight to pytest.

## Layout

- `test_shares.py`: mountinfo parsing, folder states, and the step plans (the order in which access is opened and closed). Pure, no Qt.
- `test_helper.py`: the root helper's request validation, descriptor-based opening, and remount flags. Runs without root.
- `test_helper_namespace.py`: **the helper making real bind mounts**, inside `unshare -rm` (a throwaway user and mount namespace where the test is root). `/` is made rshared like a desktop host, and a nested *slave* namespace plays the VM (virtiofsd's view), so the tests check what the VM can actually write. Covers Read and Write as seen from the VM, git protection staying off the real project, mode switches, Hide, busy and forced unmounts, the scratch spot's cleanup, and symlink refusal. Skipped where unprivileged namespaces are not allowed.
- `test_protocol.py`, `test_config.py`: the request and reply, and the config file.
- `test_window.py`: the window end to end with a `FakeRunner` in place of pkexec.
- `test_settings.py`: the dialog.
- `test_help.py`: the Options menu, and every link and anchor in `docs/`.
- `test_rules.py`: AGENTS.md rules read off the source: no hand-built buttons, no shells, the helper spawns nothing, and the non-widget modules never import Qt.

## Fixtures worth knowing (`conftest.py`)

- `projects`: `(root, share)`. `root` holds three projects (`ABC` is a git repo), a dot-folder, a file and a symlink. The share folder does not exist yet.
- `conf`: **use it in every test that reads settings.** Points `config.CONFIG_PATH` at a temp file holding `projects`, and returns `save(...)` for changing settings mid-test.
- `dialogs`: **autouse, and load-bearing.** A `QMessageBox` blocks forever under `offscreen`. This captures them all and answers questions with `dialogs.answer` (No by default).
- `runner` / `window`: a `FakeRunner` and a `MainWindow` using it. Queue replies on `runner.replies` (a `HelperReply`, or a function of the request such as `ok_results`). Set `runner.hold = True` to keep a request "waiting for the password" until `runner.finish()`.
