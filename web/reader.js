const id = new URLSearchParams(location.search).get("id");
if (!/^[0-9a-f]{12}$/.test(id || "")) { location.replace("/"); throw new Error("Bad book id"); }
const $ = (x) => document.getElementById(x);
const audio = new Audio();
const CHARS_PER_SEC = 15; // rough Kokoro pace, corrected as real clips arrive

let sentences = [], pages = [], byPage = [];
let cur = 0;         // sentence being read
let loaded = -1;     // sentence currently in the <audio> element
let pendingOffset = 0;
let playing = false; // what the user wants, even while a clip is loading
let speed = 1;
let token = 0;       // cancels stale loads when the user jumps around
let lastUserScroll = 0, lastSave = 0, scrubbing = false;

// ---- audio clips ----
const clips = new Map(); // idx -> Promise<objectURL>
const known = new Map(); // idx -> real duration (s)
const timings = new Map(); // idx -> [[start, end, page, x, y, w, h], ...] per spoken word
let knownSecs = 0, knownChars = 0;

const estDur = (i) => known.get(i) ?? (sentences[i].text.length / CHARS_PER_SEC) * (knownSecs ? knownSecs / (knownChars / CHARS_PER_SEC) : 1);

async function wavDuration(blob) {
  const v = new DataView(await blob.slice(0, 44).arrayBuffer());
  return (blob.size - 44) / v.getUint32(28, true); // bytes / byteRate
}

function clip(i) {
  if (!clips.has(i)) {
    const p = fetch(`/api/books/${id}/audio/${i}.wav`)
      .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.blob(); })
      .then(async (b) => {
        if (!known.has(i)) {
          const d = await wavDuration(b);
          known.set(i, d); knownSecs += d; knownChars += sentences[i].text.length;
        }
        if (!timings.has(i)) {
          timings.set(i, await fetch(`/api/books/${id}/audio/${i}.json`).then((r) => (r.ok ? r.json() : [])).catch(() => []));
        }
        return URL.createObjectURL(b);
      });
    p.catch(() => clips.delete(i));
    clips.set(i, p);
    evict();
  }
  return clips.get(i);
}

function evict() {
  if (clips.size < 40) return;
  for (const [i, p] of clips) {
    if (Math.abs(i - cur) > 20) { p.then(URL.revokeObjectURL, () => {}); clips.delete(i); }
  }
}

async function prefetch(from) {
  for (let i = from + 1; i <= from + 3 && i < sentences.length; i++) {
    if (Math.abs(cur - from) > 3) return;
    await clip(i).catch(() => {});
  }
}

// ---- playback ----
const curTime = () => (loaded === cur ? audio.currentTime : pendingOffset);

async function go(i, offset = 0) {
  cur = Math.max(0, Math.min(sentences.length - 1, i));
  pendingOffset = offset;
  const my = ++token;
  highlight(cur);
  save(true);
  updateUI();
  if (!playing) { audio.pause(); loaded = -1; return; }
  $("player").classList.add("loading");
  try {
    const url = await clip(cur);
    if (my !== token) return;
    audio.src = url;
    loaded = cur;
    audio.defaultPlaybackRate = audio.playbackRate = speed;
    await new Promise((r) => audio.addEventListener("loadedmetadata", r, { once: true }));
    if (my !== token) return;
    audio.currentTime = Math.min(offset, audio.duration || offset);
    await audio.play();
    prefetch(cur);
  } catch (e) {
    if (my !== token) return;
    playing = false;
    $("where").textContent = "Couldn't generate audio for this sentence";
  } finally {
    if (my === token) $("player").classList.remove("loading");
    updateUI();
  }
}

function toggle() {
  if (playing) { playing = false; audio.pause(); save(true); }
  else if (loaded === cur) { playing = true; audio.play(); }
  else { playing = true; go(cur, pendingOffset); }
  if (playing) follow(true);
  updateUI();
}

function seekBy(delta) {
  let i = cur, t = curTime() + delta;
  while (t < 0 && i > 0) { i--; t += estDur(i); }
  while (t > estDur(i) && i < sentences.length - 1) { t -= estDur(i); i++; }
  t = Math.max(0, t);
  if (i === cur && loaded === cur) { audio.currentTime = t; save(true); updateUI(); }
  else go(i, t);
}

audio.addEventListener("ended", () => {
  if (cur < sentences.length - 1) go(cur + 1);
  else { playing = false; updateUI(); }
});
audio.addEventListener("timeupdate", () => { updateUI(); save(); follow(); });

// ---- pages and highlight ----
function renderPages() {
  const root = $("pages");
  pages.forEach((p, n) => {
    const el = document.createElement("div");
    el.className = "page";
    el.style.aspectRatio = `${p.w} / ${p.h}`;
    const img = new Image();
    Object.assign(img, { loading: "lazy", alt: `Page ${n + 1}`, src: `/api/books/${id}/pages/${n}.png` });
    el.append(img);
    el.onclick = (e) => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      const hit = byPage[n].find(([, [, rx, ry, rw, rh]]) => x >= rx - 0.005 && x <= rx + rw + 0.005 && y >= ry - 0.004 && y <= ry + rh + 0.004);
      if (hit) { playing = true; go(hit[0]); }
    };
    root.append(el);
  });
}

