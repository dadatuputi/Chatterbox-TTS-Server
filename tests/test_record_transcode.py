"""Mic capture — the ffmpeg transcode used by /record_reference (webm/opus -> 24kHz mono WAV)."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import requires_ffmpeg


@requires_ffmpeg
def test_webm_opus_to_mono_24k_wav(tone_wav, tmp_path):
    import soundfile as sf

    # Encode a webm/opus blob like the browser's MediaRecorder would produce.
    src = tone_wav(seconds=8, sr=48000)
    webm = str(tmp_path / "rec.webm")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-c:a", "libopus", webm],
        check=True,
    )
    assert os.path.getsize(webm) > 0

    # Same command the endpoint runs.
    out = str(tmp_path / "out.wav")
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", webm, "-ac", "1", "-ar", "24000", out],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    info = sf.info(out)
    assert info.samplerate == 24000
    assert info.channels == 1
    assert 7.5 < info.duration < 8.5
