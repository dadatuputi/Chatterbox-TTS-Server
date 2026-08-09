# File: routes_extra.py
# Fork-added endpoints, kept out of server.py so upstream merges to server.py stay
# small (server.py includes this router with one line). Config getters are called via
# the `config` module so tests can monkeypatch them.

import asyncio
import io
import logging
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import librosa
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

import config
from config import config_manager
import utils
import voice_import
import voices_store
import history_store
from webhelpers import (
    current_user,
    is_admin_request,
    enforce_max_duration as _enforce_max_duration,
    save_to_history as _save_to_history,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/voices", tags=["UI Helpers"])
async def get_voices_api(request: Request):
    """Rich Custom Voices list (filename, owner, visibility, created) scoped to the user."""
    try:
        ref_dir = config.get_reference_audio_path(ensure_absolute=True)
        return voices_store.list_voices(ref_dir, current_user(request), is_admin_request(request))
    except Exception as e:
        logger.error(f"Error listing voices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list voices.")


@router.post("/import_reference_url", tags=["File Management"])
async def import_reference_url_endpoint(
    request: Request,
    url: str = Form(..., description="YouTube (or any yt-dlp-supported) URL, or a local path."),
    segments: str = Form("", description='"0:30-1:00" | "1:20-1:50, 4:05-4:35" | "" for whole file.'),
    name: str = Form("", description="Destination filename stem (required unless preview)."),
    preview: bool = Form(False, description="If true, return the audio without saving it."),
    cookies: str = Form("", description="Optional cookies.txt contents for age-gated/members-only sources."),
    cleanup: bool = Form(False, description="Denoise, trim edge silence, and loudness-normalize the clip."),
    auto_extract: bool = Form(False, description="Auto-select the best contiguous speech window from a longer source."),
    visibility: str = Form("private", description="'private' (only you) or 'shared' (all users)."),
):
    """Feature A: import reference audio from a URL or local file, selecting time windows.

    Only the requested windows are downloaded (yt-dlp --download-sections), then joined
    with the ffmpeg concat demuxer. With preview=true the joined audio is returned for
    playback and nothing is written; otherwise it is saved into the reference-audio dir.
    """
    # Admin gating: when auth is enabled, arbitrary-audio import is admin-only. A caller
    # authenticated via bearer token (API surface) or an anonymous request is refused.
    auth_cfg = get_auth_config()
    if auth_cfg.get("enabled") and not getattr(request.state, "is_admin", False):
        raise HTTPException(
            status_code=403,
            detail="Reference import is restricted to the admin user.",
        )

    caps = voice_import.import_capabilities()
    if not caps["available"]:
        raise HTTPException(
            status_code=503,
            detail="Reference import is unavailable: ffmpeg is not installed on the server.",
        )

    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="A URL or local file path is required.")

    is_local = voice_import.is_local_source(url)
    if not is_local and not caps["yt_dlp"]:
        raise HTTPException(
            status_code=503,
            detail=(
                "URL import is unavailable: yt-dlp is not installed. Install it with "
                "'pip install -r requirements-import.txt', or supply a local file path."
            ),
        )

    # Parse segments up front so a bad spec fails fast with a clear message.
    try:
        segs = voice_import.parse_segments(segments)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    ref_dir = config.get_reference_audio_path(ensure_absolute=True)

    # Resolve the destination (commit mode) before doing expensive work.
    dest_path: Optional[Path] = None
    if not preview:
        if not name.strip():
            raise HTTPException(
                status_code=400, detail="A destination name is required to save the reference."
            )
        stem = Path(name.strip()).stem  # drop any extension / path the user typed
        if not stem:
            raise HTTPException(status_code=400, detail="Invalid destination name.")
        vis = "shared" if visibility == "shared" else "private"
        dest_dir = voices_store.target_dir(ref_dir, current_user(request), vis)
        dest_path = dest_dir / f"{stem}.wav"

    work_dir = voice_import.make_work_dir()
    tmp_out = os.path.join(work_dir, "_reference.wav")
    cookies_path = ""
    try:
        if cookies.strip():
            cookies_path = os.path.join(work_dir, "cookies.txt")
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write(cookies)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: voice_import.fetch_reference(url, segs, tmp_out, work_dir, cookies_path),
            )
        except voice_import.ImportError_ as e:
            raise HTTPException(status_code=422, detail=str(e))
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Import timed out while fetching/processing audio.")

        if not os.path.exists(tmp_out):
            raise HTTPException(status_code=422, detail="Import produced no audio.")

        # Optional: auto-select the best speech window from a longer source (Tier 1).
        if auto_extract:
            try:
                await loop.run_in_executor(None, lambda: voice_import.extract_best_clip(tmp_out))
            except voice_import.ImportError_ as e:
                logger.warning(f"Best-clip extraction failed, using full clip: {e}")

        # Optional cleanup (denoise / trim silence / normalize) before preview or save.
        if cleanup:
            try:
                await loop.run_in_executor(None, lambda: voice_import.clean_audio(tmp_out))
            except voice_import.ImportError_ as e:
                logger.warning(f"Reference cleanup failed, using raw audio: {e}")

        # Duration for the too-short warning (A6).
        try:
            duration = float(librosa.get_duration(path=tmp_out))
        except Exception:
            duration = 0.0
        warning = ""
        if 0 < duration < 6.0:
            warning = (
                f"Reference is only {duration:.1f}s — under 6s is too short for a stable "
                "clone. Widen the segment for better results."
            )

        if preview:
            # Return the joined audio for playback; write nothing to reference_audio.
            with open(tmp_out, "rb") as f:
                audio_bytes = f.read()
            headers = {
                "X-Import-Duration": f"{duration:.2f}",
                "X-Import-Preview": "true",
            }
            if warning:
                headers["X-Import-Warning"] = warning
            return Response(content=audio_bytes, media_type="audio/wav", headers=headers)

        # Commit mode: move into the reference-audio dir, trimming to the cap.
        assert dest_path is not None
        shutil.move(tmp_out, dest_path)
        max_duration = config_manager.get_int("audio_output.max_reference_duration_sec", 30)
        duration, warning = _enforce_max_duration(dest_path, max_duration, duration, warning)
        is_valid, validation_msg = utils.validate_reference_audio(dest_path, None)
        if not is_valid:
            dest_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=validation_msg)

        logger.info(f"Imported reference audio to: {dest_path} ({duration:.1f}s)")
        return JSONResponse(
            content={
                "message": f"Imported '{dest_path.name}' ({duration:.1f}s).",
                "filename": dest_path.name,
                "duration": round(duration, 2),
                "warning": warning,
                "all_reference_files": [v["filename"] for v in voices_store.list_voices(
                    ref_dir, current_user(request), is_admin_request(request))],
            },
            status_code=200,
        )
    finally:
        voice_import.cleanup_work_dir(work_dir)


