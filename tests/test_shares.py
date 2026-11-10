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
    git_targets,
    list_projects,
    needs_change,
    parse_mountinfo,
    plan_folder,
    render_step,
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
    return lambda _source: found


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


def test_git_targets(projects):
    root, _share = projects
    assert git_targets(os.path.join(root, "ABC")) == GIT
    assert git_targets(os.path.join(root, "DEF")) == ()


def test_git_targets_protects_a_git_file_but_never_a_symlink(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: elsewhere\n")
    assert git_targets(str(tmp_path / "wt")) == GIT
    (tmp_path / "linked").mkdir()
    os.symlink("/etc", tmp_path / "linked" / ".git")
    assert git_targets(str(tmp_path / "linked")) == ()


def classify_with(entries, is_same_dir=same, found=GIT):
    return classify(
        "ABC", ROOT, SHARE, entries, is_same_dir=is_same_dir, targets=targets(found)
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
    assert state.mode == WRITE and state.git_mounts == GIT


def test_a_sibling_with_a_longer_name_is_not_inside():
    assert classify_with([entry(SHARE + "/ABCD")]).mode == HIDDEN


def test_classify_conflicts():
    assert classify_with([entry(SHARE + "/ABC")], is_same_dir=differ).mode == CONFLICT
    assert classify_with([entry(SHARE + "/ABC"), entry(SHARE + "/ABC")]).mode == CONFLICT
    assert classify_with([entry(SHARE + "/ABC/sub")]).mode == CONFLICT
    rw_git = [entry(SHARE + "/ABC"), entry(SHARE + "/ABC/.git")]
    assert classify_with(rw_git).mode == CONFLICT
    # The old protections (hooks and config only) are not this one.
    old = [entry(SHARE + "/ABC"), entry(SHARE + "/ABC/.git/hooks", ro=True)]
    assert classify_with(old).mode == CONFLICT


def test_needs_change_counts_git_protection():
    write_bare = FolderState("ABC", WRITE, (), GIT)
    assert needs_change(write_bare, WRITE, protect_git=True)
    assert not needs_change(write_bare, WRITE, protect_git=False)
    assert not needs_change(FolderState("ABC", WRITE, GIT, GIT), WRITE, protect_git=True)
    assert needs_change(FolderState("ABC", READ), WRITE, protect_git=False)
    assert not needs_change(FolderState("ABC", HIDDEN), HIDDEN, protect_git=True)


def plan(mode, entries, protect=True, found=GIT, is_same_dir=same):
    return plan_folder(
        Change("ABC", mode), ROOT, SHARE, protect, entries,
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


def test_hidden_to_write_gets_its_git_protection_before_it_is_moved_in():
    assert plan(WRITE, []) == [
        Step(BIND, S, SRC),
        Step(PRIVATE, S),
        Step(BIND, S + "/.git", S + "/.git"),
        Step(READONLY, S + "/.git"),
        Step(MKDIR, D),
        Step(MOVE, D, S),
    ]


def test_write_without_git_protection():
    expected = [Step(BIND, S, SRC), Step(PRIVATE, S), Step(MKDIR, D), Step(MOVE, D, S)]
    assert plan(WRITE, [], found=()) == expected
    assert plan(WRITE, [], protect=False) == expected


def test_nothing_is_ever_changed_in_place():
    """A remount would change only the host's copy, never the VM's, so every
    change to a share takes it down and builds it again."""
    shared = [entry(D), entry(D + "/.git", ro=True)]
    for mode in (READ, WRITE):
        steps = plan(mode, shared, protect=mode == READ)
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
