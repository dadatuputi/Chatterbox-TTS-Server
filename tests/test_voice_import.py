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


@requires_ffmpeg
def test_trim_audio_caps_duration(tone_wav):
    import soundfile as sf
    src = tone_wav(seconds=40)
    assert vi.trim_audio(src, 30) is True
    assert sf.info(src).duration <= 31.0  # trimmed to ~cap


@requires_ffmpeg
def test_trim_audio_noop_when_short(tone_wav):
    import soundfile as sf
    src = tone_wav(seconds=5)
    before = sf.info(src).duration
    vi.trim_audio(src, 30)  # already under cap; ffmpeg -t just re-writes
    assert sf.info(src).duration <= before + 0.2


@requires_ffmpeg
def test_extract_best_clip_picks_longest_speech(tmp_path):
    import numpy as np
    import soundfile as sf
    sr = 24000

    def tone(sec, f=200):
        return (0.2 * np.sin(2 * np.pi * f * np.arange(int(sr * sec)) / sr)).astype("float32")

    def sil(sec):
        return np.zeros(int(sr * sec), dtype="float32")

    # 2s silence, 12s speech, 3s silence, 4s speech, 2s silence  (23s total)
    sig = np.concatenate([sil(2), tone(12), sil(3), tone(4, 260), sil(2)])
    p = str(tmp_path / "long.wav")
    sf.write(p, sig, sr)

    dur, segs = vi._probe_speech_segments(p, -30, 0.4)
    assert len(segs) == 2  # two speech regions detected

    assert vi.extract_best_clip(p, target_sec=18.0) is True
    # 12s + 4s of speech with the silences removed, capped at target
    assert 14.0 < sf.info(p).duration <= 18.5


@requires_ffmpeg
def test_extract_best_clip_noop_on_short_clean(tmp_path):
    import numpy as np
    import soundfile as sf
    sr = 24000
    tone = (0.2 * np.sin(2 * np.pi * 200 * np.arange(sr * 8) / sr)).astype("float32")
    p = str(tmp_path / "short.wav")
    sf.write(p, tone, sr)
    # Already short and contiguous — nothing to select.
    assert vi.extract_best_clip(p, target_sec=18.0) is False


@requires_ffmpeg
def test_clean_audio_outputs_mono_24k(tmp_path):
    import numpy as np
    import soundfile as sf
    # 6s tone with 1s silence padding each end, stereo 44.1k
    sr = 44100
    tone = 0.2 * np.sin(2 * np.pi * 200 * np.arange(sr * 6) / sr)
    sig = np.concatenate([np.zeros(sr), tone, np.zeros(sr)]).astype("float32")
    stereo = np.column_stack([sig, sig])
    p = str(tmp_path / "ref.wav")
    sf.write(p, stereo, sr)
    assert vi.clean_audio(p) is True
    info = sf.info(p)
    assert info.samplerate == 24000
    assert info.channels == 1
    assert info.duration < 7.5  # edge silence trimmed
