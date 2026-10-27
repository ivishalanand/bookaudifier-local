"""TTS engines behind one interface. Pick with the TTS_ENGINE env var."""
import os
import re
import threading

import numpy as np


os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # xet downloads stall on this Mac


class Kokoro:
    name = "kokoro"
    sample_rate = 24000
    voice = "af_heart"  # default
    # Kokoro's best-rated English voices; each downloads (~0.5 MB) on first use.
    voices = {
        "af_heart": "Heart · US female",
        "af_bella": "Bella · US female",
        "af_nicole": "Nicole · US female",
        "af_sarah": "Sarah · US female",
        "am_michael": "Michael · US male",
        "am_fenrir": "Fenrir · US male",
        "am_puck": "Puck · US male",
        "bf_emma": "Emma · UK female",
        "bf_isabella": "Isabella · UK female",
        "bm_george": "George · UK male",
        "bm_fable": "Fable · UK male",
    }

    def __init__(self):
        from kokoro import KPipeline

        us = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
        uk = KPipeline(lang_code="b", repo_id="hexgrad/Kokoro-82M", model=us.model)  # British pronunciation
        self.pipelines = {"a": us, "b": uk}

    def synthesize(self, text: str, voice: str) -> tuple[np.ndarray, list]:
        chunks, tokens, offset = [], [], 0.0
        for r in self.pipelines[voice[0]](text, voice=voice):
            if r.audio is None:
                continue
            for t in r.tokens or []:
                if t.start_ts is not None:
                    tokens.append((t.text, offset + t.start_ts, offset + t.end_ts))
            chunks.append(r.audio.numpy())
            offset += len(chunks[-1]) / self.sample_rate
        return (np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)), tokens


# Add Chatterbox / Higgs / VoxCPM2 here later with the same name/sample_rate/voice/voices/synthesize shape.
# synthesize returns (audio, [(word, start_s, end_s), ...]); an engine without timings returns [].
ENGINES = {"kokoro": Kokoro}

_engine = None
_lock = threading.Lock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            _engine = ENGINES[os.environ.get("TTS_ENGINE", "kokoro")]()
        return _engine


def synthesize(text: str, voice: str) -> tuple[np.ndarray, list]:
    e = engine()
    with _lock:  # one model, one generation at a time
        return e.synthesize(text, voice)


def _letters(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


def align(words: list[str], tokens: list) -> list:
    """Give each PDF word the (start, end) of the spoken tokens covering its letters, or None."""
    spans, pos = [], 0
    for text, start, end in tokens:
        n = len(_letters(text))
        spans.append((pos, pos + n, start, end))
        pos += n
    sizes = [len(_letters(w)) for w in words]
    scale = pos / (sum(sizes) or 1)  # stretch if the speech spelled things differently
    out, pos = [], 0
    for n in sizes:
        a, b = pos * scale, (pos + n) * scale
        pos += n
        hits = [(s, e) for ta, tb, s, e in spans if n and ta < b and tb > a]
        out.append((min(h[0] for h in hits), max(h[1] for h in hits)) if hits else None)
    return out
