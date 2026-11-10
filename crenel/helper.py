"""The root helper: the one part of Crenel that runs as root.

The window never runs as root. When you press Apply it starts this module
through `pkexec`, which asks for your password. It sends one JSON request on
stdin and reads one JSON reply from stdout:

    request  {"protocol": 1, "root": "/home/me/projects", "share": "/home/me/vm-share",
              "protect_git": true, "force": false,
              "changes": [{"name": "ABC", "mode": "write"}, ...]}
    reply    {"results": [{"name": "ABC", "mode": "write", "ok": true, "busy": false,
                           "error": "", "warning": ""}, ...]}
         or  {"error": "why the request was refused"}

This is Crenel's security boundary, so it trusts nothing it is sent:

- **Paths.** The root and share folders must be absolute, normalized, owned by
  the caller (`PKEXEC_UID`), contain no symlinks anywhere, and not overlap.
  Folder names must be single path components.
- **No symlink races.** Every path is opened one component at a time with
  O_NOFOLLOW, and the mount system calls are given `/proc/self/fd/N`. A link
  swapped in after the checks therefore makes the step fail instead of
  redirecting a root-owned mount somewhere else. A bind's source must also be
  owned by the caller, checked on the opened descriptor.
- **No commands.** The helper calls mount(2) and umount2(2) itself through
  ctypes. There is no shell, no `mount` binary whose output would need
  parsing, and no command line from a config file. `shares.render_step` shows
  the equivalent commands for reading.
- **Built off to the side.** A share is assembled in a private scratch spot
  (`shares.STAGE_DIR`, a root-only tmpfs that exists only while the helper
  runs) and moved into the share folder complete. The VM's copy of a mount is
  made when the mount arrives, so this is what makes Read read-only *in the
  VM* and keeps git protections off the real project. See `shares.py`.
- **Checked results.** After each folder it re-reads /proc/self/mountinfo. If a
  folder ends up with *more* access than it had before and more than was
  asked for, the helper detaches it completely and reports the failure.

Stdlib only, and it imports nothing from Crenel but `shares`, which is also
stdlib only. Installed as /usr/lib/crenel/crenel-helper; see
packaging/build-deb.sh.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import pwd
import stat
import sys
from dataclasses import dataclass

from .shares import (
    BIND,
    CONFLICT,
    HIDDEN,
    MKDIR,
    MODES,
    MOVE,
    PRIVATE,
    READ,
    READONLY,
    RMDIR,
    STAGE_DIR,
    UMOUNT,
    WRITE,
    Change,
    FolderPlan,
    FolderState,
    MountEntry,
    classify,
    expected_git,
    mounts_under,
    plan_changes,
    read_mountinfo,
    render_step,
)

PROTOCOL = 1

# More than any real request (a few hundred folders' names), and a bound on
# what a stray writer to our stdin can make us parse as root.
MAX_REQUEST_BYTES = 1 << 20

NAME_MAX = 255

# <sys/mount.h>
MS_RDONLY = 0x1
MS_NOSUID = 0x2
MS_NODEV = 0x4
MS_NOEXEC = 0x8
MS_REMOUNT = 0x20
MS_NOATIME = 0x400
MS_NODIRATIME = 0x800
MS_BIND = 0x1000
MS_MOVE = 0x2000
MS_PRIVATE = 0x40000
MS_RELATIME = 0x200000
MS_STRICTATIME = 0x1000000
MNT_DETACH = 0x2
UMOUNT_NOFOLLOW = 0x8

# The per-mount options a remount has to restate. A bind remount replaces
# the mount's flags with exactly the ones passed, so leaving these out would
# clear nosuid or nodev from the user's own mount. In a user namespace, where
# the tests run, that is refused outright.
_KEPT_FLAGS = {
    "nosuid": MS_NOSUID,
    "nodev": MS_NODEV,
    "noexec": MS_NOEXEC,
    "noatime": MS_NOATIME,
    "nodiratime": MS_NODIRATIME,
    "relatime": MS_RELATIME,
}


class RequestError(ValueError):
    """The request is refused as a whole; nothing was changed."""


class Busy(OSError):
    """An unmount failed because something still has files open in it."""


@dataclass(frozen=True)
class Request:
    root: str
    share: str
    protect_git: bool
    force: bool
    changes: tuple[Change, ...]


# -- validation ------------------------------------------------------------------


def valid_name(name: object) -> bool:
    """Whether `name` is one plain path component: no "/", no NUL, not "." or ".."."""
    return (
        isinstance(name, str)
        and 0 < len(os.fsencode(name)) <= NAME_MAX
        and "/" not in name
        and "\0" not in name
        and name not in (".", "..")
    )


def check_folder(path: object, uid: int, what: str) -> str:
    """`path`, if it is a usable root or share folder for `uid`; else RequestError.

    Every component is lstat()ed, so a symlink anywhere in the path is
    refused. Crenel's settings dialog resolves the paths it saves, so a real
    path is always available.
    """
    if not isinstance(path, str) or not os.path.isabs(path):
        raise RequestError(f"The {what} must be an absolute path.")
    if os.path.normpath(path) != path or path == "/" or "\0" in path:
        raise RequestError(f"The {what} {path!r} is not a normalized folder path.")
    current = "/"
    info = None
    for part in path.strip("/").split("/"):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise RequestError(f"The {what} {path} cannot be used: {exc.strerror}.") from None
        if stat.S_ISLNK(info.st_mode):
            raise RequestError(
                f"The {what} {path} goes through a symlink ({current}). "
                "Use the folder's real path."
            )
        if not stat.S_ISDIR(info.st_mode):
            raise RequestError(f"The {what} {path} is not a folder ({current}).")
    assert info is not None
    if info.st_uid != uid:
        raise RequestError(f"The {what} {path} is not owned by you.")
    return path


def _inside(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def validate_request(data: object, uid: int) -> Request:
    """The request in `data`, checked from top to bottom, or RequestError."""
    if not isinstance(data, dict):
        raise RequestError("The request is not a JSON object.")
    if data.get("protocol") != PROTOCOL:
        raise RequestError(f"Unsupported request protocol {data.get('protocol')!r}.")
    root = check_folder(data.get("root"), uid, "projects folder")
    share = check_folder(data.get("share"), uid, "share folder")
    if _inside(share, root) or _inside(root, share):
        raise RequestError("The share folder and the projects folder must not contain each other.")
    protect_git = data.get("protect_git")
    force = data.get("force")
    if not isinstance(protect_git, bool) or not isinstance(force, bool):
        raise RequestError("protect_git and force must be true or false.")
    raw = data.get("changes")
    if not isinstance(raw, list):
        raise RequestError("changes must be a list.")
    changes: list[Change] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise RequestError("Each change must be an object.")
        name, mode = item.get("name"), item.get("mode")
        if not valid_name(name):
            raise RequestError(f"{name!r} is not a folder name.")
        assert isinstance(name, str)
        if mode not in MODES:
            raise RequestError(f"{mode!r} is not one of {', '.join(MODES)}.")
        if name in seen:
            raise RequestError(f"{name} appears twice.")
        seen.add(name)
        changes.append(Change(name, str(mode)))
    return Request(root, share, protect_git, force, tuple(changes))


# -- system calls --------------------------------------------------------------------

_libc: ctypes.CDLL | None = None


def _c() -> ctypes.CDLL:
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.mount.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
        ]
        _libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    return _libc


def _raise_errno(path: str) -> None:
    code = ctypes.get_errno()
    raise OSError(code, os.strerror(code), path)


def _fd_path(fd: int) -> bytes:
    return f"/proc/self/fd/{fd}".encode()


def open_nofollow(path: str) -> int:
    """An O_PATH descriptor for `path`, walked one component at a time.

    Each component is opened relative to the last with O_NOFOLLOW, so a
    symlink anywhere along the way fails with ELOOP or ENOTDIR instead of
    being followed. Mounts are still crossed, which is what makes a
    descriptor opened after a bind point at the new mount's root.
    """
    parts = [part for part in path.split("/") if part]
    if path != "/" and (not path.startswith("/") or ".." in parts):
        raise OSError(errno.EINVAL, "not an absolute, normalized path", path)
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY)
    try:
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            flags = os.O_PATH | os.O_NOFOLLOW | (0 if last else os.O_DIRECTORY)
            following = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = following
        # O_PATH | O_NOFOLLOW on a final symlink opens the link itself.
        if stat.S_ISLNK(os.fstat(fd).st_mode):
            raise OSError(errno.ELOOP, "is a symlink", path)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _top_mount(path: str) -> MountEntry | None:
    """The mount currently on top at `path`, if any."""
    entries = [e for e in read_mountinfo() if e.mount_point == path]
    return entries[-1] if entries else None


def do_mkdir(path: str, uid: int, gid: int) -> None:
    """Create the mount point `path`, owned by the user; an existing folder is fine."""
    parent = open_nofollow(os.path.dirname(path))
    base = os.path.basename(path)
    try:
        try:
            os.mkdir(base, 0o755, dir_fd=parent)
        except FileExistsError:
            info = os.stat(base, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError(errno.EEXIST, "exists and is not a folder", path) from None
            return
        os.chown(base, uid, gid, dir_fd=parent, follow_symlinks=False)
    finally:
        os.close(parent)


def do_bind(source: str, target: str, uid: int) -> None:
    """Bind `source` onto `target` (not recursively), both opened without following links.

    When source and target differ, the source has to be a folder owned by the
    caller. That is what stops this from ever exposing someone else's data.
    A self-bind (the git protection) exposes nothing new and is not checked.
    """
    source_fd = open_nofollow(source)
    target_fd = source_fd
    try:
        if source != target:
            info = os.fstat(source_fd)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError(errno.ENOTDIR, "is not a folder", source)
            if info.st_uid != uid:
                raise OSError(errno.EPERM, "is not owned by you", source)
            target_fd = open_nofollow(target)
            if not stat.S_ISDIR(os.fstat(target_fd).st_mode):
                raise OSError(errno.ENOTDIR, "is not a folder", target)
        if _c().mount(_fd_path(source_fd), _fd_path(target_fd), None, MS_BIND, None) != 0:
            _raise_errno(target)
    finally:
        os.close(source_fd)
        if target_fd != source_fd:
            os.close(target_fd)


def remount_flags(entry: MountEntry | None, read_only: bool) -> int:
    """The flags for a bind remount of `entry` that changes only ro/rw."""
    flags = MS_REMOUNT | MS_BIND | (MS_RDONLY if read_only else 0)
    options = entry.options if entry else ()
    for option in options:
        flags |= _KEPT_FLAGS.get(option, 0)
    if "relatime" not in options and "noatime" not in options:
        flags |= MS_STRICTATIME
    return flags


def do_remount(path: str, read_only: bool) -> None:
    """Make the mount at `path` read-only or read-write, keeping its other flags."""
    flags = remount_flags(_top_mount(path), read_only)
    fd = open_nofollow(path)
    try:
        if _c().mount(None, _fd_path(fd), None, flags, None) != 0:
            _raise_errno(path)
    finally:
        os.close(fd)


def do_private(path: str) -> None:
    """Cut the mount at `path` out of every link group (MS_PRIVATE).

    A plain bind of a project joins the link group of the filesystem it came
    from. Made private in the scratch spot, it is moved into the share folder
    as a mount of its own, so nothing mounted inside it is mirrored back onto
    the real project.
    """
    fd = open_nofollow(path)
    try:
        if _c().mount(None, _fd_path(fd), None, MS_PRIVATE, None) != 0:
            _raise_errno(path)
    finally:
        os.close(fd)


def do_move(source: str, target: str) -> None:
    """Move the finished share at `source` (in the scratch spot) onto `target`.

    The VM's copy is made at this moment, from the mount as it now is:
    read-only if it is read-only, with its git protections inside it.
    """
    source_fd = open_nofollow(source)
    try:
        target_fd = open_nofollow(target)
        try:
            if not stat.S_ISDIR(os.fstat(target_fd).st_mode):
                raise OSError(errno.ENOTDIR, "is not a folder", target)
            if _c().mount(_fd_path(source_fd), _fd_path(target_fd), None, MS_MOVE, None) != 0:
                _raise_errno(target)
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)


def _stage_mounted(stage: str) -> bool:
    return any(entry.mount_point == stage for entry in read_mountinfo())


def setup_stage(stage: str) -> None:
    """Create the scratch spot: an empty, private tmpfs that only root can enter.

    One left behind by an interrupted run is detached first. The folder must
    be a real directory owned by root (it lives in /run, where only root can
    write, so nobody else can have put anything there).
    """
    if _stage_mounted(stage):
        teardown_stage(stage)
    try:
        os.mkdir(stage, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(stage)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError(errno.EPERM, "is not a folder owned by root", stage)
    path = os.fsencode(stage)
    flags = MS_NOSUID | MS_NODEV | MS_NOEXEC
    if _c().mount(b"crenel-stage", path, b"tmpfs", flags, b"mode=700") != 0:
        _raise_errno(stage)
    if _c().mount(None, path, None, MS_PRIVATE, None) != 0:
        _raise_errno(stage)


def teardown_stage(stage: str) -> None:
    """Detach the scratch spot, with anything still in it, and remove its folder."""
    for _ in range(4):
        if not _stage_mounted(stage):
            break
        _c().umount2(os.fsencode(stage), MNT_DETACH | UMOUNT_NOFOLLOW)
    try:
        os.rmdir(stage)
    except OSError:
        pass


def _make_stage_dir(path: str, stage: str) -> None:
    """The folder a project is bound onto inside the scratch spot."""
    if os.path.dirname(path) != stage:
        raise OSError(errno.EINVAL, "is not directly inside the scratch spot", path)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass


def do_umount(path: str, force: bool) -> None:
    """Unmount `path`. Raises Busy if it is in use and `force` is off.

    The parent is opened without following links, and the last component is
    not followed either (UMOUNT_NOFOLLOW). The mount itself is not held
    open, since an open descriptor would keep it busy. With `force`, the
    mount is detached lazily (MNT_DETACH): it disappears from the path at
    once, but anything already open inside it stays usable until closed.
    """
    parent = open_nofollow(os.path.dirname(path))
    try:
        target = _fd_path(parent) + b"/" + os.fsencode(os.path.basename(path))
        flags = UMOUNT_NOFOLLOW | (MNT_DETACH if force else 0)
        if _c().umount2(target, flags) != 0:
            code = ctypes.get_errno()
            if code == errno.EINVAL:
                return  # not a mount point (any more): already done
            if code == errno.EBUSY:
                raise Busy(code, "is in use", path)
            raise OSError(code, os.strerror(code), path)
    finally:
        os.close(parent)


def do_rmdir(path: str) -> str:
    """Remove the empty mount point `path`. Returns a warning, or ""."""
    parent = open_nofollow(os.path.dirname(path))
    try:
        os.rmdir(os.path.basename(path), dir_fd=parent)
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno == errno.ENOTEMPTY:
            return f"{path} was left in place because it is not empty."
        raise
    finally:
        os.close(parent)
    return ""


# -- applying a request ------------------------------------------------------------------


def exposure(state: FolderState, protect_git: bool) -> int:
    """How much the VM can do to a folder in `state`, as a rank.

    0 hidden, 1 read, 2 write with every git protection asked for in place,
    3 write without them, 4 something unrecognized.
    """
    if state.mode == HIDDEN:
        return 0
    if state.mode == READ:
        return 1
    if state.mode == WRITE:
        wanted = expected_git(WRITE, protect_git, state.git_targets)
        return 2 if set(wanted) <= set(state.git_mounts) else 3
    return 4


def _desired_exposure(mode: str, protect_git: bool) -> int:
    return {HIDDEN: 0, READ: 1, WRITE: 2 if protect_git else 3}[mode]


def _run_step(step, request: Request, uid: int, gid: int, stage: str) -> str:
    """Carry out one step. Returns a warning, or ""."""
    if step.kind == UMOUNT:
        do_umount(step.path, request.force)
    elif step.kind == RMDIR:
        return do_rmdir(step.path)
    elif step.kind == MKDIR:
        do_mkdir(step.path, uid, gid)
    elif step.kind == BIND:
        if step.source != step.path:
            _make_stage_dir(step.path, stage)
        do_bind(step.source, step.path, uid)
    elif step.kind == PRIVATE:
        do_private(step.path)
    elif step.kind == READONLY:
        do_remount(step.path, True)
    elif step.kind == MOVE:
        do_move(step.source, step.path)
    else:
        raise OSError(errno.EINVAL, f"unknown step {step.kind}", step.path)
    return ""


def _state(request: Request, name: str) -> FolderState:
    return classify(name, request.root, request.share, read_mountinfo())


def _hide_everything(request: Request, name: str) -> None:
    """Detach every mount for `name`, ignoring failures: the last line of defense."""
    destination = os.path.join(request.share, name)
    for _ in range(8):  # stacked mounts come off one at a time
        under = mounts_under(read_mountinfo(), destination)
        if not under:
            break
        for entry in sorted(under, key=lambda e: e.mount_point.count("/"), reverse=True):
            try:
                parent = open_nofollow(os.path.dirname(entry.mount_point))
            except OSError:
                continue
            try:
                target = _fd_path(parent) + b"/" + os.fsencode(os.path.basename(entry.mount_point))
                _c().umount2(target, UMOUNT_NOFOLLOW | MNT_DETACH)
            finally:
                os.close(parent)
    try:
        do_rmdir(destination)
    except OSError:
        pass


def apply_folder(
    plan: FolderPlan, request: Request, uid: int, gid: int, stage: str = STAGE_DIR
) -> dict:
    """Carry out one folder's steps, check the result, and report it."""
    change = plan.change
    result = {
        "name": change.name,
        "mode": change.mode,
        "ok": True,
        "busy": False,
        "error": "",
        "warning": "",
    }
    before = _state(request, change.name)
    for step in plan.steps:
        try:
            warning = _run_step(step, request, uid, gid, stage)
        except Busy:
            result.update(ok=False, busy=True, error=f"{step.path} is in use.")
            break
        except OSError as exc:
            reason = exc.strerror or str(exc)
            result.update(ok=False, error=f"{render_step(step)}: {reason}")
            break
        if warning:
            result["warning"] = warning

    after = _state(request, change.name)
    if result["ok"] and (
        after.mode != change.mode
        or after.git_mounts != expected_git(change.mode, request.protect_git, after.git_targets)
    ):
        detail = f" ({after.detail})" if after.mode == CONFLICT else ""
        result.update(ok=False, error=f"Afterwards the folder is {after.mode}{detail}.")

    if not result["ok"] and after.mode == HIDDEN:
        # A failed share leaves no empty mount point behind for the VM to see.
        try:
            do_rmdir(os.path.join(request.share, change.name))
        except OSError:
            pass

    allowed = max(
        exposure(before, request.protect_git),
        _desired_exposure(change.mode, request.protect_git),
    )
    if exposure(after, request.protect_git) > allowed:
        _hide_everything(request, change.name)
        result.update(
            ok=False,
            error=(result["error"] + " " if result["error"] else "")
            + "It was hidden for safety, because it ended up more open than asked.",
        )
    return result