@router.post("/record_reference", tags=["File Management"])
async def record_reference_endpoint(
    request: Request,
    audio: UploadFile = File(..., description="Recorded audio blob (webm/ogg/mp4/wav)."),
    name: str = Form(..., description="Destination filename stem."),
    cleanup: bool = Form(False, description="Denoise, trim edge silence, and loudness-normalize the recording."),
    visibility: str = Form("private", description="'private' (only you) or 'shared' (all users)."),
):
    """Save a microphone recording as a reference-audio WAV.

    The browser records with MediaRecorder (usually webm/opus), so the blob is
    transcoded to WAV with ffmpeg before validation and storage.
    """
    if not voice_import.has_ffmpeg():
        raise HTTPException(
            status_code=503,
            detail="Recording capture is unavailable: ffmpeg is not installed on the server.",
        )
    if not name.strip():
        raise HTTPException(status_code=400, detail="A name is required to save the recording.")

    ref_dir = config.get_reference_audio_path(ensure_absolute=True)
    stem = Path(name.strip()).stem
    if not stem:
        raise HTTPException(status_code=400, detail="Invalid destination name.")
    vis = "shared" if visibility == "shared" else "private"
    dest_path = voices_store.target_dir(ref_dir, current_user(request), vis) / f"{stem}.wav"

    work_dir = voice_import.make_work_dir()
    # Keep the uploaded extension so ffmpeg can sniff the container.
    src_ext = Path(audio.filename or "").suffix or ".webm"
    raw_path = os.path.join(work_dir, f"_recorded{src_ext}")
    tmp_wav = os.path.join(work_dir, "_recorded.wav")
    try:
        with open(raw_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        loop = asyncio.get_running_loop()
        # Downmix to mono 24 kHz WAV to match the model's sample rate.
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", raw_path,
                 "-ac", "1", "-ar", "24000", tmp_wav],
                capture_output=True, text=True,
            ),
        )
        if result.returncode or not os.path.exists(tmp_wav):
            raise HTTPException(
                status_code=422,
                detail="Could not decode the recording: " + (result.stderr[-500:] or "unknown error"),
            )

        shutil.move(tmp_wav, dest_path)
        if cleanup:
            try:
                await loop.run_in_executor(None, lambda: voice_import.clean_audio(str(dest_path)))
            except voice_import.ImportError_ as e:
                logger.warning(f"Recording cleanup failed, using raw audio: {e}")
        try:
            duration = float(librosa.get_duration(path=dest_path))
        except Exception:
            duration = 0.0
        max_duration = config_manager.get_int("audio_output.max_reference_duration_sec", 30)
        warning = ""
        if 0 < duration < 6.0:
            warning = (
                f"Recording is only {duration:.1f}s — under 6s is too short for a stable "
                "clone. Record a longer sample."
            )
        duration, warning = _enforce_max_duration(dest_path, max_duration, duration, warning)
        is_valid, validation_msg = utils.validate_reference_audio(dest_path, None)
        if not is_valid:
            dest_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=validation_msg)
        logger.info(f"Saved recorded reference to: {dest_path} ({duration:.1f}s)")
        return JSONResponse(
            content={
                "message": f"Saved '{dest_path.name}' ({duration:.1f}s).",
                "filename": dest_path.name,
                "duration": round(duration, 2),
                "warning": warning,
                "all_reference_files": [v["filename"] for v in voices_store.list_voices(
                    ref_dir, current_user(request), is_admin_request(request))],
            },
            status_code=200,
        )
    finally:
        await audio.close()
        voice_import.cleanup_work_dir(work_dir)


