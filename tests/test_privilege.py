"""privilege.py: the command that runs the helper as root."""

from __future__ import annotations

import os

from crenel import privilege


def test_pkexec_is_never_looked_up_on_path(tmp_path, monkeypatch):
    fake = tmp_path / "pkexec"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    argv = privilege.helper_argv()
    assert argv is None or argv[0] == "/usr/bin/pkexec"


def test_the_checkout_helper_runs_isolated_and_writes_no_bytecode(tmp_path, monkeypatch):
    pkexec = tmp_path / "pkexec"
    pkexec.write_text("#!/bin/sh\n")
    pkexec.chmod(0o755)
    monkeypatch.setattr(privilege, "PKEXEC", str(pkexec))
    argv = privilege.helper_argv()
    assert argv is not None
    assert argv[:5] == [str(pkexec), "/usr/bin/python3", "-I", "-B", "-c"]


def test_no_pkexec_means_no_command(tmp_path, monkeypatch):
    monkeypatch.setattr(privilege, "PKEXEC", str(tmp_path / "missing"))
    assert privilege.helper_argv() is None
