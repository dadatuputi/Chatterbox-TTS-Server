# File: history_store.py
# Filesystem-backed per-user generation history (no database).
#
#   outputs/history/<email>/<YYYYMMDD-HHMMSS-mmm>__<voice>.<ext>
#
# The user owns the directory; the voice is encoded in the filename; created-at is the
# file mtime and size is the file size. A normal user sees only their own history; an
# admin can list everyone's, grouped by user and by voice, with disk usage.

import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import voices_store  # reuse user sanitization

HISTORY_DIRNAME = "history"
AUDIO_EXTS = (".wav", ".mp3", ".opus", ".flac")


def sanitize_user(user: str) -> str:
    return voices_store.sanitize_user(user)


def _history_root(out_root) -> Path:
    return Path(out_root) / HISTORY_DIRNAME


def _user_dir(out_root, user: str) -> Path:
    return _history_root(out_root) / sanitize_user(user)


def _safe_voice(voice: str) -> str:
    stem = Path(voice or "voice").stem
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "_" for c in stem)
    return cleaned[:60] or "voice"


def _iso(path: Path) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
    except OSError:
        return ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def save_generation(out_root, user: str, data: bytes, voice: str,
                    ext: str = "wav", ts: Optional[float] = None) -> Path:
    """Write a generated clip into the user's history and return its path."""
    d = _user_dir(out_root, user)
    d.mkdir(parents=True, exist_ok=True)
    ts = time.time() if ts is None else ts
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(ts))
    ms = int((ts % 1) * 1000)
    name = f"{stamp}-{ms:03d}__{_safe_voice(voice)}.{ext.lstrip('.')}"
    p = d / name
    p.write_bytes(data)
    return p


def _entry(path: Path) -> Dict:
    stem = path.stem
    voice = stem.split("__", 1)[1] if "__" in stem else ""
    return {
        "filename": path.name,
        "user": path.parent.name,
        "voice": voice,
        "created": _iso(path),
        "created_ts": _mtime(path),
        "size": _size(path),
    }


def _audio_files(d: Path):
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            yield p


def list_for_user(out_root, user: str) -> List[Dict]:
    items = [_entry(p) for p in _audio_files(_user_dir(out_root, user))]
    items.sort(key=lambda e: -e["created_ts"])
    return items


def list_all(out_root) -> List[Dict]:
    root = _history_root(out_root)
    items: List[Dict] = []
    if root.is_dir():
        for udir in sorted(root.iterdir()):
            if udir.is_dir():
                items.extend(_entry(p) for p in _audio_files(udir))
    items.sort(key=lambda e: -e["created_ts"])
    return items


def grouped_for_admin(out_root) -> Dict:
    """Everyone's history grouped by user and by voice, with disk usage."""
    items = list_all(out_root)
    by_user: Dict[str, Dict] = {}
    by_voice: Dict[str, Dict] = {}
    total = 0
    for e in items:
        total += e["size"]
        u = by_user.setdefault(e["user"], {"user": e["user"], "size": 0, "items": []})
        u["size"] += e["size"]; u["items"].append(e)
        v = by_voice.setdefault(e["voice"], {"voice": e["voice"], "size": 0, "count": 0})
        v["size"] += e["size"]; v["count"] += 1
    return {
        "total_size": total,
        "by_user": sorted(by_user.values(), key=lambda g: g["user"]),
        "by_voice": sorted(by_voice.values(), key=lambda g: -g["size"]),
    }


def disk_usage(out_root) -> int:
    return sum(e["size"] for e in list_all(out_root))


def resolve(out_root, user: str, filename: str) -> Optional[Path]:
    """Path of a history item owned by `user` (basename-only; traversal-safe)."""
    name = Path(filename).name
    if not name:
        return None
    p = _user_dir(out_root, user) / name
    return p if p.is_file() else None


def delete_item(out_root, user: str, filename: str) -> bool:
    p = resolve(out_root, user, filename)
    if p is None or not p.is_file():
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False


def clear_user(out_root, user: str) -> int:
    n = 0
    for p in list(_audio_files(_user_dir(out_root, user))):
        try:
            p.unlink(); n += 1
        except OSError:
            pass
    return n


def clear_voice(out_root, voice: str) -> int:
    """Delete every generation made with `voice`, across all users."""
    root = _history_root(out_root)
    n = 0
    if root.is_dir():
        for udir in root.iterdir():
            if not udir.is_dir():
                continue
            for p in list(_audio_files(udir)):
                if _entry(p)["voice"] == voice:
                    try:
                        p.unlink(); n += 1
                    except OSError:
                        pass
    return n
