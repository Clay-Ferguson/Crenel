"""config.py: reading, writing, and checking the folders."""

from __future__ import annotations

import os

import yaml

from crenel import config
from crenel.config import Settings, folder_problems, load_settings, save_settings


def test_round_trip(conf, projects):
    root, share = projects
    settings, error = load_settings()
    assert error is None
    assert settings == Settings(projects_root=root, share_dir=share, protect_git=True)
    assert save_settings(Settings(projects_root="~/x", share_dir="~/y", protect_git=False)) is None
    assert load_settings()[0] == Settings("~/x", "~/y", False)


def test_missing_and_broken_files_fall_back_to_defaults(conf):
    os.unlink(config.CONFIG_PATH)
    assert load_settings() == (Settings(), None)
    with open(config.CONFIG_PATH, "w") as handle:
        handle.write("folders: [broken\n")
    settings, error = load_settings()
    assert settings == Settings() and error


def test_a_wrong_type_costs_only_that_key(conf):
    with open(config.CONFIG_PATH, "w") as handle:
        handle.write('folders:\n  projects_root: "/p"\nprotection:\n  protect_git: "no"\n')
    assert load_settings()[0] == Settings(projects_root="/p", protect_git=True)


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
