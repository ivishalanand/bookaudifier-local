# Bookaudifier — spec

A local, free app that turns a PDF into a narrated audiobook you read along with.

## Goals
- Upload a PDF. The app saves it to a library.
- Press play: a local TTS model narrates the book.
- The sentence being read is highlighted on the real PDF page, and the page auto-scrolls to follow it.
- Bottom player bar: play/pause, back 10 s, forward 10 s, previous/next sentence, speed (0.75×–2×), book progress scrubber.
- Click any sentence on the page to start reading from there.
- Progress (position + speed) is saved; reopening a book resumes where you left off.
- Everything runs locally. No paid APIs, no network calls after setup.

## Non-goals (for now)
- EPUB or other formats (PDF only).
- Scanned PDFs without a text layer (no OCR).
- Word-by-word highlight (sentence-level only).
- Accounts, sync, mobile.

## Architecture
- **Backend:** Python + FastAPI (`server/`), run with `uv`.
  - PyMuPDF extracts words with bounding boxes, groups them into sentences, and renders page images.
  - Each sentence stores its text and highlight rectangles (page + normalized coords, merged per line).
  - Audio is generated **on demand per sentence** and cached as WAV. The client prefetches the next few sentences, so playback starts in about a second and has no gaps.
- **Frontend:** plain HTML/CSS/JS (`web/`), no build step and no CDN. Pages are shown as server-rendered images with an absolutely positioned highlight overlay.
- **Storage:** `data/books/<id>/` holds `book.pdf`, `meta.json` (title, page sizes, sentences), `progress.json`, `pages/*.png`, `audio/*.wav`.

## TTS engine
- Default: **Kokoro-82M** (`kokoro` package), voice `af_heart`, 24 kHz.
- Engines sit behind one small interface in `server/tts.py` (`synthesize(text) -> (samples, sample_rate)`), chosen with the `TTS_ENGINE` env var.
- **Later:** add Chatterbox v3 (more expressive, voice cloning) and maybe Higgs Audio v3 / VoxCPM2 as alternate engines if Kokoro's voice isn't good enough. Cached audio is stored per engine + voice, so switching never mixes voices.

## API
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/books` | Library list with progress |
| POST | `/api/books` | Upload a PDF |
| DELETE | `/api/books/{id}` | Remove a book |
| GET | `/api/books/{id}` | Metadata: pages, sentences, progress |
| GET | `/api/books/{id}/pages/{n}.png` | Page image |
| GET | `/api/books/{id}/audio/{i}.wav` | Sentence audio (generated if missing) |
| PUT | `/api/books/{id}/progress` | Save `{sentence, offset, speed}` |

## Done when
Uploading a PDF, pressing play, and hearing narration with the highlighted sentence following on the page works end to end; the controls work; and closing and reopening resumes at the same sentence.
