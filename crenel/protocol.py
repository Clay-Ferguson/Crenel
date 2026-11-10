"""The window's side of talking to the root helper: the request it sends and
how the reply (or pkexec's own failure) is read. Pure Python, no Qt.

The helper's side, and the meaning of every field, is in `helper.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from .config import Settings
from .shares import Change

PROTOCOL = 2

# pkexec's own exit codes: the password dialog was dismissed, or polkit said
# no (or pkexec could not run the program at all).
PKEXEC_DISMISSED = 126
PKEXEC_NOT_AUTHORIZED = 127


def build_request(settings: Settings, changes: Iterable[Change], force: bool = False) -> dict:
    """The JSON object the helper reads from stdin."""
    return {
        "protocol": PROTOCOL,
        "root": settings.root_path,
        "share": settings.share_path,
        "protected": list(settings.protected_names),
        "force": force,
        "changes": [{"name": c.name, "mode": c.mode} for c in changes],
    }


def encode_request(request: dict) -> bytes:
    """`request` as the bytes written to the helper's stdin.

    ASCII-only JSON, so a folder name that is not valid UTF-8 (held as lone
    surrogates, the way `os` decodes it) travels as `\\udcXX` escapes and
    arrives as the same string.
    """
    return json.dumps(request, ensure_ascii=True).encode("ascii")


@dataclass(frozen=True)
class HelperReply:
    """What came back: one result per folder, or one error for the whole request."""

    results: tuple[dict, ...] = ()
    error: str = ""

    @property
    def busy(self) -> list[str]:
        return [r["name"] for r in self.results if r.get("busy")]

    @property
    def failures(self) -> list[dict]:
        return [r for r in self.results if not r.get("ok") and not r.get("busy")]

    @property
    def warnings(self) -> list[str]:
        return [r["warning"] for r in self.results if r.get("warning")]


def _tail(data: bytes, lines: int = 6) -> str:
    text = data.decode("utf-8", "replace").strip()
    return "\n".join(text.splitlines()[-lines:])


def parse_reply(exit_code: int, stdout: bytes, stderr: bytes) -> HelperReply:
    """Make sense of the helper's exit code and output, whatever happened."""
    if exit_code == PKEXEC_DISMISSED:
        return HelperReply(error="The password prompt was cancelled. Nothing was changed.")
    if exit_code == PKEXEC_NOT_AUTHORIZED:
        detail = _tail(stderr)
        return HelperReply(
            error="pkexec did not run Crenel's helper (not authorized, or it could not "
            "be started). Nothing was changed." + (f"\n\n{detail}" if detail else "")
        )
    try:
        payload = json.loads(stdout.decode("utf-8", "surrogateescape"))
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        if isinstance(payload.get("error"), str):
            return HelperReply(error=payload["error"])
        results = payload.get("results")
        if isinstance(results, list) and all(isinstance(r, dict) for r in results):
            return HelperReply(results=tuple(results))
    detail = _tail(stderr) or _tail(stdout)
    return HelperReply(
        error=f"Crenel's helper failed (exit code {exit_code})."
        + (f"\n\n{detail}" if detail else "")
    )
