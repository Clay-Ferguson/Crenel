"""shares.py: reading the mount table, a folder's state, and the steps to change it."""

from __future__ import annotations

import os

from crenel.shares import (
    BIND,
    CONFLICT,
    HIDDEN,
    MKDIR,
    MOVE,
    PRIVATE,
    READ,
    READONLY,
    RMDIR,
    UMOUNT,
    WRITE,
    Change,
    FolderState,
    MountEntry,
    Step,
    classify,
    find_targets,
    list_projects,
    needs_change,
    parse_mountinfo,
    plan_folder,
    render_step,
    stray_entries,
    unescape,
)

ROOT = "/home/me/projects"
SHARE = "/home/me/vm-share"
STAGE = "/run/crenel-stage"
GIT = (".git",)

MOUNTINFO = (
    "22 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n"
    "90 22 259:2 /home/me/projects/My\\040App /home/me/vm-share/My\\040App "
    "ro,nosuid,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n"
    "garbage line\n"
)


def entry(path, ro=False):
    return MountEntry(path, ("ro" if ro else "rw", "relatime"))


def same(_a, _b):
    return True


def differ(_a, _b):
    return False


def targets(found=GIT):
    """A stand-in for `find_targets`: the project has `found`."""
    return lambda _source, names: tuple(sorted(n for n in found if n in names))


def test_unescape():
    assert unescape("a\\040b\\011c\\134d") == "a b\tc\\d"


def test_parse_mountinfo_reads_the_mount_point_and_its_own_options():
    entries = parse_mountinfo(MOUNTINFO)
    assert len(entries) == 2  # the garbage line is skipped
    assert entries[1].mount_point == "/home/me/vm-share/My App"
    assert entries[1].read_only and not entries[0].read_only


def test_list_projects_lists_real_folders_only(projects):
    root, _share = projects
    assert list_projects(root) == ["ABC", "DEF", "ghi"]
    assert list_projects(root + "/missing") == []


def test_find_targets(projects):
    root, _share = projects
    assert find_targets(os.path.join(root, "ABC"), GIT) == GIT
    assert find_targets(os.path.join(root, "ABC"), ()) == ()
    assert find_targets(os.path.join(root, "DEF"), GIT) == ()


