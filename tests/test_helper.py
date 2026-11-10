"""helper.py without root: what a request has to look like to be accepted.

The mounting itself is tested for real in test_helper_namespace.py.
"""

from __future__ import annotations

import os

import pytest

from crenel import helper
from crenel.helper import (
    MS_BIND,
    MS_NODEV,
    MS_NOSUID,
    MS_RDONLY,
    MS_RELATIME,
    MS_REMOUNT,
    MS_STRICTATIME,
    RequestError,
    caller_ids,
    exposure,
    open_nofollow,
    remount_flags,
    valid_name,
    validate_request,
)
from crenel.shares import CONFLICT, HIDDEN, READ, WRITE, FolderState, MountEntry

UID = os.getuid()


def request(root, share, **overrides):
    data = {
        "protocol": 1,
        "root": root,
        "share": share,
        "protect_git": True,
        "force": False,
        "changes": [{"name": "ABC", "mode": "write"}],
    }
    data.update(overrides)
    return data


@pytest.fixture
def folders(projects):
    root, share = projects
    os.mkdir(share)
    return root, share


def test_a_good_request(folders):
    root, share = folders
    parsed = validate_request(request(root, share), UID)
    assert parsed.root == root and parsed.changes[0].name == "ABC"


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "x\0y", "n" * 256, 7, None])
def test_bad_names(name):
    assert not valid_name(name)


def test_good_names():
    assert valid_name("My App") and valid_name("a.b") and valid_name("\udcff")


def refused(data, uid=UID):
    with pytest.raises(RequestError) as caught:
        validate_request(data, uid)
    return str(caught.value)


def test_refuses_relative_unnormalized_and_symlinked_paths(folders, tmp_path):
    root, share = folders
    assert "absolute" in refused(request("projects", share))
    assert "normalized" in refused(request(root + "/", share))
    assert "normalized" in refused(request(root + "/../projects", share))
    os.symlink(root, tmp_path / "link")
    assert "symlink" in refused(request(str(tmp_path / "link"), share))


def test_refuses_folders_owned_by_someone_else(folders):
    root, share = folders
    assert "not owned by you" in refused(request(root, share), uid=UID + 1)


def test_refuses_overlapping_folders(folders):
    root, _share = folders
    inner = os.path.join(root, "ABC")
    assert "contain each other" in refused(request(root, inner))
    assert "contain each other" in refused(request(root, root))


def test_refuses_bad_changes(folders):
    root, share = folders
    assert "folder name" in refused(request(root, share, changes=[{"name": "..", "mode": "read"}]))
    assert "one of" in refused(request(root, share, changes=[{"name": "ABC", "mode": "admin"}]))
    twice = [{"name": "ABC", "mode": "read"}, {"name": "ABC", "mode": "write"}]
    assert "twice" in refused(request(root, share, changes=twice))
    assert "protocol" in refused(request(root, share, protocol=99))
    assert "true or false" in refused(request(root, share, force="yes"))
    assert "JSON object" in refused([])


def test_caller_ids():
    assert caller_ids({"PKEXEC_UID": str(UID)})[0] == UID
    assert caller_ids({"SUDO_UID": str(UID)})[0] == UID
    with pytest.raises(RequestError):
        caller_ids({})


def test_open_nofollow_refuses_links_anywhere(tmp_path):
    (tmp_path / "real" / "inner").mkdir(parents=True)
    os.symlink(tmp_path / "real", tmp_path / "link")
    fd = open_nofollow(str(tmp_path / "real" / "inner"))
    os.close(fd)
    for path in (tmp_path / "link", tmp_path / "link" / "inner"):
        with pytest.raises(OSError):
            open_nofollow(str(path))


def test_remount_flags_keep_the_other_options():
    entry = MountEntry("/x", ("rw", "nosuid", "nodev", "relatime"))
    assert remount_flags(entry, True) == (
        MS_REMOUNT | MS_BIND | MS_RDONLY | MS_NOSUID | MS_NODEV | MS_RELATIME
    )
    assert remount_flags(MountEntry("/x", ("ro",)), False) == MS_REMOUNT | MS_BIND | MS_STRICTATIME


def test_exposure_ranks():
    git = (".git",)
    assert exposure(FolderState("a", HIDDEN), True) == 0
    assert exposure(FolderState("a", READ), True) == 1
    assert exposure(FolderState("a", WRITE, git, git), True) == 2
    assert exposure(FolderState("a", WRITE, (), git), True) == 3
    assert exposure(FolderState("a", WRITE, (), git), False) == 2
    assert exposure(FolderState("a", CONFLICT), True) == 4


def test_main_refuses_without_root(monkeypatch, capsys):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    assert helper.main() == 2
    assert "root" in capsys.readouterr().out
