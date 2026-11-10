"""The config file: what it holds, how it is read, and how it is written.

One YAML file at `CONFIG_PATH` holds the three things the settings dialog
edits: the projects folder whose subfolders the window lists, the share
folder the VM's virtiofs share points at, and whether projects shared Write
get their `.git` made read-only.

Every key is described once, in `KEYS`, and loading and rendering are driven
from that table. Adding a setting is a `Settings` field, a `KEYS` row and a
widget in the dialog. This is DupFinder's design, trimmed.

There are deliberately **no commands** in here. Everything that runs as root
is fixed in `helper.py`, so a config file (which anything running as you can
write) can never decide what root does.

Loading is forgiving: a missing file, malformed YAML, or a key of the wrong
type falls back to the defaults. Saving rewrites the whole file through a
temp file and `os.replace`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Any

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on how the app was started
    YAML_AVAILABLE = False


CONFIG_PATH = os.path.expanduser("~/.config/crenel/crenel-config.yaml")

# The share folder offered on first run: visible in the home folder, so it is
# obvious what the VM is pointed at. The projects folder has no default,
# since guessing would mean listing (and offering to share) the wrong tree.
DEFAULT_SHARE_DIR = "~/vm-share"
DEFAULT_PROTECT_GIT = True


@dataclass(frozen=True)
class Settings:
    """The config file as a record."""

    projects_root: str = ""
    share_dir: str = DEFAULT_SHARE_DIR
    protect_git: bool = DEFAULT_PROTECT_GIT

    @property
    def root_path(self) -> str:
        """The projects folder as a real absolute path, or "" if unset."""
        return real_path(self.projects_root) if self.projects_root else ""

    @property
    def share_path(self) -> str:
        """The share folder as a real absolute path, or "" if unset."""
        return real_path(self.share_dir) if self.share_dir else ""


def real_path(path: str) -> str:
    """`path` with "~" expanded, made absolute, and with symlinks resolved.

    The helper refuses any path that goes through a symlink, and mountinfo
    reports real paths, so this is the form every comparison uses.
    """
    return os.path.realpath(os.path.expanduser(path.strip()))


DEFAULTS = Settings()

FILE_COMMENT = """\
# Crenel configuration.
#
# Crenel rewrites this file when you press Save in the settings dialog, which
# reformats it: your own comments and blank lines here will not survive.
"""

ROOT_COMMENT = """\
  # The folder whose subfolders Crenel lists, one row per project. Only the
  # folders directly inside it are listed, never anything deeper.
"""

SHARE_COMMENT = """\
  # The folder the VM's virtiofs share points at. Crenel mounts each project
  # you share into it, so it must be empty apart from what Crenel puts there.
  # It must not be inside the projects folder, or contain it.
"""

PROTECT_COMMENT = """\
  # Whether a project shared Write gets its .git made read-only inside the
  # VM, so an agent cannot plant a hook (or a .git of its own) that runs on
  # the host the next time you use git there. Git in the VM can only read.
"""

STRING = "string"
BOOL = "bool"


@dataclass(frozen=True)
class Key:
    field: str
    section: str
    name: str
    kind: str
    comment: str


KEYS = (
    Key("projects_root", "folders", "projects_root", STRING, ROOT_COMMENT),
    Key("share_dir", "folders", "share_dir", STRING, SHARE_COMMENT),
    Key("protect_git", "protection", "protect_git", BOOL, PROTECT_COMMENT),
)

SECTIONS = ("folders", "protection")

assert sorted(key.field for key in KEYS) == sorted(f.name for f in fields(Settings))


# -- writing ----------------------------------------------------------------


def _quote(value: str) -> str:
    """`value` as a YAML double-quoted scalar."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _section(mapping: Any) -> dict:
    return mapping if isinstance(mapping, dict) else {}


