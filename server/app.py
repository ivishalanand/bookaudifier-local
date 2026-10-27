import json
import re
import shutil
import time
import uuid
from pathlib import Path

import soundfile as sf
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import pdf, tts

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ROOT / "data" / "books"
BOOKS.mkdir(parents=True, exist_ok=True)

app = FastAPI()
# Only answer to localhost, so other websites can't reach the API via DNS rebinding.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


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


class Progress(BaseModel):
    sentence: int = 0
    offset: float = 0
    speed: float = 1.0


@app.get("/api/books")
def list_books():
    books = []
    for d in BOOKS.iterdir():
        meta = read_json(d / "meta.json")
        if not meta:
            continue
        progress = read_json(d / "progress.json", {})
        books.append({
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
    meta = read_json(d / "meta.json")
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


@app.get("/api/books/{book_id}/audio/{idx}.wav")
def sentence_audio(book_id: str, idx: int):
    d = book_dir(book_id)
    sentences = read_json(d / "meta.json")["sentences"]
    if not 0 <= idx < len(sentences):
        raise HTTPException(404, "No such sentence")
    e = tts.engine()
    out = d / "audio" / f"{e.name}-{e.voice}" / f"{idx}.wav"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.wav")
        sf.write(tmp, tts.synthesize(sentences[idx]["text"]), e.sample_rate)
        tmp.rename(out)
    return FileResponse(out)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
