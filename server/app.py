import base64
import binascii
import json
import os
import re
import secrets
import shutil
import time
import uuid
from pathlib import Path

import soundfile as sf
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import pdf, tts

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ROOT / "data" / "books"
BOOKS.mkdir(parents=True, exist_ok=True)

app = FastAPI()
# Only answer to localhost (plus ALLOWED_HOSTS when hosted), so other websites can't reach the API via DNS rebinding.
hosts = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", *hosts])
PASSWORD = os.environ.get("APP_PASSWORD", "")
if hosts and not PASSWORD:
    raise RuntimeError("Set APP_PASSWORD when ALLOWED_HOSTS exposes the app beyond localhost")


@app.middleware("http")
async def require_password(request, call_next):
    """When APP_PASSWORD is set, ask the browser for it (any username)."""
    if PASSWORD:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        try:
            given = base64.b64decode(value).decode().partition(":")[2]
        except (binascii.Error, UnicodeDecodeError):
            given = ""
        if scheme.lower() != "basic" or not secrets.compare_digest(given.encode(), PASSWORD.encode()):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Bookaudifier"'})
    return await call_next(request)


@app.middleware("http")
async def revalidate_web_files(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"  # pick up app updates on reload
    return response


def book_dir(book_id: str) -> Path:
    d = BOOKS / book_id
    if not re.fullmatch(r"[0-9a-f]{12}", book_id) or not (d / "meta.json").exists():
        raise HTTPException(404, "Book not found")
    return d


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def load_meta(d: Path) -> dict:
    meta = read_json(d / "meta.json")
    if meta["sentences"] and "words" not in meta["sentences"][0]:
        # Books added before word highlighting: re-extract to get word boxes.
        fresh = pdf.extract(str(d / "book.pdf"))["sentences"]
        if [s["text"] for s in fresh] == [s["text"] for s in meta["sentences"]]:
            meta["sentences"] = fresh
            (d / "meta.json").write_text(json.dumps(meta))
    return meta


class Progress(BaseModel):
    sentence: int = 0
    offset: float = 0
    speed: float = 1.0
    voice: str = ""


@app.get("/api/books")
def list_books():
    books = []
    for d in BOOKS.iterdir():
        meta = read_json(d / "meta.json")
        if not meta:
            continue
        progress = read_json(d / "progress.json", {})
        at = min(progress.get("sentence", 0), len(meta["sentences"]) - 1)
        books.append({
            "page": meta["sentences"][at]["rects"][0][0] + 1,
            "left_secs": sum(len(s["text"]) for s in meta["sentences"][at:]) / 15,  # ~Kokoro pace at 1x
            "id": d.name,
            "title": meta["title"],
            "pages": len(meta["pages"]),
            "sentences": len(meta["sentences"]),
            "progress": progress.get("sentence", 0),
            "opened": progress.get("updated", meta["added"]),
        })
    return sorted(books, key=lambda b: b["opened"], reverse=True)


@app.post("/api/books")
def upload(file: UploadFile):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")
    book_id = uuid.uuid4().hex[:12]
    d = BOOKS / book_id
    d.mkdir()
    with open(d / "book.pdf", "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        meta = pdf.extract(str(d / "book.pdf"))
    except Exception:
        shutil.rmtree(d)
        raise HTTPException(400, "Could not read this PDF")
    if not meta["sentences"]:
        shutil.rmtree(d)
        raise HTTPException(400, "No text found in this PDF (scanned PDFs aren't supported yet)")
    meta["title"] = meta["title"] or Path(file.filename).stem
    meta["added"] = time.time()
    (d / "meta.json").write_text(json.dumps(meta))
    return {"id": book_id}


@app.delete("/api/books/{book_id}")
def delete(book_id: str):
    shutil.rmtree(book_dir(book_id))
    return {"ok": True}


@app.get("/api/books/{book_id}")
def get_book(book_id: str):
    d = book_dir(book_id)
    meta = load_meta(d)
    for s in meta["sentences"]:
        s.pop("words", None)  # served per sentence with the audio timings instead
    meta["progress"] = read_json(d / "progress.json", Progress().model_dump())
    return meta


@app.put("/api/books/{book_id}/progress")
def save_progress(book_id: str, p: Progress):
    d = book_dir(book_id)
    (d / "progress.json").write_text(json.dumps({**p.model_dump(), "updated": time.time()}))
    return {"ok": True}


@app.get("/api/books/{book_id}/pages/{pno}.png")
def page_image(book_id: str, pno: int):
    d = book_dir(book_id)
    out = d / "pages" / f"{pno}.png"
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        pdf.render_page(str(d / "book.pdf"), pno, str(out))
    return FileResponse(out, headers={"Cache-Control": "max-age=31536000"})


@app.get("/api/voices")
def voices():
    e = tts.engine()
    return {"default": e.voice, "voices": [{"id": k, "name": v} for k, v in e.voices.items()]}


def sentence_audio(book_id: str, idx: int, voice: str | None) -> Path:
    """Generate (once) a sentence's WAV plus a JSON of [start, end, page, x, y, w, h] per spoken word."""
    d = book_dir(book_id)
    sentences = load_meta(d)["sentences"]
    if not 0 <= idx < len(sentences):
        raise HTTPException(404, "No such sentence")
    e = tts.engine()
    voice = voice or e.voice
    if voice not in e.voices:
        raise HTTPException(400, "Unknown voice")
    out = d / "audio" / f"{e.name}-{voice}" / f"{idx}.wav"
    if not out.with_suffix(".json").exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        audio, tokens = tts.synthesize(sentences[idx]["text"], voice)
        words = sentences[idx].get("words", [])
        times = tts.align([w[0] for w in words], tokens)
        timed = [[*t, *w[1:]] for t, w in zip(times, words) if t]
        tmp = out.with_suffix(".tmp.wav")
        sf.write(tmp, audio, e.sample_rate)
        tmp.rename(out)
        out.with_suffix(".json").write_text(json.dumps(timed))
    return out


@app.get("/api/books/{book_id}/audio/{idx}.wav")
def audio_wav(book_id: str, idx: int, voice: str | None = None):
    return FileResponse(sentence_audio(book_id, idx, voice))


@app.get("/api/books/{book_id}/audio/{idx}.json")
def audio_words(book_id: str, idx: int, voice: str | None = None):
    return FileResponse(sentence_audio(book_id, idx, voice).with_suffix(".json"))


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
