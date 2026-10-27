"""Turn a PDF into sentences with highlight rectangles."""
import re

import pymupdf as fitz

ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "vs.", "etc.", "e.g.", "i.e.", "fig.", "no.", "vol.", "ch."}
MAX_CHARS = 350
SPEAKABLE = re.compile(r"[A-Za-z]")


def _ends_sentence(word: str) -> bool:
    w = word.rstrip("\"'”’)]")
    return w[-1:] in ".!?" and word.lower() not in ABBREVIATIONS


def extract(path: str) -> dict:
    doc = fitz.open(path)
    pages = [{"w": p.rect.width, "h": p.rect.height} for p in doc]
    sentences, words = [], []  # words: (text, page, block, line, rect)

    def flush():
        if not words:
            return
        text = ""
        for i, (t, *_rest) in enumerate(words):
            if text.endswith("-") and words[i - 1][3] != words[i][3]:
                text = text[:-1] + t  # rejoin a word hyphenated across lines
            else:
                text += (" " if text else "") + t
        if SPEAKABLE.search(text):
            sentences.append({"text": text, "rects": _line_rects(words, pages)})
        words.clear()

    for pno, page in enumerate(doc):
        prev_block = None
        for x0, y0, x1, y1, t, block, line, _ in page.get_text("words", sort=True):
            if prev_block is not None and block != prev_block:
                flush()  # headings and paragraphs end at block boundaries
            prev_block = block
            words.append((t, pno, block, line, (x0, y0, x1, y1)))
            length = sum(len(w[0]) + 1 for w in words)
            if _ends_sentence(t) or (length > MAX_CHARS and t.endswith((",", ";", ":"))) or length > MAX_CHARS * 1.5:
                flush()
        flush()

    return {"title": (doc.metadata or {}).get("title") or "", "pages": pages, "sentences": _drop_repeats(sentences, len(pages))}


def _drop_repeats(sentences, n_pages):
    """Drop running headers, footers and watermarks: short lines repeated on many pages."""
    key = lambda s: re.sub(r"\d+", "", s["text"]).strip().lower()
    seen = {}
    for s in sentences:
        if len(s["text"]) < 80:
            seen.setdefault(key(s), set()).add(s["rects"][0][0])
    limit = max(3, n_pages // 4)
    return [s for s in sentences if len(seen.get(key(s), ())) < limit]


def _line_rects(words, pages):
    """Merge word boxes on the same line into one box, normalized to 0..1 of the page."""
    lines = {}
    for _, pno, block, line, (x0, y0, x1, y1) in words:
        r = lines.setdefault((pno, block, line), [x0, y0, x1, y1])
        r[0], r[1], r[2], r[3] = min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)
    out = []
    for (pno, _, _), (x0, y0, x1, y1) in lines.items():
        w, h = pages[pno]["w"], pages[pno]["h"]
        out.append([pno, round(x0 / w, 4), round(y0 / h, 4), round((x1 - x0) / w, 4), round((y1 - y0) / h, 4)])
    return out


def render_page(path: str, pno: int, out: str, zoom: float = 2.0):
    doc = fitz.open(path)
    doc[pno].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(out)
