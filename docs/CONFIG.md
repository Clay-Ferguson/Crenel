# Crenel configuration file

Crenel keeps its settings in `~/.config/crenel/crenel-config.yaml`. It is created on the first run, and rewritten whenever you press **Save** in Options ▸ Settings. Comments and blank lines you add yourself do not survive a save, but keys Crenel does not know about do.

```yaml
folders:
  projects_root: "/home/you/projects"
  share_dir: "~/vm-share"

protection:
  protected:
    - ".git"
```

## Keys

- `folders.projects_root`: the folder whose subfolders Crenel lists. `~` is expanded, and a symlinked path is resolved to the real folder. Empty on first run, which opens the settings dialog.
- `folders.share_dir`: the folder the VM's virtiofs share points at (see the [User Guide](USER_GUIDE.md#one-time-vm-setup)). Defaults to `~/vm-share`, and is created if missing. It must not be inside `projects_root`, or contain it, and must hold nothing but empty folders and Crenel's own shares.
- `protection.protected`: a list of folder or file names directly inside each project, made read-only inside the VM for projects shared Write. Defaults to `[".git"]`; `[]` protects nothing. An item starting with `#` is a comment, kept but ignored (the settings dialog shows it as a commented line). A name with a `/` in it (a path), `.` or `..` is dropped. See [Protected names](USER_GUIDE.md#protected-names). Older versions had `protection.protect_git: true` or `false` instead: it is read as `[".git"]` or `[]`, and replaced by `protected` on the next save.

A key holding the wrong type is ignored and its default used instead. A file that cannot be parsed at all gives the defaults, and the settings dialog says so before you save over it.

## No commands

There are deliberately no command templates in this file. The commands that mount and unmount folders run as root, and this file can be written by anything running as you. Letting it decide what root runs would turn a compromised user account into a compromised machine. The operations are fixed in Crenel's helper, and **Show Commands** in the main window shows exactly what they will be.
