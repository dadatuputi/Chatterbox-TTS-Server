# File: voice_import.py
# Feature A (see BUILD_SPEC): import reference audio from a URL (YouTube or any
# yt-dlp-supported source) or a local file, selecting one or more time windows.
#
# Ported from the tested notebook logic in cell_voice_widgets.py:
#   * _ts()            - timestamp parser (SS | MM:SS | HH:MM:SS)
#   * _parse_segments  - "0:30-1:00, 4:05-4:35" -> [(s, e), ...]
#   * per-segment yt-dlp --download-sections fetch (so a 30s clip from a 2h video
#     does NOT download the whole video)
#   * ffmpeg concat demuxer to join segments
#
# yt-dlp is an OPTIONAL dependency: if the yt-dlp CLI is missing the URL path is
# disabled and the UI panel is hidden, rather than crashing the server.

import logging
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class ImportError_(Exception):
    """Raised for user-facing import failures (bad segments, download errors, etc.)."""


# --- Capability detection -----------------------------------------------------

def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def has_yt_dlp() -> bool:
    return shutil.which("yt-dlp") is not None


def import_capabilities() -> dict:
    """Report which import paths are usable given the installed tools.

    URL import needs both yt-dlp and ffmpeg; local-file import needs only ffmpeg.
    """
    ffmpeg = has_ffmpeg()
    yt_dlp = has_yt_dlp()
    return {
        "ffmpeg": ffmpeg,
        "yt_dlp": yt_dlp,
        "url_import": ffmpeg and yt_dlp,
        "local_import": ffmpeg,
        # The UI shows the panel when at least one path is available.
        "available": ffmpeg,
    }


# --- Timestamp / segment parsing (ported, unchanged semantics) ----------------

def ts_to_seconds(t: str) -> float:
    """'90' | '1:30' | '1:02:15' -> seconds."""
    sec = 0.0
    for p in t.strip().split(":"):
        if p == "":
            raise ValueError(f"invalid timestamp '{t}'")
        sec = sec * 60 + float(p)
    return sec


def parse_segments(spec: str) -> List[Tuple[float, float]]:
    """Parse 'a-b, c-d' into [(a, b), (c, d)]. Empty spec -> [] (whole file)."""
    segs: List[Tuple[float, float]] = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" not in chunk:
            raise ValueError(f"segment '{chunk}' needs a start-end, e.g. 0:30-1:00")
        a, b = chunk.rsplit("-", 1)
        s, e = ts_to_seconds(a), ts_to_seconds(b)
        if e <= s:
            raise ValueError(f"segment '{chunk}': end must be after start")
        segs.append((s, e))
    return segs


# --- ffmpeg / yt-dlp helpers --------------------------------------------------

def _run(cmd: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _join(parts: List[str], dest: str, work_dir: str) -> None:
    """Concatenate wav parts into dest using the ffmpeg concat demuxer."""
    if len(parts) == 1:
        os.replace(parts[0], dest)
        return
    lst = os.path.join(work_dir, "_concat.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    r = _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
              "-i", lst, "-c", "copy", dest])
    if r.returncode:
        raise ImportError_("ffmpeg concat failed:\n" + r.stderr[-2000:])
    for p in parts:
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.remove(lst)
    except OSError:
        pass


def is_local_source(source: str) -> bool:
    return os.path.exists(os.path.expanduser(source))


def fetch_reference(
    source: str,
    segments: List[Tuple[float, float]],
    dest: str,
    work_dir: str,
    cookies_path: str = "",
) -> None:
    """Pull the requested segments from a local file or URL into a single wav at `dest`.

    Local sources are cut with ffmpeg -ss/-to. URLs are fetched per-segment with
    yt-dlp --download-sections so only the requested windows are downloaded.
    """
    is_local = is_local_source(source)

    if is_local:
        _fetch_local(os.path.expanduser(source), segments, dest, work_dir)
        return

    if not has_yt_dlp():
        raise ImportError_(
            "yt-dlp is not installed on the server, so URL import is unavailable. "
            "Install it (pip install -r requirements-import.txt) or use a local file path."
        )
    _fetch_url(source, segments, dest, work_dir, cookies_path)


