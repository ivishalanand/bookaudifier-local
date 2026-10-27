# Bookaudifier

Turn a PDF into a narrated audiobook you read along with. Runs fully on your Mac with the local Kokoro TTS model.

## Run

```bash
uv run uvicorn server.app:app --port 8765
```

Open http://localhost:8765, upload a PDF and press play. The first run downloads the Kokoro model (~330 MB) from Hugging Face; after that everything is offline.

**Keys:** space = play/pause, ← / → = back/forward 10 s. Click any sentence on the page to start reading there.

Books, page images, audio and progress are stored in `data/`. See [SPEC.md](SPEC.md) for the design and plans for other voice engines.
