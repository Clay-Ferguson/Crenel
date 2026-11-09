"""Crenel: choose which project folders a VM can see, one folder at a time.

Nothing in this file imports Qt. `crenel.helper` runs as root and imports this
package, and it must keep running on a machine with no PyQt6 at all.
"""

APP_NAME = "Crenel"

# Applied to the whole application in __main__, as in DupFinder: a couple of
# points above the desktop default, so the folder list is easy to read.
UI_POINT_SIZE = 13