@router.post("/delete_reference", tags=["File Management"])
async def delete_reference_endpoint(request: Request, name: str = Form(...)):
    """Delete a Custom Voice + its cached .pt conditionals.

    A user may delete their own private voices; deleting shared/legacy voices (or
    anyone else's) requires admin.
    """
    user = current_user(request)
    admin = is_admin_request(request)
    ref_dir = config.get_reference_audio_path(ensure_absolute=True)
    target = voices_store.resolve(ref_dir, name, user, admin)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Reference '{name}' not found.")

    owner = voices_store.owner_of(ref_dir, target)
    is_own_private = (owner == voices_store.sanitize_user(user))
    if not (is_own_private or admin):
        raise HTTPException(
            status_code=403,
            detail="You can only delete your own voices; shared voices are admin-only.",
        )

    removed_conds = voices_store.delete_voice(target)
    logger.info(f"Deleted reference '{target.name}' and {removed_conds} cached conditionals.")
    voices = voices_store.list_voices(ref_dir, user, admin)
    return JSONResponse(
        content={
            "message": f"Deleted '{target.name}'.",
            "removed_conditionals": removed_conds,
            "all_reference_files": [v["filename"] for v in voices],
        }
    )


@router.get("/download_voice", tags=["File Management"])
async def download_voice_endpoint(request: Request, name: str):
    """Download a Custom Voice as a zip: the reference audio plus every cached .pt."""
    ref_dir = config.get_reference_audio_path(ensure_absolute=True)
    target = voices_store.resolve(ref_dir, name, current_user(request), is_admin_request(request))
    if target is None:
        raise HTTPException(status_code=404, detail=f"Voice '{name}' not found.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(target, arcname=target.name)
        for pt in voices_store.conds_paths_for(target):
            zf.write(pt, arcname=f".conds/{pt.name}")
    buf.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{target.stem}.zip"'}
    return StreamingResponse(buf, media_type="application/zip", headers=headers)


@router.get("/api/history", tags=["History"])
async def history_list_endpoint(request: Request):
    """The current user's generation history (newest first)."""
    out_root = config.get_output_path(ensure_absolute=True)
    return {"items": history_store.list_for_user(out_root, current_user(request))}


@router.get("/api/history/admin", tags=["History"])
async def history_admin_endpoint(request: Request):
    """All users' history grouped by user and by voice, with disk usage. Admin only."""
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin only.")
    out_root = config.get_output_path(ensure_absolute=True)
    return history_store.grouped_for_admin(out_root)


@router.get("/history/file", tags=["History"])
async def history_file_endpoint(request: Request, name: str, user: str = ""):
    """Stream a history clip. A user can fetch their own; admin can fetch anyone's."""
    admin = is_admin_request(request)
    owner = user.strip().lower() if (user and admin) else current_user(request)
    out_root = config.get_output_path(ensure_absolute=True)
    path = history_store.resolve(out_root, owner, name)
    if path is None:
        raise HTTPException(status_code=404, detail="History item not found.")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.post("/history/delete", tags=["History"])
async def history_delete_endpoint(request: Request, name: str = Form(...), user: str = Form("")):
    """Delete a history item — your own, or anyone's if admin."""
    admin = is_admin_request(request)
    owner = user.strip().lower() if (user and admin) else current_user(request)
    if user and user.strip().lower() != current_user(request) and not admin:
        raise HTTPException(status_code=403, detail="You can only delete your own history.")
    out_root = config.get_output_path(ensure_absolute=True)
    ok = history_store.delete_item(out_root, owner, name)
    if not ok:
        raise HTTPException(status_code=404, detail="History item not found.")
    return {"message": "Deleted.", "user": owner, "filename": Path(name).name}


@router.post("/history/clear", tags=["History"])
async def history_clear_endpoint(request: Request, scope: str = Form(...), key: str = Form("")):
    """Clear a history group. scope='user' clears a user's history (own, or any if admin);
    scope='voice' clears every generation made with a voice (admin only)."""
    admin = is_admin_request(request)
    me = current_user(request)
    out_root = config.get_output_path(ensure_absolute=True)
    if scope == "user":
        target = key.strip().lower() if (key and admin) else me
        if target != me and not admin:
            raise HTTPException(status_code=403, detail="Admin only.")
        removed = history_store.clear_user(out_root, target)
    elif scope == "voice":
        if not admin:
            raise HTTPException(status_code=403, detail="Admin only.")
        removed = history_store.clear_voice(out_root, key)
    else:
        raise HTTPException(status_code=400, detail="scope must be 'user' or 'voice'.")
    return {"message": f"Cleared {removed} item(s).", "removed": removed}
