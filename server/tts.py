"""TTS engines behind one interface. Pick with the TTS_ENGINE env var."""
import os
import threading

import numpy as np


class Kokoro:
    name = "kokoro"
    sample_rate = 24000

    def __init__(self, voice: str = "af_heart"):
        from kokoro import KPipeline

        self.voice = voice
        self.pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")

    def synthesize(self, text: str) -> np.ndarray:
        chunks = [r.audio.numpy() for r in self.pipeline(text, voice=self.voice) if r.audio is not None]
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


# Add Chatterbox / Higgs / VoxCPM2 here later with the same name/sample_rate/voice/synthesize shape.
ENGINES = {"kokoro": Kokoro}

_engine = None
_lock = threading.Lock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            _engine = ENGINES[os.environ.get("TTS_ENGINE", "kokoro")]()
        return _engine


def synthesize(text: str) -> np.ndarray:
    e = engine()
    with _lock:  # one model, one generation at a time
        return e.synthesize(text)
