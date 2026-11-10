"""The helper making real mounts, inside a throwaway user+mount namespace,
set up the way a desktop host is.

`unshare -rm` gives an unprivileged process its own mount table, in which it
is root. The bind mounts made there vanish when it exits and are never
visible to the rest of the system. Inside it the driver:

- marks the root filesystem shared (`mount --make-rshared /`), as systemd
  does on the host. Without this, the mount propagation that matters here
  never happens, and a test can pass on code that fails on a real machine
  (it did once);
- starts a "VM view": a nested mount namespace that is a *slave* of the
  test's, which is exactly how virtiofsd sees the share folder. Writes are
  tried there to see what the VM can really do;
- runs the helper the way pkexec would (`main()`, JSON on stdin, PKEXEC_UID),
  with the scratch spot moved to a folder of the test's own.

It prints what it saw as JSON for these tests to assert on. Skipped where
unprivileged user namespaces are not allowed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# The VM's side: answers "w <path>" with whether it can write there, and
# "r <path>" with whether it can rename it (to <path>-old).
VM_VIEW = r"""
import sys
for line in sys.stdin:
    kind, path = line.rstrip("\n").split(" ", 1)
    try:
        if kind == "w":
            open(path, "a").close()
        elif kind == "r":
            __import__("os").rename(path, path + "-old")
        elif kind == "l":
            print(" ".join(sorted(__import__("os").listdir(path))), flush=True)
            continue
        print("yes", flush=True)
    except OSError:
        print("no", flush=True)
"""

DRIVER = r"""
import io, json, os, subprocess, sys
sys.path.insert(0, sys.argv[1])
from crenel import helper, shares
root, share, base = sys.argv[2], sys.argv[3], sys.argv[4]
script = json.loads(sys.argv[5])
subprocess.run(["mount", "--make-rshared", "/"], check=True)
helper.STAGE_DIR = os.path.join(base, "stage")
os.environ["PKEXEC_UID"] = str(os.getuid())
vm = subprocess.Popen(
    ["unshare", "-m", "--propagation", "slave", sys.executable, "-c", sys.argv[6]],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
)
out = []
held = []

def ask_vm(kind, path):
    vm.stdin.write(f"{kind} {path}\n")
    vm.stdin.flush()
    return vm.stdout.readline().strip()

def run(changes, force=False, protect=True):
    request = {"protocol": 1, "root": root, "share": share, "protect_git": protect,
               "force": force, "changes": [{"name": n, "mode": m} for n, m in changes]}
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))
    buffer = io.StringIO()
    real, sys.stdout = sys.stdout, buffer
    try:
        code = helper.main()
    finally:
        sys.stdout = real
    reply = json.loads(buffer.getvalue())
    states = {
        n: [s.mode, list(s.git_mounts)]
        for n, s in shares.read_states(root, share).items()
    }
    return {"code": code, "reply": reply, "states": states}

def can_write(path):
    try:
        with open(path, "a") as handle:
            handle.write("")
        return True
    except OSError:
        return False

def mounts_on_projects():
    return [e.mount_point for e in shares.read_mountinfo()
            if e.mount_point.startswith(root + "/")]

for op in script:
    kind = op[0]
    if kind == "apply":
        out.append(run(op[1], **op[2]))
    elif kind == "writable":          # through the share, from the host
        out.append(can_write(os.path.join(share, op[1])))
    elif kind == "host":              # the real project, from the host
        out.append(can_write(os.path.join(root, op[1])))
    elif kind == "vm":                # through the share, from the VM's view
        out.append(ask_vm("w", os.path.join(share, op[1])) == "yes")
    elif kind == "vm_rename":
        out.append(ask_vm("r", os.path.join(share, op[1])) == "yes")
    elif kind == "vm_list":
        out.append(ask_vm("l", share).split())
    elif kind == "host_mounts":
        out.append(mounts_on_projects())
    elif kind == "hold":
        held.append(os.open(os.path.join(share, op[1]), os.O_RDONLY))
        out.append(True)
    elif kind == "release":
        for fd in held:
            os.close(fd)
        held.clear()
        out.append(True)
    elif kind == "listdir":
        out.append(sorted(os.listdir(share)))
