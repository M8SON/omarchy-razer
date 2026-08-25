#!/bin/bash
# Prepare a machine for the Omarchy razer plugin.
#
# Covers the three things that silently break OpenRazer on a fresh Omarchy box:
# the driver isn't installed, the user isn't in the openrazer group (so the
# daemon sees no devices), and restore_persistence defaults to False (so the
# daemon saves your effect on exit and then ignores it on the next start).

set -euo pipefail

CONF="$HOME/.config/openrazer/razer.conf"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

# 1. Driver and Python bindings.
if ! pacman -Qq openrazer-daemon >/dev/null 2>&1; then
  say "Installing openrazer-daemon (AUR, builds a DKMS kernel module)"
  omarchy pkg aur add openrazer-daemon python-openrazer
else
  say "openrazer-daemon already installed"
fi

# 2. Group membership. The udev rules chown the sysfs nodes to the openrazer
#    group; without it the daemon starts fine and reports zero devices.
if id -nG "$USER" | tr ' ' '\n' | grep -qx openrazer; then
  say "Already in the openrazer group"
else
  say "Adding $USER to the openrazer group"
  sudo gpasswd -a "$USER" openrazer
  say "Log out and back in for the group to take effect"
fi

# 3. restore_persistence. The daemon writes persistence.conf on exit either way,
#    but only reads it back at startup when this is True -- which is why lights
#    come back dead after a reboot on a default install.
#
#    razer.conf sits at a predictable path under $HOME, so this refuses to edit
#    through a symlink or a non-regular file: `sed -i` and `>>` would follow one
#    and rewrite whatever it points at. The write itself goes to a private temp
#    file in the same directory and is renamed into place, so the config is
#    replaced atomically and never edited in place.
CONF_DIR="$(dirname "$CONF")"

if [[ -L $CONF_DIR ]]; then
  echo "refusing: $CONF_DIR is a symlink" >&2
  exit 1
fi
mkdir -p "$CONF_DIR"
if [[ ! -d $CONF_DIR ]]; then
  echo "refusing: $CONF_DIR is not a directory" >&2
  exit 1
fi
if [[ -L $CONF ]]; then
  echo "refusing: $CONF is a symlink" >&2
  exit 1
fi
if [[ -e $CONF && ! -f $CONF ]]; then
  echo "refusing: $CONF is not a regular file" >&2
  exit 1
fi

if [[ -f $CONF ]] && grep -q '^restore_persistence = True' "$CONF"; then
  say "restore_persistence already True"
else
  say "Setting restore_persistence = True"
  tmp="$(mktemp "$CONF_DIR/.razer.conf.XXXXXX")"
  trap 'rm -f "${tmp:-}"' EXIT
  if [[ -f $CONF ]]; then
    if grep -q '^restore_persistence' "$CONF"; then
      sed 's/^restore_persistence.*/restore_persistence = True/' "$CONF" >"$tmp"
    else
      { cat "$CONF"; printf '\n[Startup]\nrestore_persistence = True\n'; } >"$tmp"
    fi
  else
    printf '[Startup]\nrestore_persistence = True\n' >"$tmp"
  fi
  chmod 644 "$tmp"
  mv -f "$tmp" "$CONF"
  trap - EXIT
fi

# 4. Daemon.
say "Enabling openrazer-daemon for this user"
systemctl --user enable --now openrazer-daemon

say "Done. Add the widget with: omarchy plugin enable daedalus.razer right"
