# Shared pytest fixtures / setup for the Chatterbox-TTS-Server test suite.
#
# Makes ffmpeg discoverable (system ffmpeg preferred; falls back to the imageio-ffmpeg
# bundled binary if that dev package happens to be installed) so the ffmpeg-dependent
# tests run both inside the app container and in a bare dev checkout.

import os
import shutil

import numpy as np
import pytest
import soundfile as sf


def _ensure_ffmpeg() -> bool:
    if shutil.which("ffmpeg"):
        return True
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        shim = os.path.join(os.path.dirname(exe), "_ffmpeg_shim")
        os.makedirs(shim, exist_ok=True)
        link = os.path.join(shim, "ffmpeg")
        if not os.path.exists(link):
            try:
                os.symlink(exe, link)
            except OSError:
                shutil.copy(exe, link)
                os.chmod(link, 0o755)
        os.environ["PATH"] = shim + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass
    return shutil.which("ffmpeg") is not None


FFMPEG_AVAILABLE = _ensure_ffmpeg()

requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg not on PATH"
)


@pytest.fixture
def tone_wav(tmp_path):
    """Factory: write a sine-tone WAV of the given duration/sample-rate, return its path."""
    def _make(seconds=8.0, sr=24000, name="tone.wav", freq=180.0):
        n = int(sr * seconds)
        data = (0.1 * np.sin(2 * np.pi * freq * np.arange(n) / sr)).astype("float32")
        path = tmp_path / name
        sf.write(str(path), data, sr)
        return str(path)
    return _make
