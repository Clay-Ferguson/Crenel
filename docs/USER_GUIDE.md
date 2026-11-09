# Crenel User Guide

Crenel decides which of your project folders a virtual machine can see. Each project is one of:

- **Hidden**: the VM cannot see the project at all, not even its name.
- **Read**: the VM can read the project but cannot change anything in it.
- **Write**: the VM can read and change the project.

It is built for running AI agents in a QEMU/KVM VM: you give the agent the projects you are working on today, and nothing else.

## Contents

- [How it works](#how-it-works)
- [One-time VM setup](#one-time-vm-setup)
- [Using Crenel](#using-crenel)
- [Git protection](#git-protection)
- [Folders in use](#folders-in-use)
- [How each share is built](#how-each-share-is-built)
- [The password prompt](#the-password-prompt)
- [Conflicts](#conflicts)
- [Settings](#settings)
- [What Crenel does not protect](#what-crenel-does-not-protect)

## How it works

The VM shares one host folder over virtiofs: the **share folder** (by default `~/vm-share`). That folder starts empty. When you share a project, Crenel *bind-mounts* it into the share folder:

```
~/projects/ABC   ── mount --bind ──▶   ~/vm-share/ABC   ── virtiofs ──▶   the VM
```

A bind mount makes the same folder appear in a second place. It is not a copy and not a link, so the agent's edits land directly in your real project. Read mode makes that second place read-only. The host's kernel enforces it, so nothing inside the VM, not even root, can write through it.

Hiding a project unmounts it and removes its empty folder from the share folder.

Bind mounts do not survive a reboot, so **every project is Hidden after the host restarts** until you share it again. That is the safe default.

Crenel keeps no record of its own: what it shows is read from the kernel's mount table every time.

## One-time VM setup

Point the VM's virtiofs share at the share folder instead of at your whole projects folder. With libvirt (virt-manager) and a VM named `ubuntu26.04`:

1. Shut the VM down.
2. Create the share folder (Crenel also creates it on its first run):
   `mkdir -p ~/vm-share`
3. Edit the VM:
   `virsh -c qemu:///system edit ubuntu26.04`
4. In the `<filesystem>` element, change the `<source dir=…>` from your projects folder to the share folder, and keep the `<target dir=…>` tag as it was. The guest's own `/etc/fstab` entry then keeps working unchanged:

```
<filesystem type='mount' accessmode='passthrough'>
  <driver type='virtiofs'/>
  <source dir='/home/you/vm-share'/>
  <target dir='projects'/>
</filesystem>
```

In virt-manager the same field is the shared filesystem's **Source path**.

5. Start the VM. Its shared folder is now empty until you share something from Crenel.

Projects you share while the VM is running should appear inside it straight away, because virtiofsd follows new mounts made under the folder it shares. If one does not show up, restart the VM once with the project already shared, and tell the developer: it would mean your virtiofsd setup does not pass new mounts through.

## Using Crenel

The window lists every folder directly inside your projects folder (it never looks deeper, and skips symlinks and dot-folders). Each row has three radio buttons: **Hidden**, **Read** and **Write**. When Crenel starts, and whenever you press **Refresh** (F5), they show what the VM can see right now.

Clicking a radio button only *marks* a change. The project's name turns **amber and bold**, and nothing happens yet. Mark as many projects as you like, then press **Apply**. Every marked change is made in one go, after one password prompt.

The buttons under the list:

- **Refresh**: re-read what is shared. If you have marked changes, Crenel asks before throwing them away.
- **Show Commands**: shows what Apply will do, written as the equivalent `mount` and `umount` commands, folder by folder.
- **Hide All**: marks every project Hidden. Press Apply to carry it out.
- **Apply**: make the marked changes.

When Apply finishes, a summary says how it went: *All 3 changes were applied.* followed by one line per project with what the VM can see of it now (✓ ABC: now writable, git protected). The summary is written from the mount table read back afterwards, not from what was asked for. If anything failed, the summary is a warning instead: it opens with something like *2 of 3 changes applied.* and has a ✗ line saying why for each failure.

The status bar counts the projects, how many are shared (writable and read-only), and how many changes are still waiting for Apply. After an Apply it also repeats the summary's first line, until the next Apply or Refresh. Hover over a project's name to see its current state and what Apply will change it to.

Changing an already-shared project, say from Write to Read, takes the share down and puts it back up the new way. A read-only setting changed in place would only reach the host's side and never the VM's (see [How each share is built](#how-each-share-is-built)). So the project is briefly unmounted, which needs it not to be in use (see [Folders in use](#folders-in-use)).

## Git protection

A project shared Write is writable from inside the VM, and that includes its `.git` folder. An agent (or anything that compromised it) could plant a git hook, a script that runs **on the host** the next time you commit there. It could also set `core.hooksPath` in `.git/config` to point git at hooks somewhere else.

With git protection on (the default), Crenel makes `.git/hooks` and `.git/config` read-only inside the VM for projects shared Write. Commits, branches, pushes and pulls still work there. What stops working in the VM is anything that writes `.git/config`: `git config`, `git remote add`, and creating a branch with upstream tracking. Do those on the host.

Protection applies only to a real `.git` folder. A worktree or a submodule, whose `.git` is a file, has nothing to protect here.

The protection covers only the VM's view. Your own `.git` on the host stays fully writable.

Turning the setting on or off marks every project currently shared Write as changed. Press Apply to update them.

## Folders in use

A folder cannot be unmounted while something has a file open inside it, for example an agent with a shell sitting in that project. Crenel then says the folder is in use, keeps your change marked, and asks whether to **detach** it anyway.

Detaching removes the project from the VM's view at once. Files that are *already open* in the VM stay usable until they are closed, but nothing new can be opened. To cut access off completely, stop the agent (or shut the VM down) first, then hide the project.

This applies to every change to a shared project, not only to hiding it, because every change re-shares it.

## How each share is built

Linux keeps a separate copy of the share folder for the VM, and the kernel copies each new mount into it at the moment the mount arrives. Two things follow from that:

- A share made read-only *after* it arrives would stay writable in the VM's copy.
- A plain bind mount stays *linked* to the project folder it came from. Anything later mounted inside the share, such as the git protection, would be copied back onto your real project, making its `.git/hooks` and `.git/config` read-only on the host.

So Crenel builds every share off to the side first, in a private, empty scratch spot (`/run/crenel-stage`, which only root can enter and which exists only during an Apply):

1. It mounts the project there and cuts the link back to the original folder.
2. It makes the share read-only, or adds the git protection.
3. It moves the finished share into the share folder in one step.

The VM's copy arrives complete, and nothing is ever mounted on your real project. Nothing is copied either: the scratch spot holds a doorway to the project, not its files.

## The password prompt

Mounting needs root. Crenel itself never runs as root. Apply hands the list of changes to a small helper program through `pkexec`, which shows your desktop's normal password prompt.

- When Crenel is installed from its `.deb`, the prompt names Crenel, and your password is remembered for a few minutes.
- When Crenel runs from its source folder (`./start.sh`), the prompt names `/usr/bin/python3` and asks every time.

The helper accepts only folder names directly inside your projects folder. It refuses symlinks and anything not owned by you, and runs no commands and no shell: it makes the mount system calls itself. Commands you could type into a settings field would be a way to run anything as root, so there are none. **Show Commands** shows what the helper will do instead.

If you cancel the prompt, nothing changes and your marked changes stay marked.

## Conflicts

A row marked **⚠** in red has something in its place in the share folder that Crenel did not put there, for example a different folder mounted under the project's name, or several mounts stacked up. None of its radio buttons is selected. Choose any mode and press Apply, and Crenel replaces whatever is there. A project renamed or deleted on the host while it was shared also shows up this way, so it can be hidden.

## Settings

Options ▸ Settings:

- **Projects folder**: the folder whose subfolders are listed.
- **VM share folder**: the folder the VM's virtiofs share points at. Crenel creates it if it is missing. It must not be inside the projects folder, or contain it.
- **Git protection**: see [Git protection](#git-protection).

The settings are kept in a YAML file. See [CONFIG.md](CONFIG.md).

## What Crenel does not protect

- **Projects you share Write.** The agent can change anything in them, apart from the git hooks and config. Be careful with anything else in a writable project that runs on the host: build scripts, `package.json` scripts, a `Makefile`, `.envrc` (direnv), `.vscode/tasks.json`. Review changes before running them on the host, or run them inside the VM.
- **Open files after a detach.** See [Folders in use](#folders-in-use).
- **Whatever else the VM shares.** Crenel manages only the share folder. Any other `<filesystem>` in the VM's configuration still shares what it shares.
