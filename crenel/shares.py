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
  anything later mounted inside the share (the git protections) is mirrored
  back onto the real project on the host.

So a share is assembled complete (made private, read-only, git-protected) in
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

# What "protect git" makes read-only inside a project shared Write: the hooks
# git runs, and the config that could point git at other hooks
# (`core.hooksPath`). Relative to the project.
GIT_DIR = ".git"
GIT_PROTECTED = (".git/hooks", ".git/config")

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


def _lstat_mode(path: str) -> int | None:
    try:
        return os.lstat(path).st_mode
    except OSError:
        return None


def git_targets(source: str) -> tuple[str, ...]:
    """The entries of `GIT_PROTECTED` that a project really has.

    Only a real `.git` directory counts. When `.git` is a file (a worktree or
    a submodule), the hooks and config are somewhere else and there is nothing
    here to protect. Symlinks are never followed: a bind mount over a link
    target would protect something other than the project.
    """
    git_mode = _lstat_mode(os.path.join(source, GIT_DIR))
    if git_mode is None or not stat.S_ISDIR(git_mode):
        return ()
    found = []
    for relative in GIT_PROTECTED:
        mode = _lstat_mode(os.path.join(source, relative))
        if mode is None:
            continue
        if relative.endswith("hooks") and stat.S_ISDIR(mode):
            found.append(relative)
        elif relative.endswith("config") and stat.S_ISREG(mode):
            found.append(relative)
    return tuple(sorted(found))


def same_dir(first: str, second: str) -> bool:
    """Whether two paths are the same directory (same device and inode)."""
    try:
        a, b = os.stat(first), os.stat(second)
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


# -- one folder's state ----------------------------------------------------------


@dataclass(frozen=True)
class FolderState:
    """What the VM can see of one project right now.

    `git_mounts` are the read-only protections in place (relative paths,
    sorted), and `git_targets` the ones this project could have. `detail`
    explains a CONFLICT and is empty otherwise.
    """

    name: str
    mode: str
    git_mounts: tuple[str, ...] = ()
    git_targets: tuple[str, ...] = ()
    detail: str = ""


def classify(
    name: str,
    root: str,
    share: str,
    entries: Sequence[MountEntry],
    *,
    is_same_dir: Callable[[str, str], bool] = same_dir,
    targets: Callable[[str], tuple[str, ...]] = git_targets,
) -> FolderState:
    """Work out `name`'s mode from the mounts at and below `<share>/<name>`."""
    destination = os.path.join(share, name)
    source = os.path.join(root, name)
    under = mounts_under(entries, destination)
    found_targets = targets(source)
    if not under:
        return FolderState(name, HIDDEN, git_targets=found_targets)

    main = [e for e in under if e.mount_point == destination]
    subs = [e for e in under if e.mount_point != destination]

    def conflict(detail: str) -> FolderState:
        return FolderState(name, CONFLICT, git_targets=found_targets, detail=detail)

    if not main:
        return conflict(f"Something is mounted inside {destination}, but not the project.")
    if len(main) > 1:
        return conflict(f"{len(main)} mounts are stacked on {destination}.")
    if not is_same_dir(destination, source):
        return conflict(f"{destination} is mounted from somewhere other than {source}.")

    git_mounts = []
    for entry in subs:
        relative = os.path.relpath(entry.mount_point, destination)
        if relative not in GIT_PROTECTED or not entry.read_only or relative in git_mounts:
            return conflict(f"Unexpected mount inside the project: {entry.mount_point}")
        git_mounts.append(relative)
    mode = READ if main[0].read_only else WRITE
    return FolderState(name, mode, tuple(sorted(git_mounts)), found_targets)


def read_states(root: str, share: str) -> dict[str, FolderState]:
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
        name: classify(name, root, share, entries)
        for name in sorted(names, key=str.casefold)
    }


def expected_git(mode: str, protect_git: bool, targets: Sequence[str]) -> tuple[str, ...]:
    """The git protections a folder in `mode` should have."""
    return tuple(sorted(targets)) if mode == WRITE and protect_git else ()


def needs_change(state: FolderState, desired: str, protect_git: bool) -> bool:
    """Whether Apply has anything to do to make `state` match `desired`."""
    if state.mode != desired:
        return True
    return state.git_mounts != expected_git(desired, protect_git, state.git_targets)


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
    protect_git: bool,
    entries: Sequence[MountEntry],
    *,
    stage: str = STAGE_DIR,
    is_same_dir: Callable[[str, str], bool] = same_dir,
    targets: Callable[[str], tuple[str, ...]] = git_targets,
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
        change.name, root, share, entries, is_same_dir=is_same_dir, targets=targets
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
    for relative in expected_git(change.mode, protect_git, state.git_targets):
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
    protect_git: bool,
    entries: Sequence[MountEntry],
    **probes,
) -> list[FolderPlan]:
    """`plan_folder` for every change, in the order given."""
    return [plan_folder(c, root, share, protect_git, entries, **probes) for c in changes]


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
