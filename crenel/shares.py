"""What is shared right now, and the steps that change it. Pure Python, no Qt.

Crenel exposes a project to the VM by bind-mounting it into the *share
folder*, the one folder the VM's virtiofs share points at:

    <projects root>/ABC   --mount --bind-->   <share folder>/ABC

The kernel's own list of mounts (`/proc/self/mountinfo`) is therefore the
whole of Crenel's state. There is no database to drift out of step, and a
reboot, which drops every bind mount, puts every folder back to Hidden.

This module is shared by the window (which reads the state and previews the
steps) and the root helper (which carries the steps out and checks the
result). So both sides agree on what a state means and on what Apply will do.
See `helper.py` for how each step is executed safely.

**Why every share is built in a scratch spot and then moved in.** Ubuntu
marks the root filesystem "shared" (mount propagation), and virtiofsd runs in
its own mount namespace holding a linked copy of the share folder. That has
two consequences:

- A mount attached under the share folder is copied into virtiofsd's view at
  that moment, with the flags it has *then*. Making it read-only afterwards
  changes only the host's copy, and the VM's copy stays writable.
- A plain bind of a project joins the root filesystem's link group, so
  anything later mounted inside the share (the protected names) is mirrored
  back onto the real project on the host.

So a share is assembled complete (made private, read-only, protected) in
`STAGE_DIR`, a private tmpfs that nothing is linked to. Only then is it moved
into the share folder, which gives it a link group of its own, and the VM's
copy arrives finished. A change to an existing share is a fresh re-share,
never an in-place remount, because a remount would never reach the VM.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

# -- modes --------------------------------------------------------------------

HIDDEN = "hidden"
READ = "read"
WRITE = "write"
MODES = (HIDDEN, READ, WRITE)

# Not a mode anyone can choose. It is what a folder is when something is
# mounted at its place in the share folder that Crenel would not have put
# there: another directory, a stack of mounts, a read-write mount inside it.
CONFLICT = "conflict"

MOUNTINFO = "/proc/self/mountinfo"

# Protected names: folders or files directly inside a project shared Write
# that are each bind-mounted onto themselves read-only. A mount point cannot
# be changed, renamed, removed or replaced from the VM. The list is the
# user's (`Settings.protected`); `.git` is the usual entry, so nothing in the
# VM can plant a hook, point git at other hooks or another git directory, or
# swap `.git` for one of its own. Only names directly inside the project are
# allowed: a deeper path could be got around by renaming its parent folder,
# which stays writable.
NAME_MAX = 255

# The helper's scratch spot (see the module docstring): a private tmpfs that
# exists only while the helper runs, readable by root alone.
STAGE_DIR = "/run/crenel-stage"


# -- mountinfo ------------------------------------------------------------------

_ESCAPE = re.compile(r"\\([0-7]{3})")


def unescape(text: str) -> str:
    """A mountinfo path with its octal escapes decoded (`\\040` is a space).

    The kernel escapes exactly space, tab, newline and backslash, and leaves
    every other byte as it is.
    """
    return _ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), text)


@dataclass(frozen=True)
class MountEntry:
    """One line of /proc/self/mountinfo, reduced to what Crenel needs."""

    mount_point: str
    # The mount's own options (field 6): "rw" or "ro" plus nosuid, nodev and
    # the rest. Not the superblock's options, which come after the "-".
    options: tuple[str, ...]

    @property
    def read_only(self) -> bool:
        return "ro" in self.options


def parse_mountinfo(text: str) -> list[MountEntry]:
    """Every mount in `text`, in the kernel's order (later mounts stack on top).

    A malformed line is skipped rather than fatal: the format is stable, and
    one surprise in it should not blank the whole window.
    """
    entries = []
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) < 10 or "-" not in fields[6:]:
            continue
        entries.append(MountEntry(unescape(fields[4]), tuple(fields[5].split(","))))
    return entries


def read_mountinfo(path: str = MOUNTINFO) -> list[MountEntry]:
    """The mounts this process can see.

    Read with surrogateescape, the same way `os` decodes a filename, so a
    project whose name is not valid UTF-8 still compares equal to its path.
    """
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        return parse_mountinfo(handle.read())


def mounts_under(entries: Iterable[MountEntry], path: str) -> list[MountEntry]:
    """The mounts at `path` or anywhere below it, in mountinfo order."""
    prefix = path.rstrip("/") + "/"
    return [e for e in entries if e.mount_point == path or e.mount_point.startswith(prefix)]


# -- the filesystem --------------------------------------------------------------


def list_projects(root: str) -> list[str]:
    """The folders directly under `root`, sorted, without recursing.

    Symlinks are left out (a link is not a project, and the helper refuses
    them anyway), and so are dot-folders, which hold tool state rather than
    projects. A missing or unreadable root lists nothing.
    """
    try:
        with os.scandir(root) as listing:
            names = [
                entry.name
                for entry in listing
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".")
            ]
    except OSError:
        return []
    return sorted(names, key=str.casefold)


def stray_entries(share: str, entries: Sequence[MountEntry]) -> list[str]:
    """What is in the share folder that Crenel did not put there, sorted.

    The VM sees everything in the share folder, and can write to whatever is
    not a read-only share. So the only things expected in it are Crenel's
    mounts and empty folders (mount points no longer in use, as after a
    reboot, which show the VM a name and nothing else). Anything else (a
    file, a symlink, a folder with something in it) is listed. A missing or
    unreadable share folder lists nothing.
    """
    try:
        names = os.listdir(share)
    except OSError:
        return []
    stray = []
    for name in names:
        path = os.path.join(share, name)
        if mounts_under(entries, path):
            continue
        mode = _lstat_mode(path)
        if mode is not None and stat.S_ISDIR(mode):
            try:
                if not os.listdir(path):
                    continue
            except OSError:
                pass
        stray.append(name)
    return sorted(stray, key=str.casefold)


def valid_name(name: object) -> bool:
    """Whether `name` is one plain path component: no "/", no NUL, not "." or "..".

    The rule for a project's name and for a protected name alike.
    """
    return (
        isinstance(name, str)
        and 0 < len(os.fsencode(name)) <= NAME_MAX
        and "/" not in name
        and "\0" not in name
        and name not in (".", "..")
    )


def _lstat_mode(path: str) -> int | None:
    try:
        return os.lstat(path).st_mode
    except OSError:
        return None


def find_targets(source: str, names: Iterable[str]) -> tuple[str, ...]:
    """The protected `names` that the project at `source` really has, sorted.

    A name counts as a directory or as a file (`.git` is a file in a worktree
    or a submodule, whose `gitdir:` line could otherwise be pointed somewhere
    else). A symlink is never followed: a bind mount over a link target would
    protect something other than the project.
    """
    found = []
    for name in names:
        mode = _lstat_mode(os.path.join(source, name))
        if mode is not None and (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            found.append(name)
    return tuple(sorted(set(found)))


def same_dir(first: str, second: str) -> bool:
    """Whether two paths are the same folder or file (same device and inode)."""
    try:
        a, b = os.stat(first), os.stat(second)
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


# -- one folder's state ----------------------------------------------------------


@dataclass(frozen=True)
class FolderState:
    """What the VM can see of one project right now.

    `protections` are the read-only protected names mounted in place (sorted),
    and `targets` the ones on the list that this project has. `detail`
    explains a CONFLICT and is empty otherwise.
    """

    name: str
    mode: str
    protections: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    detail: str = ""


def classify(
    name: str,
    root: str,
    share: str,
    entries: Sequence[MountEntry],
    protected: Sequence[str],
    *,
    is_same_dir: Callable[[str, str], bool] = same_dir,
    targets: Callable[[str, Sequence[str]], tuple[str, ...]] = find_targets,
) -> FolderState:
    """Work out `name`'s mode from the mounts at and below `<share>/<name>`.

    A mount inside the project counts as a protection if it is a read-only
    bind of something directly inside the project onto itself, whether or
    not that name is on the `protected` list right now. One left over from
    an older list then shows as a change to apply, not as a conflict.
    """
    destination = os.path.join(share, name)
    source = os.path.join(root, name)
    under = mounts_under(entries, destination)
    found_targets = targets(source, protected)
    if not under:
        return FolderState(name, HIDDEN, targets=found_targets)

    main = [e for e in under if e.mount_point == destination]
    subs = [e for e in under if e.mount_point != destination]

    def conflict(detail: str) -> FolderState:
        return FolderState(name, CONFLICT, targets=found_targets, detail=detail)

    if not main:
        return conflict(f"Something is mounted inside {destination}, but not the project.")
    if len(main) > 1:
        return conflict(f"{len(main)} mounts are stacked on {destination}.")
    if not is_same_dir(destination, source):
        return conflict(f"{destination} is mounted from somewhere other than {source}.")

    protections = []
    for entry in subs:
        relative = os.path.relpath(entry.mount_point, destination)
        if (
            not valid_name(relative)
            or not entry.read_only
            or relative in protections
            or not is_same_dir(entry.mount_point, os.path.join(source, relative))
        ):
            return conflict(f"Unexpected mount inside the project: {entry.mount_point}")
        protections.append(relative)
    mode = READ if main[0].read_only else WRITE
    return FolderState(name, mode, tuple(sorted(protections)), found_targets)


def read_states(root: str, share: str, protected: Sequence[str]) -> dict[str, FolderState]:
    """Every project's state, by name.

    The names are the folders under `root` plus anything mounted directly
    under `share`. A project that has been renamed or deleted while it was
    shared then still shows up, as a CONFLICT that can be set to Hidden.
    """
    entries = read_mountinfo()
    names = set(list_projects(root))
    prefix = share.rstrip("/") + "/"
    for entry in entries:
        if entry.mount_point.startswith(prefix):
            names.add(entry.mount_point[len(prefix):].split("/")[0])
    return {
        name: classify(name, root, share, entries, protected)
        for name in sorted(names, key=str.casefold)
    }


def expected_protections(mode: str, targets: Sequence[str]) -> tuple[str, ...]:
    """The protections a folder in `mode` with these `targets` should have."""
    return tuple(sorted(targets)) if mode == WRITE else ()


def needs_change(state: FolderState, desired: str) -> bool:
    """Whether Apply has anything to do to make `state` match `desired`."""
    if state.mode != desired:
        return True
    return state.protections != expected_protections(desired, state.targets)


# -- the steps that change it ------------------------------------------------------

UMOUNT = "umount"
RMDIR = "rmdir"
MKDIR = "mkdir"
BIND = "bind"
PRIVATE = "private"
READONLY = "readonly"
MOVE = "move"


@dataclass(frozen=True)
class Step:
    """One thing the helper does. `source` is used by BIND and MOVE."""

    kind: str
    path: str
    source: str = ""


@dataclass(frozen=True)
class Change:
    """One folder Apply is asked to put into `mode`."""

    name: str
    mode: str


@dataclass
class FolderPlan:
    change: Change
    steps: list[Step] = field(default_factory=list)

    @property
    def uses_stage(self) -> bool:
        return any(step.kind == MOVE for step in self.steps)


def _deepest_first(entries: Sequence[MountEntry]) -> list[MountEntry]:
    """Unmount order: inner mounts before outer ones, top of a stack first."""
    indexed = list(enumerate(entries))
    indexed.sort(key=lambda pair: (pair[1].mount_point.count("/"), pair[0]), reverse=True)
    return [entry for _index, entry in indexed]


def plan_folder(
    change: Change,
    root: str,
    share: str,
    protected: Sequence[str],
    entries: Sequence[MountEntry],
    *,
    stage: str = STAGE_DIR,
    is_same_dir: Callable[[str, str], bool] = same_dir,
    targets: Callable[[str, Sequence[str]], tuple[str, ...]] = find_targets,
) -> FolderPlan:
    """The steps that take one folder from what `entries` says to `change.mode`.

    Whatever the folder was, everything mounted for it in the share folder
    comes off first, deepest first. Hidden stops there and removes the empty mount point. Read and
    Write then build the share complete in the scratch spot and move it into
    the share folder, so the VM never sees it half-made. See the module
    docstring for why a share is never adjusted in place.
    """
    destination = os.path.join(share, change.name)
    source = os.path.join(root, change.name)
    staged = os.path.join(stage, change.name)
    state = classify(
        change.name, root, share, entries, protected, is_same_dir=is_same_dir, targets=targets
    )
    plan = FolderPlan(change)
    steps = plan.steps

    steps.extend(
        Step(UMOUNT, e.mount_point) for e in _deepest_first(mounts_under(entries, destination))
    )
    if change.mode == HIDDEN:
        steps.append(Step(RMDIR, destination))
        return plan

    steps.append(Step(BIND, staged, source))
    steps.append(Step(PRIVATE, staged))
    if change.mode == READ:
        steps.append(Step(READONLY, staged))
    for relative in expected_protections(change.mode, state.targets):
        path = os.path.join(staged, relative)
        steps.append(Step(BIND, path, path))
        steps.append(Step(READONLY, path))
    steps.append(Step(MKDIR, destination))
    steps.append(Step(MOVE, destination, staged))
    return plan


def plan_changes(
    changes: Iterable[Change],
    root: str,
    share: str,
    protected: Sequence[str],
    entries: Sequence[MountEntry],
    **probes,
) -> list[FolderPlan]:
    """`plan_folder` for every change, in the order given."""
    return [plan_folder(c, root, share, protected, entries, **probes) for c in changes]


def stage_setup_commands(stage: str = STAGE_DIR) -> list[str]:
    """The scratch spot's creation, as commands (for the preview)."""
    path = shlex.quote(stage)
    return [
        f"mkdir -m 700 {path}",
        f"mount -t tmpfs -o mode=700,nosuid,nodev,noexec crenel-stage {path}",
        f"mount --make-private {path}",
    ]


def stage_teardown_commands(stage: str = STAGE_DIR) -> list[str]:
    path = shlex.quote(stage)
    return [f"umount -l {path}", f"rmdir {path}"]


def render_step(step: Step, force: bool = False) -> str:
    """`step` as the shell command that does the same thing.

    The helper does not run these: it makes the system calls itself, through
    file descriptors (see helper.py). These are for reading, in the Show
    Commands preview and in error messages.
    """
    path = shlex.quote(step.path)
    if step.kind == UMOUNT:
        return f"umount {'-l ' if force else ''}{path}"
    if step.kind == RMDIR:
        return f"rmdir {path}"
    if step.kind == MKDIR:
        return f"mkdir {path}"
    if step.kind == BIND:
        return f"mount --bind {shlex.quote(step.source)} {path}"
    if step.kind == PRIVATE:
        return f"mount --make-private {path}"
    if step.kind == READONLY:
        return f"mount -o remount,bind,ro {path}"
    if step.kind == MOVE:
        return f"mount --move {shlex.quote(step.source)} {path}"
    raise ValueError(f"unknown step {step.kind!r}")
