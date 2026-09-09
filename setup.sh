#!/bin/bash
# Prepare a machine for the Omarchy razer plugin.
#
# Covers the three things that silently break OpenRazer on a fresh Omarchy box
# once the packages are on: the user isn't in the openrazer group (so the
# daemon sees no devices), restore_persistence defaults to False (so the daemon
# saves your effect on exit and then ignores it on the next start), and the
# user unit isn't enabled. OpenRazer itself is a system package that pacman
# owns; this script checks for it and stops if it is missing, but never
# installs it.

set -euo pipefail

# Every tool below runs from the system directory, never from whatever the
# inherited PATH puts first: a user-writable directory ahead of /usr/bin must
# not be able to stand in for sudo, gpasswd or systemctl.
PATH=/usr/bin
export PATH

HERE="$(dirname "${BASH_SOURCE[0]}")"
CONF_DIR="$HOME/.config/openrazer"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

# 1. Driver and Python bindings. Not installed from here: whatever this script
#    fetched would be replaced by the next `pacman -Syu` anyway, and the DKMS
#    module has to be built against the kernel that is actually running, so
#    OpenRazer is a prerequisite pacman manages like any other system package.
#    Stop here if it is missing, naming the one command that installs it.
if ! /usr/bin/pacman -Qq openrazer-daemon python-openrazer >/dev/null 2>&1; then
  say "OpenRazer is not installed. Install it first, then run this script again:"
  say "    omarchy pkg add openrazer-daemon python-openrazer"
  exit 1
fi
say "openrazer-daemon and python-openrazer are installed"

# 2. Group membership. The udev rules chown the sysfs nodes to the openrazer
#    group; without it the daemon starts fine and reports zero devices.
#    The user comes from the kernel via id(1), not from $USER, which is just
#    an inherited environment variable.
me="$(/usr/bin/id -un)"
if /usr/bin/id -nG | /usr/bin/tr ' ' '\n' | /usr/bin/grep -qx openrazer; then
  say "Already in the openrazer group"
else
  say "Adding $me to the openrazer group"
  /usr/bin/sudo /usr/bin/gpasswd -a "$me" openrazer
  say "Log out and back in for the group to take effect"
fi

# 3. restore_persistence. The daemon writes persistence.conf on exit either way,
#    but only reads it back at startup when this is True -- which is why lights
#    come back dead after a reboot on a default install.
#
#    razer.conf sits at a predictable path under $HOME. A shell check-then-edit
#    (test -L, then sed/mv by name) leaves a window in which the file can be
#    swapped for a symlink; persistence.py holds the directory open and does
#    every open, read, create and rename through that descriptor instead.
say "Setting restore_persistence = True"
/usr/bin/python3 "$HERE/persistence.py" "$CONF_DIR"

# 4. Daemon.
say "Enabling openrazer-daemon for this user"
/usr/bin/systemctl --user enable --now openrazer-daemon

say "Done. Add the widget with: omarchy plugin enable io.github.m8son.razer right"
