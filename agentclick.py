#!/usr/bin/env python3
"""
agentclick.py — wraps any command and plays typewriter clicks as it streams output.

Cross-platform: macOS (afplay) and Ubuntu / Linux (paplay / pw-play / aplay / ffplay).

Bundled sounds (GPL-3.0, from mattogodoy/hacker-sounds):
  sounds/key.wav / sounds/enter.wav — keep the `sounds/` folder next to this script.

Usage:
  agentclick <command> [args...]
  agentclick -- <command> [args...]
  agentclick --help
  agentclick --test              # play one click + one enter to verify audio
  agentclick --version

Env tuning:
  CLICK_VOL=0.5          volume 0.0-1.0 (default 0.5). Applied to afplay/paplay and synth amplitude.
  CLICK_INTERVAL=0.045   min seconds between key clicks (default 0.045 ≈ 22/sec max)
  CLICK_SOUND=/path.wav  override bundled key.wav
  ENTER_SOUND=/path.wav  override bundled enter.wav
  CLICK_SYNTH=1          force synthesized clicks (ignore wav files)
  CLICK_FREQ_LOW=1400 CLICK_FREQ_HIGH=2200  synth pitch range only
  AGENTCLICK_DEBUG=1     verbose logging to stderr

Setup — macOS:
  chmod +x agentclick.py && mv agentclick.py /usr/local/bin/agentclick
  mv sounds /usr/local/bin/sounds   # or /usr/local/share/agentclick/sounds
  # ~/.zshrc:
  #   alias claude='agentclick claude'
  #   alias pi='agentclick pi'

Setup — Ubuntu / Debian:
  chmod +x agentclick.py && sudo mv agentclick.py /usr/local/bin/agentclick
  sudo mkdir -p /usr/local/share/agentclick && sudo mv sounds /usr/local/share/agentclick/sounds
  # Ensure an audio player is installed (one of these is usually present):
  #   sudo apt update && sudo apt install -y pulseaudio-utils   # provides paplay
  #   # or: sudo apt install -y alsa-utils   # provides aplay
  #   # or: sudo apt install -y pipewire-pulse  # provides pw-play on 22.04+
  # ~/.bashrc / ~/.zshrc:
  #   alias claude='agentclick claude'

Credit: key.wav / enter.wav from mattogodoy/hacker-sounds (GPL-3.0).
"""
from __future__ import annotations

import argparse
import atexit
import math
import os
import pty
import random
import select
import shutil
import signal
import struct
import sys
import tempfile
import threading
import time
import wave
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

__version__ = "1.1.0"

# ── env helpers ──────────────────────────────────────────────────────────────

