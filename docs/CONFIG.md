# Crenel configuration file

Crenel keeps its settings in `~/.config/crenel/crenel-config.yaml`. It is created on the first run, and rewritten whenever you press **Save** in Options ▸ Settings. Comments and blank lines you add yourself do not survive a save, but keys Crenel does not know about do.

```yaml
folders:
  projects_root: "/home/you/projects"
  share_dir: "~/vm-share"

protection:
  protect_git: true
```

## Keys

- `folders.projects_root`: the folder whose subfolders Crenel lists. `~` is expanded, and a symlinked path is resolved to the real folder. Empty on first run, which opens the settings dialog.
- `folders.share_dir`: the folder the VM's virtiofs share points at (see the [User Guide](USER_GUIDE.md#one-time-vm-setup)). Defaults to `~/vm-share`, and is created if missing. It must not be inside `projects_root`, or contain it, and must hold nothing but empty folders and Crenel's own shares.
- `protection.protect_git`: `true` or `false` (a real YAML boolean, unquoted). Whether projects shared Write get their `.git` made read-only inside the VM. See [Git protection](USER_GUIDE.md#git-protection).

A key holding the wrong type is ignored and its default used instead. A file that cannot be parsed at all gives the defaults, and the settings dialog says so before you save over it.

## No commands

There are deliberately no command templates in this file. The commands that mount and unmount folders run as root, and this file can be written by anything running as you. Letting it decide what root runs would turn a compromised user account into a compromised machine. The operations are fixed in Crenel's helper, and **Show Commands** in the main window shows exactly what they will be.