def test_find_targets_takes_folders_and_files_but_never_a_symlink(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: elsewhere\n")
    (tmp_path / "wt" / ".vscode").mkdir()
    (tmp_path / "wt" / ".envrc").write_text("use nix\n")
    names = (".vscode", ".git", "Makefile", ".envrc", ".git")
    assert find_targets(str(tmp_path / "wt"), names) == (".envrc", ".git", ".vscode")
    (tmp_path / "linked").mkdir()
    os.symlink("/etc", tmp_path / "linked" / ".git")
    assert find_targets(str(tmp_path / "linked"), GIT) == ()


def classify_with(entries, is_same_dir=same, found=GIT, protected=GIT):
    return classify(
        "ABC", ROOT, SHARE, entries, protected, is_same_dir=is_same_dir, targets=targets(found)
    )


def test_classify_hidden_read_write():
    assert classify_with([]).mode == HIDDEN
    assert classify_with([entry(SHARE + "/ABC", ro=True)]).mode == READ
    state = classify_with(
        [
            entry(SHARE + "/ABC"),
            entry(SHARE + "/ABC/.git", ro=True),
        ]
    )
    assert state.mode == WRITE and state.protections == GIT


def test_a_sibling_with_a_longer_name_is_not_inside():
    assert classify_with([entry(SHARE + "/ABCD")]).mode == HIDDEN


def test_classify_conflicts():
    assert classify_with([entry(SHARE + "/ABC")], is_same_dir=differ).mode == CONFLICT
    assert classify_with([entry(SHARE + "/ABC"), entry(SHARE + "/ABC")]).mode == CONFLICT
    assert classify_with([entry(SHARE + "/ABC/sub")]).mode == CONFLICT
    rw_git = [entry(SHARE + "/ABC"), entry(SHARE + "/ABC/.git")]
    assert classify_with(rw_git).mode == CONFLICT
    # Only names directly inside the project are protections, never deeper.
    old = [entry(SHARE + "/ABC"), entry(SHARE + "/ABC/.git/hooks", ro=True)]
    assert classify_with(old).mode == CONFLICT


def test_a_protection_must_be_the_project_s_own():
    """A read-only mount of something else is not a protection."""
    sub = SHARE + "/ABC/.git"
    foreign = [entry(SHARE + "/ABC"), entry(sub, ro=True)]
    assert classify_with(foreign, is_same_dir=lambda a, _b: a != sub).mode == CONFLICT


def test_a_protection_no_longer_listed_is_a_change_not_a_conflict():
    shared = [entry(SHARE + "/ABC"), entry(SHARE + "/ABC/.envrc", ro=True)]
    state = classify_with(shared, found=(".envrc", ".git"), protected=GIT)
    assert state.mode == WRITE
    assert state.protections == (".envrc",) and state.targets == GIT
    assert needs_change(state, WRITE)


def test_needs_change_counts_protections():
    write_bare = FolderState("ABC", WRITE, (), GIT)
    assert needs_change(write_bare, WRITE)
    assert not needs_change(FolderState("ABC", WRITE), WRITE)
    assert not needs_change(FolderState("ABC", WRITE, GIT, GIT), WRITE)
    assert needs_change(FolderState("ABC", WRITE, GIT), WRITE)  # taken off the list
    assert needs_change(FolderState("ABC", READ), WRITE)
    assert not needs_change(FolderState("ABC", HIDDEN, targets=GIT), HIDDEN)
    assert not needs_change(FolderState("ABC", READ, targets=GIT), READ)


def plan(mode, entries, protected=GIT, found=GIT, is_same_dir=same):
    return plan_folder(
        Change("ABC", mode), ROOT, SHARE, protected, entries,
        stage=STAGE, is_same_dir=is_same_dir, targets=targets(found),
    ).steps


D = SHARE + "/ABC"
S = STAGE + "/ABC"
SRC = ROOT + "/ABC"


def test_hidden_to_read_is_built_read_only_then_moved_in():
    assert plan(READ, []) == [
        Step(BIND, S, SRC),
        Step(PRIVATE, S),
        Step(READONLY, S),
        Step(MKDIR, D),
        Step(MOVE, D, S),
    ]


def test_hidden_to_write_gets_its_protections_before_it_is_moved_in():
    assert plan(WRITE, []) == [
        Step(BIND, S, SRC),
        Step(PRIVATE, S),
        Step(BIND, S + "/.git", S + "/.git"),
        Step(READONLY, S + "/.git"),
        Step(MKDIR, D),
        Step(MOVE, D, S),
    ]


def test_write_protects_every_listed_name_the_project_has():
    steps = plan(WRITE, [], protected=(".git", ".envrc", "Makefile"), found=(".envrc", ".git"))
    assert steps[2:6] == [
        Step(BIND, S + "/.envrc", S + "/.envrc"),
        Step(READONLY, S + "/.envrc"),
        Step(BIND, S + "/.git", S + "/.git"),
        Step(READONLY, S + "/.git"),
    ]
    assert steps[6:] == [Step(MKDIR, D), Step(MOVE, D, S)]


def test_write_without_protections():
    expected = [Step(BIND, S, SRC), Step(PRIVATE, S), Step(MKDIR, D), Step(MOVE, D, S)]
    assert plan(WRITE, [], found=()) == expected
    assert plan(WRITE, [], protected=()) == expected


def test_nothing_is_ever_changed_in_place():
    """A remount would change only the host's copy, never the VM's, so every
    change to a share takes it down and builds it again."""
    shared = [entry(D), entry(D + "/.git", ro=True)]
    for mode in (READ, WRITE):
        steps = plan(mode, shared, protected=GIT if mode == READ else ())
        assert steps[:2] == [Step(UMOUNT, D + "/.git"), Step(UMOUNT, D)]
        assert steps[-1] == Step(MOVE, D, S)
    assert not [s for s in plan(WRITE, [entry(D, ro=True)]) if s.path == D and s.kind == READONLY]


def test_hide_unmounts_deepest_first_then_removes_the_folder():
    entries = [entry(D), entry(D + "/.git", ro=True)]
    expected = [Step(UMOUNT, D + "/.git"), Step(UMOUNT, D), Step(RMDIR, D)]
    assert plan(HIDDEN, entries) == expected


def test_a_conflict_is_replaced():
    steps = plan(READ, [entry(D)], is_same_dir=differ)
    assert steps[:2] == [Step(UMOUNT, D), Step(BIND, S, SRC)]


def test_render_step_quotes_paths():
    bind = render_step(Step(BIND, "/s/My App", "/p/My App"))
    assert bind == "mount --bind '/p/My App' '/s/My App'"
    assert render_step(Step(UMOUNT, "/s/x"), force=True) == "umount -l /s/x"
    assert render_step(Step(READONLY, "/s/x")) == "mount -o remount,bind,ro /s/x"
    assert render_step(Step(MOVE, "/s/x", "/run/st/x")) == "mount --move /run/st/x /s/x"
    assert render_step(Step(PRIVATE, "/run/st/x")) == "mount --make-private /run/st/x"


def test_stray_entries_allows_only_mounts_and_empty_folders(tmp_path):
    share = tmp_path / "vm-share"
    (share / "unused").mkdir(parents=True)          # an empty mount point: fine
    (share / "ABC").mkdir()
    (share / "ABC" / "file").write_text("x")       # a share: its mount is listed below
    (share / "full").mkdir()
    (share / "full" / "file").write_text("x")      # a folder with something in it
    (share / "notes.txt").write_text("x")
    (share / ".hidden").write_text("x")
    os.symlink(share / "unused", share / "link")    # a link, even to an empty folder
    entries = [entry(str(share / "ABC"))]
    assert stray_entries(str(share), entries) == [".hidden", "full", "link", "notes.txt"]
    assert stray_entries(str(tmp_path / "missing"), []) == []

