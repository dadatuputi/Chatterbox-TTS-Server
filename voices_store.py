# File: voices_store.py
# Filesystem-backed data model for Custom Voices (no database).
#
# Ownership and visibility are derived from WHERE a voice file lives — the UI builds
# its view from the directory structure:
#
#   reference_audio/
#     <name>.wav              legacy flat files  -> treated as shared (visible to all)
#     shared/<name>.wav       shared             -> visible to all users
#     users/<email>/<name>.wav                   -> private to that user
#     .conds/<name>.<model>.pt                   -> cached conditionals (per dir)
#
# created-at comes from the file mtime. Admins see every user's voices; a normal user
# sees shared + legacy + their own. Resolution always reduces a requested name to its
# basename first, so it can never escape reference_audio (CWE-22 safe).

import os
import time
from pathlib import Path
from typing import Dict, List, Optional

SHARED_DIRNAME = "shared"
USERS_DIRNAME = "users"
CONDS_DIRNAME = ".conds"
AUDIO_EXTS = (".wav", ".mp3")


def sanitize_user(email: str) -> str:
    """Filesystem-safe key for a user directory. Empty identity -> 'local'."""
    email = (email or "").strip().lower()
    if not email:
        return "local"
    safe = "".join(c if (c.isalnum() or c in "@._-+") else "_" for c in email)
    return safe or "local"


def _shared_dir(root: Path) -> Path:
    return Path(root) / SHARED_DIRNAME


def _user_dir(root: Path, user: str) -> Path:
    return Path(root) / USERS_DIRNAME / sanitize_user(user)


def target_dir(root, user: str, visibility: str) -> Path:
    """Directory a newly created voice should be written to, created if needed."""
    d = _shared_dir(Path(root)) if visibility == "shared" else _user_dir(Path(root), user)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_for(path: Path, root: Path) -> Dict[str, str]:
    """Derive {visibility, owner} from a file's location under root."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = (path.name,)
    if len(parts) >= 2 and parts[0] == USERS_DIRNAME:
        return {"visibility": "private", "owner": parts[1]}
    if len(parts) >= 2 and parts[0] == SHARED_DIRNAME:
        return {"visibility": "all", "owner": "shared"}
    return {"visibility": "all", "owner": "legacy"}  # flat legacy file


def _created_iso(path: Path) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
    except OSError:
        return ""


def _entry(path: Path, root: Path) -> Dict:
    m = _meta_for(path, root)
    return {
        "filename": path.name,
        "owner": m["owner"],
        "visibility": m["visibility"],
        "created": _created_iso(path),
        "created_ts": _mtime(path),
    }


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _audio_files(d: Path):
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            yield p


def list_voices(root, user: str, is_admin: bool) -> List[Dict]:
    """Voices visible to `user` (or all, for an admin), newest first.

    De-duplicated by filename with precedence: the user's own private copy wins over a
    shared/legacy file of the same name.
    """
    root = Path(root)
    by_name: Dict[str, Dict] = {}

    def consider(p: Path, precedence: int):
        e = _entry(p, root)
        key = e["filename"]
        prev = by_name.get(key)
        if prev is None or precedence > prev["_prec"]:
            e["_prec"] = precedence
            by_name[key] = e

    # precedence: legacy(0) < shared(1) < someone-else's private(2, admin only) < own private(3)
    for p in _audio_files(root):
        consider(p, 0)
    for p in _audio_files(_shared_dir(root)):
        consider(p, 1)
    users_root = root / USERS_DIRNAME
    if users_root.is_dir():
        own = sanitize_user(user)
        for udir in sorted(users_root.iterdir()):
            if not udir.is_dir():
                continue
            is_own = udir.name == own
            if not is_own and not is_admin:
                continue
            for p in _audio_files(udir):
                consider(p, 3 if is_own else 2)

    out = list(by_name.values())
    for e in out:
        e.pop("_prec", None)
    out.sort(key=lambda e: (-e["created_ts"], e["filename"].lower()))
    return out


def can_access(root, filename: str, user: str, is_admin: bool) -> bool:
    return resolve(root, filename, user, is_admin) is not None


def resolve(root, filename: str, user: str, is_admin: bool) -> Optional[Path]:
    """Resolve a voice by basename to a path the user may use, or None.

    Search order: the user's private dir, shared, legacy-flat; an admin also falls back
    to any user's private dir. Only the basename is used, so this cannot traverse out.
    """
    name = Path(filename).name
    if not name:
        return None
    root = Path(root)
    for cand in (_user_dir(root, user) / name, _shared_dir(root) / name, root / name):
        if cand.is_file() and cand.suffix.lower() in AUDIO_EXTS:
            return cand
    if is_admin:
        users_root = root / USERS_DIRNAME
        if users_root.is_dir():
            for udir in sorted(users_root.iterdir()):
                cand = udir / name
                if udir.is_dir() and cand.is_file() and cand.suffix.lower() in AUDIO_EXTS:
                    return cand
    return None


def resolve_public(root, filename: str) -> Optional[Path]:
    """Resolve a voice visible to everyone (shared + legacy only) — for the API surface.

    Private per-user voices are intentionally not reachable here.
    """
    name = Path(filename).name
    if not name:
        return None
    root = Path(root)
    for cand in (_shared_dir(root) / name, root / name):
        if cand.is_file() and cand.suffix.lower() in AUDIO_EXTS:
            return cand
    return None


def owner_of(root, path: Path) -> str:
    """Owner key for a resolved voice path ('shared', 'legacy', or an email)."""
    return _meta_for(Path(path), Path(root))["owner"]


def conds_paths_for(voice_path: Path) -> List[Path]:
    """Cached .pt files that belong to a voice (beside it, in .conds/)."""
    d = voice_path.parent / CONDS_DIRNAME
    if not d.is_dir():
        return []
    return sorted(d.glob(f"{voice_path.stem}.*.pt"))


def delete_voice(voice_path: Path) -> int:
    """Delete a voice file and its cached conditionals. Returns #.pt removed."""
    removed = 0
    for pt in conds_paths_for(voice_path):
        try:
            pt.unlink()
            removed += 1
        except OSError:
            pass
    try:
        voice_path.unlink(missing_ok=True)
    except OSError:
        pass
    return removed
