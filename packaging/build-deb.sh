#!/usr/bin/env bash
# Build a Debian package (.deb) of Crenel. DupFinder's script, plus the root
# helper and its polkit action.
#
# Usage:  packaging/build-deb.sh          (from anywhere)
# Output: dist/crenel_<version>_all.deb   (dist/ at the top of the checkout)
# Then:   sudo apt install --reinstall ./dist/crenel_<version>_all.deb
#
# Installed layout:
#   /usr/bin/crenel                                   launcher
#   /usr/lib/crenel/crenel/                           the app
#   /usr/lib/crenel/crenel-helper                     root helper, run through pkexec
#   /usr/lib/crenel/windowchrome/                     copy of ../windowchrome
#   /usr/lib/crenel/docs/                             USER_GUIDE.md, CONFIG.md (help.DOCS_DIR)
#   /usr/share/polkit-1/actions/com.github.clayferguson.crenel.policy
#   /usr/share/applications/crenel.desktop            menu entry
#   /usr/share/icons/hicolor/*/apps/crenel.png        themed icon, once the artwork exists
#
# Everything is installed root-owned (--root-owner-group). That matters more
# here than in DupFinder: the helper runs as root and imports crenel.shares
# from /usr/lib/crenel, so nothing under it may be writable by a user.
set -euo pipefail

umask 022

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname -- "$HERE")"
WINDOWCHROME="$ROOT/../windowchrome/windowchrome"
DIST="$ROOT/dist"
PACKAGE=crenel
ARCH=all
POLICY=com.github.clayferguson.crenel.policy

die() {
  echo "Error: $*" >&2
  exit 1
}

command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not found (it is part of dpkg, on every Debian-based system)."

VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -n 1)"
[ -n "$VERSION" ] || die "could not read the version from pyproject.toml."

[ -f "$WINDOWCHROME/__init__.py" ] || die "windowchrome not found at $WINDOWCHROME.
Clone it beside this checkout:  git clone https://github.com/Clay-Ferguson/windowchrome.git ../windowchrome"

if [ -z "${CRENEL_MAINTAINER:-}" ]; then
  name="$(git -C "$ROOT" config user.name || true)"
  email="$(git -C "$ROOT" config user.email || true)"
  [ -n "$name" ] && [ -n "$email" ] || die "set git user.name and user.email, or CRENEL_MAINTAINER=\"Name <email>\"."
  CRENEL_MAINTAINER="$name <$email>"
fi

STAGE="$DIST/${PACKAGE}_${VERSION}_${ARCH}"
DEB="$DIST/${PACKAGE}_${VERSION}_${ARCH}.deb"
LIB="$STAGE/usr/lib/$PACKAGE"
rm -rf "$STAGE" "$DEB"

# -- files -------------------------------------------------------------------

install -d -m 755 \
  "$LIB" \
  "$STAGE/usr/bin" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/polkit-1/actions" \
  "$STAGE/usr/share/doc/$PACKAGE" \
  "$STAGE/DEBIAN"

# Every file of a tree: files 644, directories 755. __pycache__ is left out
# and compiled fresh by postinst.
copy_tree() {
  local src="$1" dest="$2" file
  while IFS= read -r -d '' file; do
    install -D -m 644 "$src/$file" "$dest/$file"
  done < <(cd "$src" && find . -name __pycache__ -prune -o -type f -print0)
}

copy_tree "$ROOT/crenel" "$LIB/crenel"
copy_tree "$WINDOWCHROME" "$LIB/windowchrome"
copy_tree "$ROOT/docs" "$LIB/docs"

install -m 644 "$ROOT/LICENSE.md" "$STAGE/usr/share/doc/$PACKAGE/copyright"
install -m 644 "$HERE/$POLICY" "$STAGE/usr/share/polkit-1/actions/$POLICY"