def _indent_yaml(mapping: dict, indent: str) -> str:
    """Keys this version does not know, carried through a rewrite."""
    if not mapping or not YAML_AVAILABLE:
        return ""
    dumped = yaml.safe_dump(mapping, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return "".join(f"{indent}{line}\n" for line in dumped.splitlines())


def _render_key(key: Key, value: Any) -> str:
    text = ("true" if value else "false") if key.kind == BOOL else _quote(value)
    return f"{key.comment}  {key.name}: {text}\n"


def render_config(settings: Settings, config: dict | None = None) -> str:
    """The full text of a config file holding `settings`, keeping unknown keys."""
    config = _section(config)
    text = FILE_COMMENT
    for section in SECTIONS:
        keys = [key for key in KEYS if key.section == section]
        known = {key.name for key in keys}
        text += f"\n{section}:\n"
        text += "\n".join(_render_key(key, getattr(settings, key.field)) for key in keys)
        extra = {k: v for k, v in _section(config.get(section)).items() if k not in known}
        text += _indent_yaml(extra, "  ")
    extra_top = {k: v for k, v in config.items() if k not in SECTIONS}
    return text + _indent_yaml(extra_top, "")


def ensure_config() -> None:
    """Create `CONFIG_PATH` with the defaults if it isn't there yet."""
    if os.path.exists(CONFIG_PATH):
        return
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            handle.write(render_config(DEFAULTS))
    except OSError as exc:
        print(f"Crenel: could not create {CONFIG_PATH}: {exc}")


# -- reading ----------------------------------------------------------------


def read_config() -> tuple[dict, str | None]:
    """The parsed config, plus a message if it could not be read."""
    if not YAML_AVAILABLE:
        return {}, "PyYAML is not available, so the config file was not read."
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except FileNotFoundError:
        return {}, None
    except (OSError, yaml.YAMLError) as exc:
        print(f"Crenel: error loading {CONFIG_PATH}: {exc}")
        return {}, str(exc)
    if loaded is None:
        return {}, None
    if not isinstance(loaded, dict):
        return {}, "The config file does not contain a YAML mapping."
    return loaded, None


def _read_key(config: dict, key: Key, default: Any) -> Any:
    value = _section(config.get(key.section)).get(key.name)
    if key.kind == BOOL:
        # Only a real YAML boolean counts; "true" in quotes is a typo.
        return value if isinstance(value, bool) else default
    if not isinstance(value, str):
        return default
    return value.strip()


def load_settings() -> tuple[Settings, str | None]:
    """The file as a `Settings`, plus a message if it could not be read.

    Every field falls back to its default on its own, so one bad key costs
    only that key.
    """
    config, error = read_config()
    fallback = Settings()
    values = {key.field: _read_key(config, key, getattr(fallback, key.field)) for key in KEYS}
    return Settings(**values), error


def save_settings(settings: Settings) -> str | None:
    """Write `settings` to `CONFIG_PATH`; return an error message or None."""
    config, _error = read_config()
    text = render_config(settings, config)
    temporary = f"{CONFIG_PATH}.tmp"
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, CONFIG_PATH)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return f"Could not write {CONFIG_PATH}:\n\n{exc}"
    return None


def folder_problems(settings: Settings) -> list[str]:
    """Why `settings` cannot be used to list and share folders; [] if it can.

    A share folder that does not exist yet is not a problem: Crenel creates
    it (see `ensure_share_dir`). The helper checks all of this again, as root.
    """
    problems = []
    root, share = settings.root_path, settings.share_path
    if not root:
        problems.append("No projects folder is set. Choose one under Options ▸ Settings.")
    elif not os.path.isdir(root):
        problems.append(f"The projects folder does not exist:\n{root}")
    if not share:
        problems.append("No share folder is set. Choose one under Options ▸ Settings.")
    elif os.path.exists(share) and not os.path.isdir(share):
        problems.append(f"The share folder is not a folder:\n{share}")
    if root and share:
        if root == share:
            problems.append("The projects folder and the share folder must be different.")
        elif share.startswith(root.rstrip("/") + "/"):
            problems.append("The share folder must not be inside the projects folder.")
        elif root.startswith(share.rstrip("/") + "/"):
            problems.append("The projects folder must not be inside the share folder.")
    return problems


def ensure_share_dir(settings: Settings) -> str | None:
    """Create the share folder if it is missing. An error message, or None."""
    share = settings.share_path
    if not share or os.path.isdir(share):
        return None
    try:
        os.makedirs(share, mode=0o755, exist_ok=True)
    except OSError as exc:
        return f"Could not create the share folder {share}:\n{exc.strerror or exc}"
    return None