function highlight(i, instant = false) {
  document.querySelectorAll(".hl").forEach((el) => el.remove());
  const pageEls = $("pages").children;
  let first;
  for (const [p, x, y, w, h] of sentences[i].rects) {
    const el = document.createElement("div");
    el.className = "hl";
    Object.assign(el.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
    pageEls[p].append(el);
  }
  follow(instant);
}

// Mark the word being spoken, checked every frame since timeupdate is too coarse.
const wordEl = Object.assign(document.createElement("div"), { className: "hlw" });
let wordKey = "";
function trackWord() {
  requestAnimationFrame(trackWord);
  const words = loaded === cur ? timings.get(cur) : null;
  const t = audio.currentTime;
  let k = -1;
  if (words) for (let j = 0; j < words.length && words[j][0] <= t; j++) k = j;
  const key = `${cur}:${k}`;
  if (key === wordKey) return;
  wordKey = key;
  if (k < 0) { wordEl.remove(); return; }
  const [, , p, x, y, w, h] = words[k];
  Object.assign(wordEl.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
  $("pages").children[p].append(wordEl);
}
requestAnimationFrame(trackWord);

// Keep the highlight in view; a manual scroll pauses this for 3 s.
function follow(force = false) {
  const first = document.querySelector(".hl");
  if (!first || (!force && Date.now() - lastUserScroll < 3000)) return;
  const r = first.getBoundingClientRect();
  if (force || r.top < 90 || r.bottom > innerHeight - 180) {
    first.scrollIntoView({ block: "center", behavior: Math.abs(r.top) > 3 * innerHeight ? "auto" : "smooth" });
  }
}
["wheel", "touchmove", "mousedown", "keydown"].forEach((ev) => addEventListener(ev, () => (lastUserScroll = Date.now()), { passive: true }));

// ---- UI ----
const fmt = (s) => {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
};

function updateUI() {
  let start = 0, total = 0;
  for (let j = 0; j < sentences.length; j++) { const d = estDur(j); if (j < cur) start += d; total += d; }
  $("elapsed").textContent = fmt((start + curTime()) / speed);
  $("total").textContent = fmt(total / speed);
  if (!scrubbing) $("seek").value = cur;
  if (!$("player").classList.contains("loading") || !playing) {
    $("where").textContent = `Page ${sentences[cur].rects[0][0] + 1} of ${pages.length}`;
  }
  $("playIcon").innerHTML = playing ? '<path d="M6 5h4v14H6zm8 0h4v14h-4z"/>' : '<path d="M8 5v14l11-7z"/>';
}

function save(force = false) {
  if (!force && Date.now() - lastSave < 3000) return;
  lastSave = Date.now();
  fetch(`/api/books/${id}/progress`, {
    method: "PUT",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sentence: cur, offset: curTime(), speed }),
  });
}

$("play").onclick = toggle;
$("back").onclick = () => seekBy(-10);
$("fwd").onclick = () => seekBy(10);
$("prev").onclick = () => go(curTime() > 2 ? cur : cur - 1);
$("next").onclick = () => go(cur + 1);
$("speed").onchange = (e) => {
  speed = Number(e.target.value);
  audio.defaultPlaybackRate = audio.playbackRate = speed;
  save(true); updateUI();
};
$("seek").oninput = (e) => {
  scrubbing = true;
  $("where").textContent = `Page ${sentences[e.target.value].rects[0][0] + 1} of ${pages.length}`;
};
$("seek").onchange = (e) => { scrubbing = false; go(Number(e.target.value)); };

addEventListener("keydown", (e) => {
  if (["INPUT", "SELECT"].includes(e.target.tagName)) return;
  if (e.code === "Space") { e.preventDefault(); toggle(); }
  else if (e.code === "ArrowLeft") seekBy(-10);
  else if (e.code === "ArrowRight") seekBy(10);
});
addEventListener("pagehide", () => save(true));

if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", toggle);
  ms.setActionHandler("pause", toggle);
  ms.setActionHandler("seekbackward", () => seekBy(-10));
  ms.setActionHandler("seekforward", () => seekBy(10));
  ms.setActionHandler("previoustrack", () => go(cur - 1));
  ms.setActionHandler("nexttrack", () => go(cur + 1));
}

// ---- boot ----
(async () => {
  const res = await fetch(`/api/books/${id}`);
  if (!res.ok) { location.href = "/"; return; }
  const book = await res.json();
  ({ sentences, pages } = book);
  document.title = $("title").textContent = book.title;
  if ("mediaSession" in navigator) navigator.mediaSession.metadata = new MediaMetadata({ title: book.title });
  byPage = pages.map(() => []);
  sentences.forEach((s, i) => s.rects.forEach((r) => byPage[r[0]].push([i, r])));
  $("seek").max = sentences.length - 1;
  speed = book.progress.speed || 1;
  $("speed").value = String(speed);
  renderPages();
  cur = Math.min(book.progress.sentence || 0, sentences.length - 1);
  pendingOffset = book.progress.offset || 0;
  highlight(cur, true);
  updateUI();
  clip(cur).catch(() => {}); // warm up so play starts instantly
})();
