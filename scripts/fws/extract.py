"""URL content extraction, keyless.

Primary path: fetch with browser headers -> trafilatura main-content extraction.
Fallbacks: readability-ish BeautifulSoup + markdownify, then (advanced depth)
AMP variants, alternate user agents, and the Internet Archive snapshot.
"""
from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from .core import (FetchError, Http, absolutize, chunk_text, host_of, rerank_chunks,
                   strip_tags, truncate)

BLOCKED_MARKERS = (
    "just a moment", "enable javascript", "access denied", "captcha",
    "are you a robot", "unusual traffic", "blocked", "403 forbidden",
    "verify you are human", "please turn javascript on",
)

ALT_UAS = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
]


def _looks_blocked(html: str) -> bool:
    low = (html or "")[:4000].lower()
    return any(m in low for m in BLOCKED_MARKERS) and len(html) < 60000


def _trafilatura_extract(html: str, url: str, fmt: str, include_images: bool):
    try:
        import trafilatura
    except ImportError:
        return None, {}
    out_fmt = "markdown" if fmt == "markdown" else "txt"
    try:
        content = trafilatura.extract(
            html, url=url, output_format=out_fmt, include_links=False,
            include_tables=True, include_images=include_images, include_comments=False,
            favor_recall=True, include_description=False,
        )
    except TypeError:  # older trafilatura signatures
        content = trafilatura.extract(html, url=url, output_format=out_fmt,
                                      include_links=False, include_tables=True)
    except Exception:
        content = None
    meta = {}
    try:
        m = trafilatura.extract_metadata(html)
        if m:
            meta = {"title": m.title or "", "author": m.author or "",
                    "date": m.date or "", "sitename": m.sitename or ""}
    except Exception:
        pass
    return (content or "").strip(), meta


def _bs4_extract(html: str, fmt: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og = soup.select_one('meta[property="og:title"]')
    if not title and og:
        title = og.get("content", "").strip()
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "button"]):
        tag.decompose()
    for sel in ("nav", "footer", "header", "aside", "[role=navigation]", "[role=banner]",
                "[role=contentinfo]", ".sidebar", ".comments", ".related", ".breadcrumb",
                ".cookie", ".consent", ".advert", ".ads"):
        for el in soup.select(sel):
            el.decompose()
    main = (soup.select_one("article") or soup.select_one("main")
            or soup.select_one("[role=main]") or soup.select_one("#content")
            or soup.select_one(".post") or soup.body or soup)
    if fmt == "markdown":
        try:
            from markdownify import markdownify as md
            text = md(str(main), heading_style="ATX", bullets="-", strip=["a"])
        except Exception:
            text = main.get_text("\n", strip=True)
    else:
        text = main.get_text("\n", strip=True)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, title


def _pdf_text(data: bytes) -> str:
    try:
        import pypdf
    except ImportError:
        return ""
    import io
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages[:40]).strip()
    except Exception:
        return ""


def _wayback(http: Http, url: str) -> str | None:
    try:
        r = http.get("https://archive.org/wayback/available", params={"url": url},
                     ttl=86400, min_interval=0.5, attempts=2)
        import json
        snap = json.loads(r["text"]).get("archived_snapshots", {}).get("closest", {})
        return snap.get("url") if snap.get("available") else None
    except Exception:
        return None


def _amp_variants(url: str) -> list[str]:
    p = urllib.parse.urlsplit(url)
    base = urllib.parse.urlunsplit((p.scheme, p.netloc, re.sub(r"/$", "", p.path), "", ""))
    out = [base + "/amp", base + "/amp/", base + "?amp", base + "?output=amp",
           base + "?amp=1", base + "/amp.html"]
    return out


def collect_images(html: str, base_url: str, limit: int = 12) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    seen, out = set(), []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if src.startswith("//"):
            src = "https:" + src
        absu = absolutize(base_url, src)
        if not absu or absu in seen or absu.startswith("data:"):
            continue
        if re.search(r"(pixel|spacer|1x1|blank|logo|icon|sprite|gravatar)", absu, re.I):
            continue
        seen.add(absu)
        out.append(absu)
        if len(out) >= limit:
            break
    return out


