#!/usr/bin/env bash
# install.sh — one-line installer for agentclick (macOS + Ubuntu)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/iamhsouna/agentclick/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/iamhsouna/agentclick/main/install.sh | bash -s -- --help
# Env:
#   DEST=/custom/bin        # override install dir (default: /usr/local/bin or ~/.local/bin if no sudo)
#   REPO=iamhsouna/agentclick
#   BRANCH=main
set -euo pipefail

REPO="${REPO:-iamhsouna/agentclick}"
BRANCH="${BRANCH:-main}"
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

# args
DEST="${DEST:-}"
NO_SOUND=0
for arg in "$@"; do
  case "$arg" in
    --help|-h) echo "Usage: install.sh [--dest DIR] [--help]"; echo "  curl -fsSL $RAW/install.sh | bash"; echo "  DEST=~/.local/bin bash install.sh"; exit 0 ;;
    --dest) shift; DEST="${1:-}"; shift || true ;;
    --dest=*) DEST="${arg#--dest=}";;
  esac
done

# pick dest: $DEST > /usr/local/bin if writable/sudo else ~/.local/bin
if [ -z "$DEST" ]; then
  if [ -w /usr/local/bin ] || command -v sudo >/dev/null 2>&1; then
    DEST="/usr/local/bin"
  else
    DEST="$HOME/.local/bin"
  fi
fi

SHARE_FALLBACK="/usr/local/share/agentclick/sounds"
USER_SHARE="$HOME/.local/share/agentclick/sounds"

echo "→ agentclick installer (macOS + Ubuntu)"
echo "  repo : $REPO ($BRANCH)"
echo "  dest : $DEST"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "✗ missing: $1" >&2; return 1; }; }

# check deps
if ! need_cmd curl && ! need_cmd wget; then
  echo "✗ need curl or wget" >&2; exit 1
fi
if ! need_cmd python3; then
  echo "✗ need python3 (3.9+) — https://www.python.org/downloads/" >&2; exit 1
fi

# choose fetch helper
fetch() {
  local url="$1" out="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$out"
  else
    wget -qO "$out" "$url"
  fi
}

# check audio player (warn only)
check_audio() {
  if [ "$(uname -s)" = "Darwin" ]; then
    if command -v afplay >/dev/null 2>&1; then
      echo "  audio: afplay ✓"
    else
      echo "  audio: afplay missing (unexpected on macOS)" >&2
    fi
  else
    if command -v paplay >/dev/null 2>&1; then echo "  audio: paplay ✓"
    elif command -v pw-play >/dev/null 2>&1; then echo "  audio: pw-play ✓"
    elif command -v aplay >/dev/null 2>&1; then echo "  audio: aplay ✓"
    elif command -v ffplay >/dev/null 2>&1; then echo "  audio: ffplay ✓"
    elif command -v play >/dev/null 2>&1; then echo "  audio: sox (play) ✓"
    else
      echo "  audio: no player found — clicks will be silent until you install one:" >&2
      echo "         sudo apt update && sudo apt install -y pulseaudio-utils  # paplay" >&2
      echo "     or: sudo apt install -y alsa-utils  # aplay" >&2
    fi
  fi
}
check_audio || true

TMPDIR_="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_"' EXIT

echo "  fetch: $RAW/agentclick.py"
fetch "$RAW/agentclick.py" "$TMPDIR_/agentclick.py"
echo "  fetch: $RAW/sounds/key.wav"
fetch "$RAW/sounds/key.wav" "$TMPDIR_/key.wav"
echo "  fetch: $RAW/sounds/enter.wav"
fetch "$RAW/sounds/enter.wav" "$TMPDIR_/enter.wav"

# validate we got real files (not 404 html)
for f in "$TMPDIR_/agentclick.py" "$TMPDIR_/key.wav" "$TMPDIR_/enter.wav"; do
  if [ ! -s "$f" ]; then echo "✗ fetch failed: $f is empty" >&2; exit 1; fi
  if head -1 "$f" | grep -q "<html" 2>/dev/null; then echo "✗ fetch got HTML (check REPO/BRANCH): $f" >&2; head -5 "$f" >&2; exit 1; fi
