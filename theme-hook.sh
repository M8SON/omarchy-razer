#!/bin/bash
# Omarchy theme-set hook: put the current theme's keyboard colour on Razer
# hardware whenever the theme changes.
#
# Off by default. Turn it on by linking it into the hook directory:
#
#   ln -sf ~/.config/omarchy/plugins/daedalus.razer/theme-hook.sh \
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
PLUGIN_DIR="$HOME/.config/omarchy/plugins/daedalus.razer"

color=""

# A theme may ship an explicit keyboard colour. Prefer it: it is the value the
# theme author chose for hardware, which is not always the accent.
if [[ -f $THEME_DIR/keyboard.rgb ]]; then
  color=$(tr -d '#[:space:]' <"$THEME_DIR/keyboard.rgb")
fi

# Otherwise fall back to the accent, which is what the panel's swatch row
# leads with, so the hook and the UI agree on what "the theme colour" means.
if [[ ! $color =~ ^[0-9A-Fa-f]{6}$ && -f $THEME_DIR/colors.toml ]]; then
  color=$(sed -n 's/^accent[[:space:]]*=[[:space:]]*"#\([0-9A-Fa-f]\{6\}\)".*/\1/p' \
    "$THEME_DIR/colors.toml" | head -1)
fi

[[ $color =~ ^[0-9A-Fa-f]{6}$ ]] || exit 0
[[ -f $PLUGIN_DIR/razerctl.py ]] || exit 0

python3 "$PLUGIN_DIR/razerctl.py" theme "$color" >/dev/null 2>&1 || true