vm.stdin.close()
vm.wait()
print(json.dumps(out))
"""


def _namespaces_work() -> bool:
    if shutil.which("unshare") is None:
        return False
    probe = subprocess.run(["unshare", "-rm", "true"], capture_output=True)
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not _namespaces_work(), reason="no unprivileged namespaces")


def drive(root, share, script):
    os.makedirs(share, exist_ok=True)
    base = os.path.dirname(share)
    result = subprocess.run(
        ["unshare", "-rm", "--propagation", "unchanged", sys.executable, "-I", "-c", DRIVER,
         str(REPO), root, share, base, json.dumps(script), VM_VIEW],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_read_is_read_only_in_the_vm(projects):
    root, share = projects
    out = drive(root, share, [
        ["apply", [["DEF", "read"]], {}],
        ["vm", "DEF/new-file"],
        ["writable", "DEF/new-file"],
        ["host", "DEF/new-file"],
    ])
    assert out[0]["reply"]["results"][0]["ok"]
    assert out[0]["states"]["DEF"] == ["read", []]
    assert out[1:] == [False, False, True]  # VM no, share no, your own folder yes


def test_write_protects_git_in_the_vm_and_nowhere_else(projects):
    root, share = projects
    out = drive(root, share, [
        ["apply", [["ABC", "write"]], {}],
        ["vm", "ABC/new-file"],
        ["vm", "ABC/.git/config"],
        ["vm", "ABC/.git/hooks/pre-commit"],
        ["vm", "ABC/.git/commondir"],
        ["vm_rename", "ABC/.git"],
        ["host", "ABC/.git/config"],
        ["host", "ABC/.git/hooks/pre-commit"],
        ["host_mounts"],
    ])
    assert out[0]["states"]["ABC"] == ["write", [".git"]]
    # The VM writes the project, but nothing in .git, and cannot swap .git out.
    assert out[1:6] == [True, False, False, False, False]
    assert out[6:8] == [True, True]  # your own .git stays writable
    assert out[8] == []  # nothing mounted on the real project


def test_switching_modes_reaches_the_vm(projects):
    root, share = projects
    out = drive(root, share, [
        ["apply", [["ABC", "write"], ["DEF", "read"]], {}],
        ["apply", [["ABC", "read"], ["DEF", "write"]], {}],
        ["vm", "ABC/x"],
        ["vm", "DEF/x"],
        ["apply", [["ABC", "hidden"], ["DEF", "hidden"]], {}],
        ["vm_list"],
        ["listdir"],
    ])
    assert out[1]["states"]["ABC"] == ["read", []]
    assert out[1]["states"]["DEF"] == ["write", []]
    assert out[2:4] == [False, True]
    assert out[4]["states"]["ABC"][0] == "hidden"
    assert out[5] == [] and out[6] == []  # gone from the VM and the share folder


def test_git_protection_can_be_turned_off(projects):
    root, share = projects
    out = drive(root, share, [
        ["apply", [["ABC", "write"]], {}],
        ["apply", [["ABC", "write"]], {"protect": False}],
        ["vm", "ABC/.git/config"],
    ])
    assert out[1]["states"]["ABC"] == ["write", []]
    assert out[2] is True


def test_busy_then_forced(projects):
    root, share = projects
    out = drive(root, share, [
        ["apply", [["DEF", "write"]], {}],
        ["hold", "DEF"],
        ["apply", [["DEF", "hidden"]], {}],
        ["apply", [["DEF", "hidden"]], {"force": True}],
        ["release"],
    ])
    busy = out[2]["reply"]["results"][0]
    assert busy["busy"] and not busy["ok"]
    assert out[2]["states"]["DEF"][0] == "write"
    assert out[3]["reply"]["results"][0]["ok"]
    assert out[3]["states"]["DEF"][0] == "hidden"


def test_the_scratch_spot_is_cleaned_up(projects):
    root, share = projects
    drive(root, share, [["apply", [["DEF", "read"]], {}]])
    assert not os.path.exists(os.path.join(os.path.dirname(share), "stage"))


def test_a_symlinked_project_is_refused(projects):
    root, share = projects
    out = drive(root, share, [["apply", [["link-to-DEF", "read"]], {}], ["listdir"]])
    result = out[0]["reply"]["results"][0]
    assert not result["ok"]
    assert "link-to-DEF" not in out[0]["states"]
    assert out[0]["states"]["DEF"][0] == "hidden"
    assert out[1] == []


def test_a_bad_request_changes_nothing(projects):
    root, share = projects
    out = drive(root, share, [["apply", [["../etc", "read"]], {}], ["listdir"]])
    assert out[0]["code"] == 2 and "folder name" in out[0]["reply"]["error"]
    assert out[1] == []


def test_a_git_file_is_protected_too(projects):
    """A worktree's `.git` file could otherwise be pointed at another gitdir."""
    root, share = projects
    with open(os.path.join(root, "ghi", ".git"), "w") as handle:
        handle.write("gitdir: /elsewhere\n")
    out = drive(root, share, [
        ["apply", [["ghi", "write"]], {}],
        ["vm", "ghi/.git"],
        ["vm_rename", "ghi/.git"],
        ["vm", "ghi/new-file"],
    ])
    assert out[0]["states"]["ghi"] == ["write", [".git"]]
    assert out[1:] == [False, False, True]