shopt -s nullglob
icons=("$HERE"/icons/hicolor/*/apps/crenel.png)
shopt -u nullglob
if [ "${#icons[@]}" -eq 0 ]; then
  echo "Warning: no icons in packaging/icons/hicolor yet — building without one."
  echo "         Add packaging/icons/source.png and run packaging/icons/make-icons.py."
fi
for icon in "${icons[@]}"; do
  size_dir="$(basename "$(dirname "$(dirname "$icon")")")"
  install -d -m 755 "$STAGE/usr/share/icons/hicolor/$size_dir/apps"
  install -m 644 "$icon" "$STAGE/usr/share/icons/hicolor/$size_dir/apps/crenel.png"
done

# The launcher. -I (isolated mode) keeps PYTHONPATH and ~/.local out, so the
# app runs against the distribution's python3-pyqt6 and python3-yaml.
cat > "$STAGE/usr/bin/$PACKAGE" <<'EOF'
#!/bin/sh
# Crenel launcher, installed by the crenel package.
exec /usr/bin/python3 -I -c 'import sys; sys.path.insert(0, "/usr/lib/crenel"); from crenel.__main__ import main; sys.exit(main())' "$@"
EOF
chmod 755 "$STAGE/usr/bin/$PACKAGE"

# The root helper: the path the polkit action names, so pkexec shows Crenel's
# own prompt for it. -I matters even more here, since this runs as root:
# no PYTHONPATH, no user site-packages, no script directory on sys.path.
cat > "$LIB/crenel-helper" <<'EOF'
#!/usr/bin/python3 -I
"""Crenel's root helper, started by Crenel through pkexec. See crenel/helper.py."""
import sys

sys.path.insert(0, "/usr/lib/crenel")
from crenel.helper import main  # noqa: E402

sys.exit(main())
EOF
chmod 755 "$LIB/crenel-helper"

sed \
  -e '/^#/d' \
  -e "s|^Exec=.*|Exec=$PACKAGE|" \
  -e "s|^Icon=.*|Icon=$PACKAGE|" \
  "$HERE/crenel.desktop" > "$STAGE/usr/share/applications/crenel.desktop"
chmod 644 "$STAGE/usr/share/applications/crenel.desktop"

# -- smoke test ----------------------------------------------------------------

# Run with the launcher's interpreter and flags, plus -B so nothing is written
# into the stage. It checks that every package import resolves to a staged
# file, that every doc link resolves, and that the modules import: all of them
# where this python3 has PyQt6, otherwise the Qt-free ones. Those include the
# helper's whole import closure.
[ -x /usr/bin/python3 ] || die "/usr/bin/python3 not found; it is needed to check the staged files (apt install python3)."
/usr/bin/python3 -I -B - "$LIB" <<'PY' || die "the staged files failed the smoke test (above); nothing was packaged."
import ast
import importlib
import pathlib
import re
import sys

lib = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(lib))
problems = []


def is_module(path):
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


for package in ("crenel", "windowchrome"):
    for source in sorted((lib / package).rglob("*.py")):
        where = source.relative_to(lib)
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = source.parent
                for _ in range(node.level - 1):
                    base = base.parent
                if node.module:
                    if not is_module(base.joinpath(*node.module.split("."))):
                        problems.append(f"{where}: from {'.' * node.level}{node.module}")
                elif not (base / "__init__.py").is_file():
                    problems.append(f"{where}: from {'.' * node.level} (no package)")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                for name in names:
                    top = name.split(".")[0]
                    if top in ("crenel", "windowchrome") and not is_module(lib.joinpath(*name.split("."))):
                        problems.append(f"{where}: import {name}")

for doc in sorted((lib / "docs").rglob("*.md")):
    text = doc.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)\s]+)", text):
        if re.match(r"[a-z]+:|#", target):
            continue
        if not (doc.parent / target.split("#")[0]).exists():
            problems.append(f"{doc.relative_to(lib)}: link to {target}")
if not (lib / "docs" / "USER_GUIDE.md").is_file():
    problems.append("docs/USER_GUIDE.md is missing (help.DOCS_DIR opens it)")
if not (lib / "crenel-helper").is_file():
    problems.append("crenel-helper is missing (the polkit action names it)")

try:
    import PyQt6.QtWidgets  # noqa: F401
    modules = [p.stem for p in (lib / "crenel").glob("*.py") if p.stem != "__init__"]
    scope = "every module"
except ImportError:
    modules = "config shares helper protocol".split()
    scope = "the Qt-free modules (no PyQt6 for this python3)"
for name in sorted(modules):
    try:
        importlib.import_module(f"crenel.{name}")
    except Exception as exc:
        problems.append(f"import crenel.{name}: {exc!r}")

if problems:
    print("\n".join(problems), file=sys.stderr)
    sys.exit(1)
print(f"Smoke test passed: imports resolve, doc links resolve, {scope} import.")
PY

# -- package metadata --------------------------------------------------------

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  python3 -m compileall -q /usr/lib/crenel >/dev/null 2>&1 || true
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
EOF

cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
find /usr/lib/crenel -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
EOF

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
EOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"

INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"

# pkexec is what runs the helper as root; on older releases it lived in
# policykit-1.
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PACKAGE
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.11), python3-pyqt6, python3-yaml, pkexec | policykit-1
Recommends: qt6-wayland
Maintainer: $CRENEL_MAINTAINER
Installed-Size: $INSTALLED_SIZE
Description: choose which project folders a VM can see
 Crenel lists the folders in your projects folder and lets you make each one
 Hidden, Read or Write for a QEMU/KVM virtual machine. It bind-mounts the
 chosen projects into the one folder the VM's virtiofs share points at, read
 only where you say so, and can make a writable project's .git
 read-only. Changes are applied together after one password prompt.
EOF

# -- build -------------------------------------------------------------------

dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$DEB" >/dev/null
rm -rf "$STAGE"

echo "Built $DEB"
echo ""
echo "Install:  sudo apt install --reinstall $DEB"
echo "Remove:   sudo apt remove $PACKAGE"