def apply_request(request: Request, uid: int, gid: int) -> list[dict]:
    """Carry out every change, with the scratch spot there for as long as it is needed.

    `STAGE_DIR` is read here, at call time, so the tests can point it at a
    folder of their own.
    """
    stage = STAGE_DIR
    plans = plan_changes(
        request.changes,
        request.root,
        request.share,
        request.protect_git,
        read_mountinfo(),
        stage=stage,
    )
    staging = any(plan.uses_stage for plan in plans)
    if staging:
        try:
            setup_stage(stage)
        except OSError as exc:
            reason = f"Could not create the scratch spot {stage}: {exc.strerror or exc}"
            return [
                {"name": p.change.name, "mode": p.change.mode, "ok": False, "busy": False,
                 "error": reason, "warning": ""}
                for p in plans
            ]
    try:
        return [apply_folder(plan, request, uid, gid, stage) for plan in plans]
    finally:
        if staging:
            teardown_stage(stage)


# -- entry point --------------------------------------------------------------------------


def caller_ids(environ=os.environ) -> tuple[int, int]:
    """The uid and gid of whoever ran pkexec (or sudo)."""
    for variable in ("PKEXEC_UID", "SUDO_UID"):
        value = environ.get(variable, "")
        if value.isdigit():
            uid = int(value)
            try:
                gid = pwd.getpwuid(uid).pw_gid
            except KeyError:
                raise RequestError(f"Unknown user id {uid}.") from None
            return uid, gid
    raise RequestError("Run through pkexec: there is no PKEXEC_UID to say who asked.")


def _reply(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    if os.geteuid() != 0:
        _reply({"error": "The Crenel helper has to run as root (through pkexec)."})
        return 2
    try:
        uid, gid = caller_ids()
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise RequestError("The request is too large.")
        try:
            data = json.loads(raw.decode("utf-8", "surrogateescape"))
        except ValueError as exc:
            raise RequestError(f"The request is not valid JSON: {exc}") from None
        request = validate_request(data, uid)
    except RequestError as exc:
        _reply({"error": str(exc)})
        return 2
    os.umask(0o022)
    _reply({"results": apply_request(request, uid, gid)})
    return 0


if __name__ == "__main__":  # pragma: no cover - started through pkexec
    sys.exit(main())
