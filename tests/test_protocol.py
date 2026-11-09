"""protocol.py: the request the window sends and how replies are read."""

from __future__ import annotations

import json

from crenel.config import Settings
from crenel.protocol import build_request, encode_request, parse_reply
from crenel.shares import Change


def test_build_and_encode(projects):
    root, share = projects
    settings = Settings(projects_root=root, share_dir=share, protect_git=False)
    request = build_request(settings, [Change("ABC", "read"), Change("\udcff", "write")], True)
    assert request["root"] == root and request["share"] == share
    assert request["force"] is True and request["protect_git"] is False
    data = encode_request(request)
    assert data.isascii()
    assert json.loads(data)["changes"][1]["name"] == "\udcff"


def test_results():
    payload = {"results": [
        {"name": "A", "ok": True, "busy": False, "error": "", "warning": "left"},
        {"name": "B", "ok": False, "busy": True, "error": "in use", "warning": ""},
        {"name": "C", "ok": False, "busy": False, "error": "nope", "warning": ""},
    ]}
    reply = parse_reply(0, json.dumps(payload).encode(), b"")
    assert reply.error == ""
    assert reply.busy == ["B"]
    assert [f["name"] for f in reply.failures] == ["C"]
    assert reply.warnings == ["left"]


def test_errors():
    assert "cancelled" in parse_reply(126, b"", b"").error
    assert "not authorized" in parse_reply(127, b"", b"Error executing command").error
    assert parse_reply(2, b'{"error": "bad name"}', b"").error == "bad name"
    crashed = parse_reply(1, b"", b"Traceback...\nKeyError: x")
    assert "exit code 1" in crashed.error and "KeyError" in crashed.error
