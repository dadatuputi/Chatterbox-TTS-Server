"""Feature A — reference import: timestamp/segment parsing and ffmpeg cut+concat."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_import as vi
from conftest import requires_ffmpeg


# --- pure parsing ---------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("90", 90.0),
    ("1:30", 90.0),
    ("1:02:15", 3735.0),
    ("0:00", 0.0),
])
def test_ts_to_seconds(text, expected):
    assert vi.ts_to_seconds(text) == expected


def test_parse_segments_valid():
    assert vi.parse_segments("0:30-1:00, 4:05-4:35") == [(30.0, 60.0), (245.0, 275.0)]


def test_parse_segments_empty_is_whole_file():
    assert vi.parse_segments("") == []
    assert vi.parse_segments("   ") == []


@pytest.mark.parametrize("bad", ["0:30", "1:00-0:30", "5-5"])
def test_parse_segments_rejects_bad(bad):
    with pytest.raises(ValueError):
        vi.parse_segments(bad)


def test_import_capabilities_shape():
    caps = vi.import_capabilities()
    for key in ("ffmpeg", "yt_dlp", "url_import", "local_import", "available"):
        assert key in caps


def test_is_local_source(tone_wav):
    path = tone_wav(seconds=1)
    assert vi.is_local_source(path) is True
    assert vi.is_local_source("https://example.com/x") is False


# --- ffmpeg cut + concat --------------------------------------------------

@requires_ffmpeg
def test_local_two_segments_concat(tone_wav, tmp_path):
    import soundfile as sf
    src = tone_wav(seconds=20)
    dest = str(tmp_path / "out.wav")
    work = vi.make_work_dir()
    try:
        vi.fetch_reference(src, vi.parse_segments("0:02-0:07, 0:10-0:15"), dest, work)
        dur = sf.info(dest).duration
        assert 9.0 < dur < 11.0, f"expected ~10s, got {dur}"
    finally:
        vi.cleanup_work_dir(work)
    assert not os.path.exists(work), "scratch dir should be cleaned"


@requires_ffmpeg
def test_local_whole_file(tone_wav, tmp_path):
    import soundfile as sf
    src = tone_wav(seconds=12)
    dest = str(tmp_path / "whole.wav")
    work = vi.make_work_dir()
    try:
        vi.fetch_reference(src, [], dest, work)
        assert 11.0 < sf.info(dest).duration < 13.0
    finally:
        vi.cleanup_work_dir(work)