def extract_one(http: Http, url: str, *, fmt: str = "markdown", depth: str = "basic",
                include_images: bool = False, query: str | None = None,
                max_chars: int = 0) -> dict | None:
    """Return {title,url,content,content_type,images?,favicon?,meta} or None."""
    tried: list[tuple[str, str]] = []
    best: dict | None = None

    def attempt(target: str, headers: dict | None = None, ttl: int = 3600) -> None:
        nonlocal best
        try:
            r = http.get(target, headers=headers, ttl=ttl, min_interval=0.4, attempts=2)
        except FetchError as exc:
            tried.append((target, exc.reason))
            return
        except Exception as exc:  # noqa: BLE001
            tried.append((target, type(exc).__name__))
            return
        ctype = (r.get("content_type") or "").lower()
        if "pdf" in ctype or target.lower().endswith(".pdf"):
            try:
                raw = http.fetch(target, binary=True, ttl=3600, min_interval=0.4, attempts=2)
                text = _pdf_text(raw["text"])
            except Exception:
                text = ""
            built = {"title": urllib.parse.urlsplit(target).path.rsplit("/", 1)[-1],
                     "url": url, "content": text, "content_type": "application/pdf"}
            if not best or len(text) > len(best["content"]):
                best = built
            return
        html = r["text"]
        if not html or _looks_blocked(html):
            tried.append((target, "blocked/empty"))
            return
        content, meta = _trafilatura_extract(html, target, fmt, include_images)
        if not content or len(content) < 200:
            bs_text, bs_title = _bs4_extract(html, fmt)
            if len(bs_text) > len(content):
                content = bs_text
                meta.setdefault("title", bs_title)
        built = {"title": meta.get("title") or _title_of(html) or host_of(target),
                 "url": url, "content": content.strip(),
                 "content_type": ctype or "text/html",
                 "meta": meta, "_html": html, "_final_url": r.get("url") or target}
        if not best or len(built["content"]) > len(best["content"]):
            best = built

    attempt(url)
    if depth == "advanced" and (not best or len(best.get("content", "")) < 400):
        for ua in ALT_UAS:
            attempt(url, headers={"User-Agent": ua}, ttl=600)
            if best and len(best.get("content", "")) >= 400:
                break
        if best and len(best.get("content", "")) < 400:
            for variant in _amp_variants(url)[:4]:
                attempt(variant, ttl=600)
                if best and len(best.get("content", "")) >= 400:
                    break
        if best and len(best.get("content", "")) < 400:
            if snap := _wayback(http, url):
                attempt(snap, ttl=86400)

    if not best or not best.get("content"):
        return None

    html_for_images = best.pop("_html", "") or ""
    best.pop("_html", None)
    final_url = best.pop("_final_url", url)

    if query:
        keep = 4 if depth == "advanced" else 3
        chunks = chunk_text(best["content"], size=1200)
        best["content"] = "\n\n".join(rerank_chunks(chunks, query, keep=keep))
    if max_chars:
        best["content"] = truncate(best["content"], max_chars)
    if include_images:
        best["images"] = collect_images(html_for_images, final_url)
    return best


def _title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    if m:
        return strip_tags(m.group(1))[:200]
    m = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html or "", re.I)
    return strip_tags(m.group(1))[:200] if m else ""


def extract_urls(http: Http, urls: list[str], *, fmt: str = "markdown", depth: str = "basic",
                 include_images: bool = False, include_favicon: bool = False,
                 query: str | None = None, max_chars: int = 0, log=None) -> list[dict]:
    from .engines import favicon_url
    log = log or (lambda *a: None)
    out = []
    for u in urls:
        got = extract_one(http, u, fmt=fmt, depth=depth, include_images=include_images,
                          query=query, max_chars=max_chars)
        if got:
            if include_favicon:
                got["favicon"] = favicon_url(got["url"])
            out.append(got)
        else:
            log(f"extract: failed for {u}")
            out.append({"title": "", "url": u, "content": "", "content_type": "",
                        "failed": True})
    return out
