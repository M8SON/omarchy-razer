#!/bin/bash
# Omarchy theme-set hook: put the current theme's keyboard colour on Razer
# hardware whenever the theme changes.
#
# Off by default. Turn it on by linking it into the hook directory:
#
#   ln -sf ~/.config/omarchy/plugins/io.github.m8son.razer/theme-hook.sh \
#          ~/.config/omarchy/hooks/theme-set.d/razer
#
# and off again by deleting that link. omarchy-hook runs each file in
# theme-set.d through bash, so the link needs no executable bit.
#
# This follows the convention Omarchy's own keyboard handlers use (see
# omarchy-theme-set-keyboard-asus-rog): read the theme's colour, validate it,
# apply it, and exit quietly whenever there is nothing to do -- a hook that
# runs on every theme change must never be noisy or fail the switch.

THEME_DIR="$HOME/.local/state/omarchy/current/theme"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/io.github.m8son.razer"

color=""

# Theme files sit at replaceable paths, and this hook runs inside every theme
# switch, so a read that follows a symlink, streams a huge file, or blocks on
# a FIFO would hang or slow the switch itself. Refuse symlinks and anything
# not a regular file, and never read more than 64 KiB. (Bash cannot fstat an
# already-open descriptor, so a check-then-read race window remains; head -c
# still bounds the bytes either way, and the panel's own read is fully
# descriptor-bound in razerctl.py.)
read_small() {
  [[ ! -L $1 && -f $1 ]] || return 1
  head -c 65536 -- "$1" 2>/dev/null
}

# A theme may ship an explicit keyboard colour. Prefer it: it is the value the
# theme author chose for hardware, which is not always the accent.
color=$(read_small "$THEME_DIR/keyboard.rgb" | tr -d '#[:space:]')

# Otherwise fall back to the accent, which is what the panel's swatch row
# leads with, so the hook and the UI agree on what "the theme colour" means.
if [[ ! $color =~ ^[0-9A-Fa-f]{6}$ ]]; then
  color=$(read_small "$THEME_DIR/colors.toml" \
    | sed -n 's/^accent[[:space:]]*=[[:space:]]*"#\([0-9A-Fa-f]\{6\}\)".*/\1/p' \
    | head -1)
fi

[[ $color =~ ^[0-9A-Fa-f]{6}$ ]] || exit 0
[[ -f $PLUGIN_DIR/razerctl.py ]] || exit 0

python3 "$PLUGIN_DIR/razerctl.py" theme "$color" >/dev/null 2>&1 || true