def _env_float(name: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        _debug(f"WARN: {name}={raw!r} not a number, using {default}")
        return default
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def _debug(msg: str) -> None:
    if os.environ.get("AGENTCLICK_DEBUG") == "1":
        print(f"[agentclick] {msg}", file=sys.stderr)


VOL: float = _env_float("CLICK_VOL", 0.5, 0.0, 1.0)
MIN_INTERVAL: float = _env_float("CLICK_INTERVAL", 0.045, 0.0, 1.0)
FREQ_LOW: float = _env_float("CLICK_FREQ_LOW", 1400, 20, 20000)
FREQ_HIGH: float = _env_float("CLICK_FREQ_HIGH", 2200, 20, 20000)
if FREQ_HIGH < FREQ_LOW:
    FREQ_LOW, FREQ_HIGH = FREQ_HIGH, FREQ_LOW

SYNTH_MODE: bool = os.environ.get("CLICK_SYNTH") == "1"
CUSTOM_KEY: Optional[str] = os.environ.get("CLICK_SOUND") or None
CUSTOM_ENTER: Optional[str] = os.environ.get("ENTER_SOUND") or None
# treat empty string as unset
if CUSTOM_KEY is not None and CUSTOM_KEY.strip() == "":
    CUSTOM_KEY = None
if CUSTOM_ENTER is not None and CUSTOM_ENTER.strip() == "":
    CUSTOM_ENTER = None

# ── sound resolution ─────────────────────────────────────────────────────────

def _resolve_bundled(sound_name: str) -> Optional[str]:
    """Search several plausible install locations for the bundled wav."""
    # Resolve symlinks (e.g. /usr/local/bin/agentclick -> real location)
    try:
        real_file = Path(__file__).resolve()
    except Exception:
        real_file = Path(__file__).absolute()
    script_dir = real_file.parent
    # also try the literal __file__ dir (covers some packaging edge cases)
    literal_dir = Path(os.path.abspath(__file__)).parent

    candidates: List[Path] = []
    for d in [script_dir, literal_dir]:
        candidates.append(d / "sounds" / sound_name)
        candidates.append(d.parent / "sounds" / sound_name)
    # standard share locations (Ubuntu friendly)
    candidates.extend([
        Path("/usr/local/share/agentclick/sounds") / sound_name,
        Path("/usr/share/agentclick/sounds") / sound_name,
        Path.home() / ".local/share/agentclick/sounds" / sound_name,
        Path("/usr/local/bin/sounds") / sound_name,
    ])
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def _resolve_sound(which: str) -> Optional[str]:
    """
    Resolve key / enter sound path.
    Priority: CLICK_SOUND/ENTER_SOUND env > bundled file > None
    """
    if SYNTH_MODE:
        return None
    if which == "key" and CUSTOM_KEY:
        if os.path.isfile(CUSTOM_KEY):
            return CUSTOM_KEY
        _debug(f"WARN: CLICK_SOUND={CUSTOM_KEY!r} not found, falling back to bundled")
    if which == "enter" and CUSTOM_ENTER:
        if os.path.isfile(CUSTOM_ENTER):
            return CUSTOM_ENTER
        _debug(f"WARN: ENTER_SOUND={CUSTOM_ENTER!r} not found, falling back to bundled")

    bundled = _resolve_bundled(f"{which}.wav")
    if bundled:
        return bundled
    return None


KEY_PATH: Optional[str] = _resolve_sound("key")
ENTER_PATH: Optional[str] = _resolve_sound("enter")

_debug(f"KEY_PATH={KEY_PATH} ENTER_PATH={ENTER_PATH} SYNTH_MODE={SYNTH_MODE} VOL={VOL}")

# ── synth fallback ───────────────────────────────────────────────────────────

_TMP_SYNTH_FILES: List[str] = []


def _make_tick(path: str, freq: float, duration: float = 0.012, rate: int = 44100) -> None:
    n = int(rate * duration)
    frames = bytearray()
    for i in range(n):
        env = (1 - i / n) ** 2
        val = int(VOL * env * 32767 * math.sin(2 * math.pi * freq * i / rate))
        # clamp to int16
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
        frames += struct.pack("<h", val)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def _cleanup_synth() -> None:
    for p in _TMP_SYNTH_FILES:
        try:
            os.unlink(p)
        except OSError:
            pass


atexit.register(_cleanup_synth)

if KEY_PATH is None:
    # No wav available → synthesize a small pool with varied pitch
    POOL_SIZE = 5
    _pool_paths: List[str] = []
    tmpdir = tempfile.gettempdir()
    # use PID in name to avoid collisions across concurrent runs
    pid = os.getpid()
    for i in range(POOL_SIZE):
        p = os.path.join(tmpdir, f"agentclick_tick_{pid}_{i}.wav")
        # avoid regenerating if somehow left over and valid
        freq = FREQ_LOW + (FREQ_HIGH - FREQ_LOW) * i / max(1, POOL_SIZE - 1)
        try:
            _make_tick(p, freq)
        except OSError as e:
            _debug(f"synth write failed {p}: {e}")
            continue
        _TMP_SYNTH_FILES.append(p)
        _pool_paths.append(p)
    if not _pool_paths:
        _debug("WARN: synth pool empty — clicks will be silent")
else:
    _pool_paths = [KEY_PATH]

# ── audio player detection ───────────────────────────────────────────────────

PlayerSpec = Tuple[List[str], str]  # (base_cmd_without_file, volume_support)


def _detect_player() -> Optional[PlayerSpec]:
    """
    Return the best available audio player for the current OS.
    Order tuned for macOS vs Ubuntu.
    """
    # explicit override for testing: AGENTCLICK_PLAYER=paplay etc handled via which check still
    # 1. macOS
    if sys.platform == "darwin":
        if shutil.which("afplay"):
            return (["afplay", "-v", str(VOL)], "afplay")
        # fall through to linux-style players (e.g. brew-installed sox/ffplay)

    # 2. Linux / Ubuntu — prefer PulseAudio / PipeWire, then ALSA, then generic
    # paplay volume: --volume=0..65536  (we map VOL accordingly)
    if shutil.which("paplay"):
        vol_int = str(int(VOL * 65536))
        return (["paplay", f"--volume={vol_int}"], "paplay")
    if shutil.which("pw-play"):
        # pw-play has no stable volume flag across versions; play at fixed vol
        return (["pw-play"], "pw-play")
    if shutil.which("aplay"):
        return (["aplay", "-q"], "aplay")
    if shutil.which("ffplay"):
        # -nodisp -autoexit -loglevel quiet
        # ffplay -volume is 0-100
        # we keep it simple: no volume mapping (ffplay volume flag is per-stream and flaky)
        return (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], "ffplay")
    if shutil.which("play"):  # sox
        return (["play", "-q"], "sox")

    # last resort: afplay if present on Linux (unlikely)
    if shutil.which("afplay"):
        return (["afplay", "-v", str(VOL)], "afplay")

    return None