done

# ensure dest exists
if [ ! -d "$DEST" ]; then
  echo "→ mkdir -p $DEST"
  if [ -w "$(dirname "$DEST")" ]; then mkdir -p "$DEST"
  else sudo mkdir -p "$DEST"
  fi
fi

# install binary
echo "→ install $DEST/agentclick"
if [ -w "$DEST" ]; then
  cp "$TMPDIR_/agentclick.py" "$DEST/agentclick"
  chmod +x "$DEST/agentclick"
else
  sudo cp "$TMPDIR_/agentclick.py" "$DEST/agentclick"
  sudo chmod +x "$DEST/agentclick"
fi

# install sounds — try 3 locations in order, keep at least one next to binary for max compat
install_sounds_to() {
  local dir="$1" use_sudo="$2"
  if [ "$use_sudo" = "sudo" ]; then
    sudo mkdir -p "$dir"
    sudo cp "$TMPDIR_/key.wav" "$dir/key.wav"
    sudo cp "$TMPDIR_/enter.wav" "$dir/enter.wav"
  else
    mkdir -p "$dir"
    cp "$TMPDIR_/key.wav" "$dir/key.wav"
    cp "$TMPDIR_/enter.wav" "$dir/enter.wav"
  fi
  echo "  sounds: $dir ✓"
}

# 1) always put sounds next to binary (works even if share path misses)
if [ -w "$DEST" ]; then
  mkdir -p "$DEST/sounds"
  cp "$TMPDIR_/key.wav" "$DEST/sounds/key.wav"
  cp "$TMPDIR_/enter.wav" "$DEST/sounds/enter.wav"
  echo "  sounds: $DEST/sounds ✓"
else
  sudo mkdir -p "$DEST/sounds"
  sudo cp "$TMPDIR_/key.wav" "$DEST/sounds/key.wav"
  sudo cp "$TMPDIR_/enter.wav" "$DEST/sounds/enter.wav"
  echo "  sounds: $DEST/sounds ✓"
fi

# 2) also install to standard share (so /usr/local/bin/agentclick finds it via fallback)
if [ "$DEST" = "/usr/local/bin" ]; then
  if [ -w "/usr/local/share" ] || command -v sudo >/dev/null 2>&1; then
    if [ -w "/usr/local/share" ]; then install_sounds_to "$SHARE_FALLBACK" ""
    else install_sounds_to "$SHARE_FALLBACK" "sudo"
    fi
  fi
else
  # user dest → also put in ~/.local/share for the fallback search path
  install_sounds_to "$USER_SHARE" "" || true
fi

# verify
echo "→ verify"
if ! "$DEST/agentclick" --dry-run >/dev/null 2>&1; then
  # try without dest prefix if dest is on PATH
  if command -v agentclick >/dev/null 2>&1; then
    agentclick --dry-run 2>&1 | sed 's/^/  /' || true
  else
    echo "  warn: verification failed, but binary is installed at $DEST/agentclick" >&2
    "$DEST/agentclick" --dry-run 2>&1 | sed 's/^/  /' || true
  fi
else
  "$DEST/agentclick" --dry-run 2>&1 | sed 's/^/  /' || true
fi

# PATH hint
if ! echo ":$PATH:" | grep -q ":$DEST:"; then
  echo ""
  echo "⚠  $DEST not on PATH. Add it:"
  SHELL_RC="$HOME/.zshrc"
  [ -n "${BASH_VERSION:-}" ] && SHELL_RC="$HOME/.bashrc"
  [ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"
  echo "  export PATH=\"\$PATH:$DEST\"  # add to $SHELL_RC"
fi

echo ""
echo "✓ installed to $DEST/agentclick"
echo "  try: agentclick --test        # should play two clicks"
echo "  try: agentclick --dry-run"
echo "  try: agentclick echo hello"
if [ "$DEST" != "/usr/local/bin" ] && [ "$DEST" != "$HOME/.local/bin" ]; then
  echo "  (custom DEST — run with $DEST/agentclick)"
fi