def _fetch_local(src: str, segments, dest: str, work_dir: str) -> None:
    if not has_ffmpeg():
        raise ImportError_("ffmpeg is not installed on the server; cannot process audio.")
    logger.info(f"Import: local source '{src}'")
    if not segments:
        r = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, dest])
        if r.returncode:
            raise ImportError_("ffmpeg failed:\n" + r.stderr[-2000:])
        return
    parts = []
    for i, (s, e) in enumerate(segments):
        out_p = os.path.join(work_dir, f"_seg{i}.wav")
        logger.info(f"Import: cutting {s:.0f}s-{e:.0f}s")
        r = _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s), "-to", str(e),
                  "-i", src, out_p])
        if r.returncode or not os.path.exists(out_p):
            raise ImportError_(f"segment {i + 1} failed:\n" + r.stderr[-2000:])
        parts.append(out_p)
    _join(parts, dest, work_dir)


def _fetch_url(source: str, segments, dest: str, work_dir: str, cookies_path: str) -> None:
    base = ["yt-dlp", "-x", "--audio-format", "wav", "--force-overwrites",
            "-q", "--no-warnings"]
    if cookies_path and os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
        base += ["--cookies", cookies_path]
        logger.info("Import: using supplied cookies file")
    logger.info(f"Import: remote source '{source}'")

    if not segments:
        # yt-dlp appends the audio extension itself; give it a stem and locate the result.
        stem = os.path.join(work_dir, "_full")
        r = _run(base + ["-o", stem + ".%(ext)s", source])
        produced = stem + ".wav"
        if not os.path.exists(produced):
            raise ImportError_("download failed:\n" + r.stdout[-1500:] + r.stderr[-1500:])
        os.replace(produced, dest)
        return

    parts = []
    for i, (s, e) in enumerate(segments):
        out_p = os.path.join(work_dir, f"_seg{i}.wav")
        logger.info(f"Import: fetching {s:.0f}s-{e:.0f}s")
        r = _run(base + ["--download-sections", f"*{s}-{e}",
                         "--force-keyframes-at-cuts", "-o", out_p, source])
        if not os.path.exists(out_p):
            raise ImportError_(
                f"segment {i + 1} failed:\n" + r.stdout[-1500:] + r.stderr[-1500:]
            )
        parts.append(out_p)
    _join(parts, dest, work_dir)


def trim_audio(path: str, max_sec: float) -> bool:
    """Trim the audio at `path` in place to the first `max_sec` seconds.

    Returns True if a trim was performed. Intended to enforce a maximum reference
    duration without rejecting the file (the model only uses the opening seconds).
    """
    if not has_ffmpeg() or not max_sec or max_sec <= 0:
        return False
    tmp = path + ".trim.wav"
    r = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-t", str(max_sec), tmp])
    if r.returncode or not os.path.exists(tmp):
        raise ImportError_("trim failed:\n" + r.stderr[-1000:])
    os.replace(tmp, path)
    return True


def clean_audio(path: str) -> bool:
    """Denoise, trim edge silence, and loudness-normalize `path` in place.

    Optional (off by default) — some references have wanted ambience (e.g. a hall's
    natural reverb) that cleanup would strip. Returns True if applied.
    """
    if not has_ffmpeg():
        return False
    af = (
        "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,"
        "areverse,"
        "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,"
        "areverse,"
        "highpass=f=70,"
        "afftdn=nf=-20,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )
    tmp = path + ".clean.wav"
    r = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
              "-af", af, "-ar", "24000", "-ac", "1", tmp])
    if r.returncode or not os.path.exists(tmp):
        raise ImportError_("cleanup failed:\n" + r.stderr[-1000:])
    os.replace(tmp, path)
    return True


def make_work_dir(base_tmp: Optional[str] = None) -> str:
    """Create a scratch directory for segment downloads/cuts."""
    return tempfile.mkdtemp(prefix="voice_import_", dir=base_tmp)


def cleanup_work_dir(work_dir: str) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)