_PLAYER: Optional[PlayerSpec] = _detect_player()
if _PLAYER:
    _debug(f"audio player: {' '.join(_PLAYER[0])} ({_PLAYER[1]})")
else:
    _debug("WARN: no audio player found — clicks will be silent")
    _debug("  macOS: afplay should be at /usr/bin/afplay")
    _debug("  Ubuntu: sudo apt install pulseaudio-utils  # for paplay")
    _debug("       or: sudo apt install alsa-utils       # for aplay")

# ── playback ─────────────────────────────────────────────────────────────────

_last_click: float = 0.0
_lock = threading.Lock()


def play(path: str) -> None:
    """Fire-and-forget playback; never raises, never blocks."""
    if not path or not os.path.isfile(path):
        return
    if _PLAYER is None:
        return
    base, _kind = _PLAYER
    # base already contains volume flag where applicable
    cmd = [*base, path]
    try:
        # Use Popen so we don't block the pty loop; suppress all output.
        # start_new_session avoids SIGINT propagation to the player child.
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        _debug(f"player not found at exec: {cmd[0]!r}")
    except OSError as e:
        _debug(f"player exec failed {cmd}: {e}")


def maybe_click() -> None:
    global _last_click
    now = time.monotonic()
    with _lock:
        if now - _last_click < MIN_INTERVAL:
            return
        _last_click = now
    if not _pool_paths:
        return
    play(random.choice(_pool_paths))


def on_newline() -> None:
    if ENTER_PATH:
        play(ENTER_PATH)
    else:
        maybe_click()

# ── pty helpers ───────────────────────────────────────────────────────────────


def _copy_winsize(fd: int) -> None:
    """Propagate terminal size to the pty master fd (best-effort)."""
    try:
        import fcntl
        import termios

        buf = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, buf)
    except Exception:
        pass


def _handle_sigwinch(signum, frame, master_fd: int) -> None:  # type: ignore[no-untyped-def]
    _copy_winsize(master_fd)

# ── main execution ───────────────────────────────────────────────────────────


def _print_help() -> None:
    help_text = f"""agentclick {__version__} — play click sounds as a command streams output

Usage:
  agentclick [options] <command> [args...]
  agentclick [options] -- <command> [args...]

Options:
  -h, --help       show this help
  --version        show version
  --test           play one key click and one enter sound, then exit
  --synth          force synthesized click for this invocation
  --dry-run        show resolved sounds & player without running a command

Environment:
  CLICK_VOL, CLICK_INTERVAL, CLICK_SOUND, ENTER_SOUND, CLICK_SYNTH,
  CLICK_FREQ_LOW, CLICK_FREQ_HIGH, AGENTCLICK_DEBUG

Examples:
  agentclick claude
  agentclick pi -- --help
  agentclick -- npm run dev
  agentclick --test
"""
    print(help_text, end="")


def _cmd_exists(argv: List[str]) -> bool:
    if not argv:
        return False
    prog = argv[0]
    # absolute / relative path
    if os.path.sep in prog:
        return os.path.isfile(prog) and os.access(prog, os.X_OK)
    return shutil.which(prog) is not None


