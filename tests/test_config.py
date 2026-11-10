"""config.py: reading, writing, and checking the folders and protected names."""

from __future__ import annotations

import os

import yaml

from crenel import config
from crenel.config import (
    Settings,
    folder_problems,
    load_settings,
    protected_problems,
    save_settings,
)


def write(text):
    with open(config.CONFIG_PATH, "w") as handle:
        handle.write(text)


def test_round_trip(conf, projects):
    root, share = projects
    settings, error = load_settings()
    assert error is None
    assert settings == Settings(projects_root=root, share_dir=share, protected=(".git",))
    for names in ((), (".git", ".envrc", 'a "quoted" \\ name')):
        assert save_settings(Settings("~/x", "~/y", names)) is None
        assert load_settings()[0] == Settings("~/x", "~/y", names)
    with open(config.CONFIG_PATH) as handle:
        assert yaml.safe_load(handle)["protection"]["protected"] == list(names)


def test_missing_and_broken_files_fall_back_to_defaults(conf):
    os.unlink(config.CONFIG_PATH)
    assert load_settings() == (Settings(), None)
    with open(config.CONFIG_PATH, "w") as handle:
        handle.write("folders: [broken\n")
    settings, error = load_settings()
    assert settings == Settings() and error


def test_a_wrong_type_costs_only_that_key(conf):
    write('folders:\n  projects_root: "/p"\nprotection:\n  protected: ".envrc"\n')
    assert load_settings()[0] == Settings(projects_root="/p", protected=(".git",))


def test_names_that_cannot_be_protected_are_dropped(conf):
    write('protection:\n  protected: [".git", " .envrc ", "a/b", "..", 3, "", ".git"]\n')
    assert load_settings()[0].protected == (".git", ".envrc")


def test_comment_lines_are_kept_but_not_used(conf):
    names = (".git", "# .envrc", "#a/b", ".vscode")
    assert save_settings(Settings("~/x", "~/y", names)) is None
    settings = load_settings()[0]
    assert settings.protected == names
    assert settings.protected_names == (".git", ".vscode")
    assert protected_problems(settings.protected) == []


def test_the_old_git_switch_is_read_as_a_list(conf):
    write("protection:\n  protect_git: false\n")
    assert load_settings()[0].protected == ()
    write("protection:\n  protect_git: true\n")
    assert load_settings()[0].protected == (".git",)
    save_settings(load_settings()[0])
    with open(config.CONFIG_PATH) as handle:
        assert yaml.safe_load(handle)["protection"] == {"protected": [".git"]}
    write("protection:\n  protect_git: true\n  protected: []\n")
    assert load_settings()[0].protected == ()  # the list wins


def test_protected_problems():
    assert protected_problems([".git", ".envrc", "My Notes"]) == []
    assert "is a path" in protected_problems([".vscode/tasks.json"])[0]
    assert "is a path" in protected_problems(["/.git"])[0]
    assert "can be protected" in protected_problems([".."])[0]
    assert "too long" in protected_problems(["n" * 256])[0]
    assert len(protected_problems(["a/b", ".git", "c/d"])) == 2


def test_unknown_keys_survive_a_save(conf):
    with open(config.CONFIG_PATH, "a") as handle:
        handle.write("future:\n  thing: 3\n")
    save_settings(load_settings()[0])
    with open(config.CONFIG_PATH) as handle:
        assert yaml.safe_load(handle)["future"] == {"thing": 3}


def test_the_file_has_no_commands_in_it():
    text = config.render_config(Settings())
    assert "command" not in text.lower().replace("no commands", "")


def test_folder_problems(projects):
    root, share = projects
    assert folder_problems(Settings(root, share)) == []
    assert "No projects folder" in folder_problems(Settings("", share))[0]
    assert "does not exist" in folder_problems(Settings(root + "/nope", share))[0]
    assert "inside the projects" in folder_problems(Settings(root, root + "/ABC"))[0]
    os.makedirs(share + "/p")
    assert "inside the share" in folder_problems(Settings(share + "/p", share))[0]
    assert "different" in folder_problems(Settings(root, root))[0]


def test_paths_are_resolved(tmp_path):
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "link")
    settings = Settings(projects_root=str(tmp_path / "link"))
    assert settings.root_path == os.path.realpath(tmp_path / "real")
    assert Settings(share_dir="~/s").share_path == os.path.realpath(os.path.expanduser("~/s"))
