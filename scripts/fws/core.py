"""Shared HTTP layer, on-disk cache, and text/ranking helpers.

Everything here is keyless: plain HTTPS to public endpoints with browser-like
headers, polite per-host pacing, retries with backoff, and a local cache so
repeated agent queries do not hammer the same engines.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------------
# console: this machine's default codepage is not UTF-8; results contain
# arbitrary unicode, so force UTF-8 on stdout/stderr.
# ---------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

CACHE_DIR = os.environ.get(
    "FWS_CACHE_DIR",
    os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                 "free-web-search", "cache"),
)
DEFAULT_TTL = int(os.environ.get("FWS_CACHE_TTL", "900"))
NO_CACHE = os.environ.get("FWS_NO_CACHE") == "1"

RETRY_STATUS = (202, 403, 408, 425, 429, 500, 502, 503, 504)


class FetchError(Exception):
    """Raised when a URL could not be fetched (after retries)."""

    def __init__(self, url: str, status: int | None, reason: str):
        super().__init__(f"{reason} (status={status}) for {url}")
        self.url = url
        self.status = status
        self.reason = reason


class Http:
    """requests.Session wrapper with pacing, retries, UA rotation and caching."""

    _host_lock = threading.Lock()
    _last_hit: dict[str, float] = {}

    def __init__(self, timeout: float = 20.0, attempts: int = 3):
        self.timeout = timeout
        self.default_attempts = attempts
        self.session = requests.Session()
        self.session.headers.update(BASE_HEADERS)
        self.session.headers["User-Agent"] = random.choice(UA_POOL)

    # -- cache -------------------------------------------------------------
    @staticmethod
    def _cache_key(method: str, url: str, body) -> str:
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        raw = f"{method}|{url}|{body or ''}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str, ttl: int) -> dict | None:
        if NO_CACHE or ttl <= 0:
            return None
        path = os.path.join(CACHE_DIR, key[:2], key + ".json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
            if time.time() - blob.get("ts", 0) > ttl:
                return None
            return blob
        except Exception:
            return None

    def _cache_put(self, key: str, blob: dict) -> None:
        if NO_CACHE:
            return
        try:
            path = os.path.join(CACHE_DIR, key[:2], key + ".json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(blob, fh)
            os.replace(tmp, path)
        except Exception:
            pass

    # -- pacing ------------------------------------------------------------
    @staticmethod
    def _pace(host: str, min_interval: float) -> None:
        if min_interval <= 0:
            return
        with Http._host_lock:
            now = time.time()
            wait = Http._last_hit.get(host, 0.0) + min_interval - now
            if wait > 0:
                time.sleep(min(wait, 8.0))
            Http._last_hit[host] = time.time()

    # -- fetch -------------------------------------------------------------
    def fetch(self, url: str, *, method: str = "GET", params: dict | None = None,
              data: dict | None = None, json: dict | None = None,
              headers: dict | None = None,
              timeout: float | None = None, ttl: int | None = None,
              min_interval: float = 0.6, allow_redirects: bool = True,
              binary: bool = False, attempts: int | None = None) -> dict:
        """Return {status, url, text, content_type, headers, from_cache}."""
        ttl = DEFAULT_TTL if ttl is None else ttl
        prepared = requests.Request(method, url, params=params, data=data, json=json,
                                    headers=headers or {}).prepare()
        full_url = prepared.url or url
        key = self._cache_key(method, full_url, prepared.body)
        cached = self._cache_get(key, ttl)
        if cached is not None:
            cached["from_cache"] = True
            return cached

        host = urllib.parse.urlsplit(full_url).netloc
        last_status: int | None = None
        last_reason = "error"
        max_attempts = int(attempts or self.default_attempts)
        for attempt in range(max_attempts):
            self._pace(host, min_interval * (attempt + 1))
            hdrs = dict(BASE_HEADERS)
            hdrs["User-Agent"] = UA_POOL[(attempt + hash(full_url)) % len(UA_POOL)]
            hdrs.update(headers or {})
            if prepared.headers.get("Content-Type"):
                hdrs["Content-Type"] = prepared.headers["Content-Type"]
            try:
                resp = self.session.request(
                    method, full_url, headers=hdrs, data=prepared.body,
                    timeout=timeout or self.timeout,
                    allow_redirects=allow_redirects, stream=binary,
                )
            except requests.exceptions.Timeout:
                last_reason = "timeout"
                time.sleep(1.0 + attempt * 2 + random.random())
                continue
            except requests.exceptions.SSLError:
                last_reason = "ssl error"
                time.sleep(0.5)
                continue
            except requests.exceptions.RequestException as exc:
                last_reason = f"{type(exc).__name__}"
                time.sleep(0.5 + attempt)
                continue

            last_status = resp.status_code
            if resp.status_code in RETRY_STATUS and attempt < max_attempts - 1:
                delay = min(2.0 * (2 ** attempt) + random.random() * 1.5, 12.0)
                time.sleep(delay)
                continue
            if binary:
                body = resp.content
            else:
                if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
                    resp.encoding = resp.apparent_encoding or "utf-8"
                body = resp.text

            blob = {
                "status": resp.status_code,
                "url": resp.url,
                "text": body,
                "content_type": resp.headers.get("Content-Type", ""),
                "headers": {k.lower(): v for k, v in resp.headers.items()},
                "ts": time.time(),
                "from_cache": False,
            }
            if resp.status_code >= 400:
                raise FetchError(full_url, resp.status_code, f"HTTP {resp.status_code}")
            self._cache_put(key, blob)
            return blob

        raise FetchError(full_url, last_status, last_reason)

    def get(self, url: str, **kw) -> dict:
        return self.fetch(url, **kw)

    def post(self, url: str, **kw) -> dict:
        return self.fetch(url, method="POST", **kw)


# ---------------------------------------------------------------------------
# text helpers: tokenising, relevance scoring, chunking, date parsing
# ---------------------------------------------------------------------------
STOPWORDS = set("""a an the and or but if then than that this these those of to in on for with
without at by from as is are was were be been being it its it's do does did not no so such can
could will would should may might must have has had you your yours we our they their he she his
her what which who whom when where why how all any both each few more most other some too very
i me my myself we us our about above below up down out off over under again further once here
there all any both each few other same also main best top latest new please tell me get gets
want need vs versus between give""".split())

_TOKEN_RE = re.compile(r"[a-z0-9à-ÿ']{2,}")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in STOPWORDS]


def key_terms(text: str, limit: int = 6) -> list[str]:
    """Content words of a phrase, in order — the seeds of a refined query."""
    out, seen = [], set()
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9+.\-_]{2,}", text or ""):
        low = tok.lower()
        if low in STOPWORDS or low in seen:
            continue
        seen.add(low)
        out.append(low)
    return out[:limit]


def chunk_text(text: str, size: int = 1100, overlap: int = 150) -> list[str]:
    """Split into paragraph-aligned chunks of roughly `size` characters."""
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not text:
        return []
    paras, chunks, cur = text.split("\n\n"), [], ""
    for p in paras:
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}".strip()
        else:
            if cur:
                chunks.append(cur)
            while len(p) > size:
                head, p = p[:size], p[size + overlap:]
                chunks.append(head.strip())
            cur = p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if len(c) > 40] or ([text] if text else [])


def rerank_chunks(chunks: list[str], query: str, keep: int = 3) -> list[str]:
    """Lexical relevance rerank of extracted chunks against a query."""
    qtok = set(tokenize(query))
    if not qtok or not chunks:
        return chunks[:keep] if keep else chunks
    scored = []
    for i, c in enumerate(chunks):
        ctok = tokenize(c)
        if not ctok:
            continue
        freq = {}
        for t in ctok:
            freq[t] = freq.get(t, 0) + 1
        s = sum(1 + math.log(freq[t]) for t in qtok if t in freq)
        s *= 1.0 + 0.35 * (len(set(ctok) & qtok) / len(qtok))
        scored.append((s, -i, c))
    scored.sort(reverse=True)
    picked = [c for _, _, c in scored[:keep]]
    order = {c: i for i, c in enumerate(chunks)}
    return sorted(picked, key=lambda c: order.get(c, 0))


def relevance(query: str, title: str, snippet: str, url: str) -> float:
    """0..1 relevance score used to rank merged engine results."""
    qtok = tokenize(query)
    if not qtok:
        return 0.0
    ttok, stok = tokenize(title), tokenize(snippet)
    utok = tokenize(urllib.parse.urlsplit(url).path.replace("/", " ").replace("-", " "))
    num = den = 0.0
    for t in set(qtok):
        w = 1.0 + math.log(1 + qtok.count(t))
        hit = 0.0
        if t in ttok:
            hit = max(hit, 1.0)
        if t in utok:
            hit = max(hit, 0.8)
        if t in stok:
            hit = max(hit, 0.55)
        num += w * hit
        den += w
    base = num / den if den else 0.0
    cover = len(set(qtok) & (set(ttok) | set(stok) | set(utok))) / len(set(qtok))
    return round(min(1.0, 0.72 * base + 0.28 * cover), 4)


DATE_PATTERNS = [
    re.compile(r"\b([A-Z][a-z]{2,8})\s+(\d{1,2}),?\s+(\d{4})\b"),
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})\s+([A-Z][a-z]{2,8})\s+(\d{4})\b"),
    re.compile(r"\b(\d{4})/(\d{2})/(\d{2})\b"),
]
MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})


def parse_date(text: str) -> str | None:
    """Best-effort YYYY-MM-DD extraction from snippet/URL text."""
    if not text:
        return None
    for pat in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        g = m.groups()
        if len(g[0]) == 4:
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
        elif g[1].isdigit():
            mo = MONTHS.get(g[1].lower()[:9]) or MONTHS.get(g[1].lower()[:3])
            if not mo:
                continue
            y, d, mo = int(g[2]), int(g[0]), mo
        else:
            mo = MONTHS.get(g[0].lower()[:9]) or MONTHS.get(g[0].lower()[:3])
            if not mo:
                continue
            d, y = int(g[1]), int(g[2])
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            continue
    rel = re.search(r"(\d+)\s*(minute|hour|day|week|month|year)s?\s+ago", text, re.I)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2).lower()
        delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n),
                 "day": timedelta(days=n), "week": timedelta(weeks=n),
                 "month": timedelta(days=30 * n), "year": timedelta(days=365 * n)}[unit]
        return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%d")
    return None


def host_of(url: str) -> str:
    h = urllib.parse.urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def domain_match(url: str, domain: str) -> bool:
    h, d = host_of(url), (domain or "").lower().strip().lstrip(".")
    if not d:
        return False
    return h == d or h.endswith("." + d)


def absolutize(base: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return None
    out = urllib.parse.urljoin(base, href)
    parts = urllib.parse.urlsplit(out)
    if parts.scheme not in ("http", "https"):
        return None
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path or "/",
                                    parts.query, ""))


def norm_url(url: str) -> str:
    """Canonical form used for de-duplication."""
    p = urllib.parse.urlsplit(url)
    path = re.sub(r"/+$", "", p.path or "/")
    frag = ""
    if "#" in path:
        path, frag = path.split("#", 1)
    return urllib.parse.urlunsplit((p.scheme.lower(), host_of(url), path,
                                    urllib.parse.urlparse(p.query).query[:120], frag))


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