def _run_with_pty(argv: List[str]) -> int:
    """
    Run argv via pty.spawn, playing sounds on output.
    Returns the child's exit code (0-255).
    """
    # pty.spawn's master_read callback
    def master_read(fd: int) -> bytes:
        try:
            data = os.read(fd, 8192)
        except OSError:
            return b""
        if not data:
            return b""
        # Sound triggers
        # Count newlines for enter sound (play once per chunk that contains newline)
        if b"\n" in data:
            on_newline()
        # printable ASCII heuristic — ignore ANSI escape sequences for the most part
        # but a simple printable count is fast and good enough
        printable = sum(1 for b in data if 33 <= b <= 126)
        if printable > 0:
            maybe_click()
        return data

    # Keep original SIGWINCH handler to restore later
    old_handler = None
    master_fd_holder: List[int] = []

    # Wrap stdin_read to capture master fd for winsize propagation.
    # pty.spawn opens the pty itself; we can hook SIGWINCH via a small
    # indirection: the first call to master_read happens after open, so we
    # also set up handler before spawn.
    def _sigwinch_handler(signum, frame):  # type: ignore[no-untyped-def]
        if master_fd_holder:
            _copy_winsize(master_fd_holder[0])

    # Install SIGWINCH forwarder if stdin is a tty
    have_sigwinch = hasattr(signal, "SIGWINCH")
    if have_sigwinch and sys.stdin.isatty():
        try:
            old_handler = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, _sigwinch_handler)
        except (ValueError, OSError):
            old_handler = None

    status: int = 1
    try:
        # pty.spawn handles stdin/stdout forwarding and tty raw mode
        # It returns a waitpid-style status integer
        status = pty.spawn(argv, master_read)  # type: ignore[arg-type]
    except FileNotFoundError:
        print(f"agentclick: command not found: {argv[0]}", file=sys.stderr)
        return 127
    except OSError as e:
        print(f"agentclick: failed to spawn {argv[0]!r}: {e}", file=sys.stderr)
        return 126
    finally:
        if have_sigwinch and old_handler is not None:
            try:
                signal.signal(signal.SIGWINCH, old_handler)
            except (ValueError, OSError):
                pass

    # Decode waitpid status -> exit code
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        # conventional shell: 128 + signal
        # also re-raise the signal for the parent if needed
        try:
            signal.signal(signal.Signals(sig), signal.SIG_DFL)
            os.kill(os.getpid(), sig)
        except Exception:
            pass
        return 128 + sig
    return 1


def _run_without_pty(argv: List[str]) -> int:
    """
    Fallback when stdin is not a tty or pty is unavailable.
    Pipes the child's stdout/stderr and plays clicks on output.
    Preserves interactivity less well but still audible.
    """
    _debug("using pipe fallback (no tty)")
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except FileNotFoundError:
        print(f"agentclick: command not found: {argv[0]}", file=sys.stderr)
        return 127
    except OSError as e:
        print(f"agentclick: failed to spawn {argv[0]!r}: {e}", file=sys.stderr)
        return 126

    assert proc.stdout is not None
    # Pump output in a thread so we can play sounds without blocking
    def pump() -> None:
        while True:
            chunk = proc.stdout.read(8192)  # type: ignore[union-attr]
            if not chunk:
                break
            # mirror to our stdout
            try:
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                break
            if b"\n" in chunk:
                on_newline()
            if sum(1 for b in chunk if 33 <= b <= 126) > 0:
                maybe_click()

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        ret = proc.wait()
    except KeyboardInterrupt:
        try:
            proc.terminate()
        except OSError:
            pass
        ret = 130
    t.join(timeout=1.0)
    return ret


