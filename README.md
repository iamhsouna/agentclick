# agentclick

Wrap any command and play **movie-style typing clicks** as it streams output — instead of on your own keystrokes.

Shipped with the same `key.wav` / `enter.wav` from [mattogodoy/hacker-sounds](https://github.com/mattogodoy/hacker-sounds) (GPL-3.0) that powers the VS Code "hacker sounds" extension. Works on **macOS** (`afplay`) and **Ubuntu/Linux** (`paplay` / `pw-play` / `aplay` / `ffplay` / `sox`).

```
agentclick claude          # clicks while Claude Code streams
agentclick pi              # clicks while pi streams
agentclick -- npm run dev  # anything else
```

## Demo

`agentclick` watches the wrapped command's stdout/stderr. Every chunk of printable output triggers a soft `key.wav` tick (rate-limited), every newline triggers `enter.wav`. When there's no audio device or player, it exits silently — the wrapped command always runs.

## Install

### macOS

No dependencies — `afplay` is built in.

```bash
chmod +x agentclick.py
sudo mv agentclick.py /usr/local/bin/agentclick
sudo mv sounds /usr/local/bin/sounds   # or /usr/local/share/agentclick/sounds

# optional: alias your agents so `claude` is always audible
echo "alias claude='agentclick claude'" >> ~/.zshrc
echo "alias pi='agentclick pi'" >> ~/.zshrc
```

### Ubuntu / Debian (22.04+, also Raspberry Pi OS)

Needs one audio player. Most desktops already have one; pick the first that works:

```bash
# check what you have
which paplay pw-play aplay ffplay play

# if none, install PulseAudio utils (recommended)
sudo apt update && sudo apt install -y pulseaudio-utils
# alternatives:
# sudo apt install -y pipewire-pulse   # provides pw-play
# sudo apt install -y alsa-utils       # provides aplay
# sudo apt install -y ffmpeg           # provides ffplay
# sudo apt install -y sox              # provides play
```

Then install `agentclick`:

```bash
chmod +x agentclick.py
sudo mv agentclick.py /usr/local/bin/agentclick
sudo mkdir -p /usr/local/share/agentclick
sudo mv sounds /usr/local/share/agentclick/sounds

echo "alias claude='agentclick claude'" >> ~/.bashrc
```

**Sounds search path** (first match wins): `CLICK_SOUND` env → next to the binary (`sounds/key.wav`) → `/usr/local/share/agentclick/sounds` → `/usr/share/agentclick/sounds` → `~/.local/share/agentclick/sounds` → `/usr/local/bin/sounds`.

## Usage

```bash
agentclick <command> [args...]
agentclick -- <command> [args...]   # use -- if command starts with -
agentclick --help
agentclick --version
agentclick --test                   # play one key + one enter to verify audio
agentclick --dry-run                # show resolved sounds/player, don't run
agentclick --synth <command>        # force synthesized beeps (ignore wav files)
agentclick --synth --test
```

Exit code of the wrapped command is propagated (including `127` for not found, `128+signal` for signals).

**Non-TTY fallback:** When `stdin`/`stdout` isn't a TTY (piped, CI, `AGENTCLICK_NO_PTY=1`), it falls back to a pipe pump — same clicks, slightly less faithful terminal emulation but no extra setup.

## Tuning

All via env vars:

| var | default | notes |
|-----|---------|-------|
| `CLICK_VOL` | `0.5` | 0.0–1.0. Mapped to `-v` (afplay) / `--volume` (paplay) / synth amplitude |
| `CLICK_INTERVAL` | `0.045` | min seconds between key ticks (~22/s max). Lower → faster clicking |
| `CLICK_SOUND` | bundled `key.wav` | override wav: `CLICK_SOUND=/path/to/click.wav agentclick claude` |
| `ENTER_SOUND` | bundled `enter.wav` | override enter wav |
| `CLICK_SYNTH` | `0` | `CLICK_SYNTH=1` ignores wavs, uses synthesized clicks |
| `CLICK_FREQ_LOW` / `CLICK_FREQ_HIGH` | `1400` / `2200` | pitch range for synth mode only |
| `AGENTCLICK_DEBUG` | `0` | `AGENTCLICK_DEBUG=1` verbose to stderr (resolved paths, player, warnings) |
| `AGENTCLICK_NO_PTY` | `0` | `1` forces pipe mode |

Examples:

```bash
CLICK_VOL=0.2 CLICK_INTERVAL=0.02 agentclick claude
CLICK_SOUND=~/sounds/custom.wav agentclick pi
CLICK_SYNTH=1 CLICK_FREQ_LOW=800 CLICK_FREQ_HIGH=1200 agentclick -- npm run dev
AGENTCLICK_DEBUG=1 agentclick --dry-run -- claude --help
```

## How it works

1. Detects the best audio player at startup: `afplay` on macOS, else `paplay` > `pw-play` > `aplay` > `ffplay` > `play`.
2. Resolves `key.wav`/`enter.wav` (or builds a 5-file synth pool in `TMPDIR` with PID-unique names, cleaned up on exit).
3. Spawns the command via `pty.spawn` (with `SIGWINCH` forwarding), watches each 8 KiB chunk — `enter.wav` on `\n`, rate-limited key tick on printable ASCII.
4. Falls back to `subprocess.Popen` + threaded pump when no TTY.

No dependencies beyond the Python stdlib (tested on Python 3.9+). Audio players are invoked fire-and-forget via `Popen(..., start_new_session=True)` so they never block the stream.

## Troubleshooting

```bash
# no sound?
agentclick --dry-run          # shows player + paths
agentclick --test             # should play two sounds
AGENTCLICK_DEBUG=1 agentclick --test  # verbose

# Ubuntu: no player found
sudo apt install pulseaudio-utils && agentclick --dry-run  # should show paplay

# custom sound not playing?
ls -l "$CLICK_SOUND" && file "$CLICK_SOUND"  # must be a valid WAV

# completely silent fallback (CI without audio)
agentclick --dry-run  # "player=(none)" → command still runs, just no clicks
```

## Uninstall

```bash
sudo rm /usr/local/bin/agentclick
sudo rm -rf /usr/local/share/agentclick /usr/local/bin/sounds
# remove aliases from ~/.zshrc / ~/.bashrc
```

## Credits & License

- `sounds/key.wav` and `sounds/enter.wav` from [mattogodoy/hacker-sounds](https://github.com/mattogodoy/hacker-sounds), licensed **GPL-3.0**.
- `agentclick.py` itself is **MIT** (or GPL-3.0 if you distribute the sounds together — your choice). See `LICENSE` if present.