def main() -> None:
    global _pool_paths
    # Lightweight arg parsing: we want to support `agentclick cmd` and `agentclick -- cmd`
    # and also `agentclick --help` etc without pulling argv into argparse's subcommand logic.
    raw = sys.argv[1:]

    # --synth is a prefix flag that can be combined with other flags (e.g. --synth --dry-run)
    synth_cli = False
    if raw and raw[0] == "--synth":
        synth_cli = True
        if KEY_PATH is not None:
            tmpdir = tempfile.gettempdir()
            pid = os.getpid()
            synth_pool: List[str] = []
            for i in range(5):
                p = os.path.join(tmpdir, f"agentclick_tick_{pid}_{i}.wav")
                _make_tick(p, FREQ_LOW + (FREQ_HIGH - FREQ_LOW) * i / 4)
                if p not in _TMP_SYNTH_FILES:
                    _TMP_SYNTH_FILES.append(p)
                synth_pool.append(p)
            _pool_paths = synth_pool
            print("agentclick: --synth enabled (ignoring wav files)", file=sys.stderr)
        raw = raw[1:]
        if not raw:
            print("agentclick: --synth requires a command or flag (--help/--dry-run/--test)", file=sys.stderr)
            sys.exit(1)

    # --help / --version / --test / --dry-run handling before command dispatch
    if not raw or raw in (["-h"], ["--help"]):
        if not raw:
            print("usage: agentclick <command> [args...]  (try --help)", file=sys.stderr)
            sys.exit(1)
        _print_help()
        sys.exit(0)

    if raw[0] in ("--version", "-V"):
        print(f"agentclick {__version__}")
        sys.exit(0)

    if raw[0] == "--test":
        # quick audio check
        print(f"agentclick {__version__} — test", file=sys.stderr)
        print(f"  key sound : {KEY_PATH or '(synth)'}", file=sys.stderr)
        if synth_cli:
            print("  (synth forced via --synth)", file=sys.stderr)
        print(f"  enter sound: {ENTER_PATH or '(synth)'}", file=sys.stderr)
        if _PLAYER:
            print(f"  player   : {' '.join(_PLAYER[0])} [{_PLAYER[1]}]", file=sys.stderr)
        else:
            print("  player   : (none found — install afplay / paplay / aplay)", file=sys.stderr)
        print("  playing key click...", file=sys.stderr)
        maybe_click()
        time.sleep(0.25)
        print("  playing enter...", file=sys.stderr)
        on_newline()
        time.sleep(0.6)
        print("  done. If you heard two sounds, you're good.", file=sys.stderr)
        sys.exit(0)

    if raw[0] == "--dry-run":
        eff_synth = SYNTH_MODE or synth_cli
        print(f"agentclick {__version__}")
        print(f"VOL={VOL} MIN_INTERVAL={MIN_INTERVAL} SYNTH_MODE={eff_synth}")
        print(f"KEY_PATH={KEY_PATH}")
        print(f"ENTER_PATH={ENTER_PATH}")
        print(f"pool={_pool_paths}")
        if _PLAYER:
            print(f"player={' '.join(_PLAYER[0])} kind={_PLAYER[1]}")
        else:
            print("player=(none)")
        remaining = raw[1:]
        if remaining and remaining[0] == "--":
            remaining = remaining[1:]
        if remaining:
            print(f"would run: {remaining}")
        sys.exit(0)

    # strip optional leading --
    argv: List[str]
    if raw[0] == "--":
        argv = raw[1:]
        if not argv:
            print("agentclick: -- requires a command", file=sys.stderr)
            sys.exit(1)
    else:
        # also support `agentclick --help` already handled; otherwise treat all as command
        argv = raw

    # handle `agentclick --help` after `--`? already covered
    if argv[0] in ("-h", "--help"):
        _print_help()
        sys.exit(0)

    if not _cmd_exists(argv):
        # still try to run so the OS gives the canonical error, but warn early
        hint = ""
        if shutil.which(argv[0]) is None and os.path.sep not in argv[0]:
            hint = f" (not in PATH: {os.environ.get('PATH','')[:120]})"
        print(f"agentclick: command not found: {argv[0]}{hint}", file=sys.stderr)
        # don't exit yet — let spawn try and return 127

    # Choose pty vs pipe based on whether we have a tty and pty is importable
    use_pty = sys.stdin.isatty() and sys.stdout.isatty() and sys.platform != "win32"
    # Allow opt-out: AGENTCLICK_NO_PTY=1
    if os.environ.get("AGENTCLICK_NO_PTY") == "1":
        use_pty = False

    if use_pty:
        try:
            code = _run_with_pty(argv)
        except Exception as e:
            _debug(f"pty path failed: {e}, falling back to pipe")
            code = _run_without_pty(argv)
    else:
        code = _run_without_pty(argv)

    sys.exit(code)


if __name__ == "__main__":
    main()
