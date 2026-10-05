"""Keyless search engines: adapters, query building, merge + ranking.

Adapters are ordered by quality; the aggregator stops as soon as it has a
healthy result pool and falls through on rate limits (DDG 202, Brave 429, ...).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from .core import (CACHE_DIR, FetchError, Http, domain_match, host_of, key_terms,
                   norm_url, parse_date, relevance, strip_tags, tokenize)

# ---------------------------------------------------------------------------
# option normalisation
# ---------------------------------------------------------------------------
TIME_RANGE_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
DDG_KL = {
    "united states": "us-en", "usa": "us-en", "united kingdom": "uk-en", "uk": "uk-en",
    "canada": "ca-en", "australia": "au-en", "ireland": "ie-en", "new zealand": "nz-en",
    "india": "in-en", "singapore": "sg-en", "germany": "de-de", "france": "fr-fr",
    "spain": "es-es", "italy": "it-it", "portugal": "pt-pt", "netherlands": "nl-nl",
    "sweden": "sv-se", "norway": "nb-no", "denmark": "da-dk", "finland": "fi-fi",
    "poland": "pl-pl", "russia": "ru-ru", "turkey": "tr-tr", "brazil": "br-pt",
    "mexico": "mx-es", "argentina": "ar-es", "japan": "jp-jp", "south korea": "kr-ko",
    "china": "cn-zh", "taiwan": "tw-zh", "hong kong": "hk-en", "vietnam": "vn-vi",
    "thailand": "th-th", "malaysia": "my-ms", "indonesia": "id-id", "philippines": "ph-en",
    "south africa": "za-en", "nigeria": "ng-en", "kenya": "ke-en", "egypt": "eg-ar",
    "saudi arabia": "sa-ar", "united arab emirates": "ae-ar", "israel": "il-he",
    "switzerland": "ch-de", "austria": "at-de", "belgium": "be-fr", "czech republic": "cz-cs",
    "greece": "gr-el", "romania": "ro-ro", "hungary": "hu-hu", "ukraine": "ua-uk",
    "chile": "cl-es", "colombia": "co-es", "peru": "pe-es", "denmark ": "da-dk",
}
BING_CC = {
    "united states": "US", "united kingdom": "GB", "canada": "CA", "australia": "AU",
    "india": "IN", "germany": "DE", "france": "FR", "spain": "ES", "italy": "IT",
    "netherlands": "NL", "sweden": "SE", "japan": "JP", "south korea": "KR", "china": "CN",
    "vietnam": "VN", "thailand": "TH", "singapore": "SG", "brazil": "BR", "mexico": "MX",
    "poland": "PL", "russia": "RU", "turkey": "TR", "switzerland": "CH", "austria": "AT",
}
NEWS_HOSTS = {
    "bbc.com", "bbc.co.uk", "cnn.com", "nytimes.com", "washingtonpost.com", "theguardian.com",
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com", "npr.org",
    "aljazeera.com", "france24.com", "dw.com", "straitstimes.com", "bangkokpost.com",
    "thairath.co.th", "bloomberg.co.jp", "theverge.com", "wired.com", "arstechnica.com",
    "techcrunch.com", "theverge.co.uk", "independent.co.uk", "telegraph.co.uk",
    "news.google.com", "time.com", "newsweek.com", "politico.com", "axios.com",
}


LEAD_IN_RE = re.compile(r"^(?:what(?:'s)?|how|why|who|whom|whose|when|where|which|do|does|did|"
                        r"is|are|was|were|can|could|should|would|will|shall|may|please|"
                        r"explain|define|describe|tell me|give me)\b[\s,]+", re.I)


def normalize_query(q: str) -> str:
    """Drop interrogative lead-ins: keyless SERPs match on 'what', not the topic."""
    out = (q or "").strip().rstrip("?").strip()
    for _ in range(3):
        nxt = LEAD_IN_RE.sub("", out).strip()
        if not nxt or nxt.lower() == out.lower():
            break
        out = nxt
    return out


class SearchOpts:
    """The full search parameter surface."""

    def __init__(self, query: str, *, search_depth: str = "basic", topic: str = "general",
                 time_range: str | None = None, start_date: str = "", end_date: str = "",
                 max_results: int = 5, include_images: bool = False,
                 include_image_descriptions: bool = False, include_raw_content: bool = False,
                 include_domains: list[str] | None = None,
                 exclude_domains: list[str] | None = None, country: str = "",
                 include_favicon: bool = False, exact_match: bool = False):
        self.query = query.strip()
        self.search_depth = (search_depth or "basic").lower()
        self.topic = (topic or "general").lower()
        # An explicit date window overrides the coarse time_range.
        if start_date or end_date:
            time_range = None
        self.time_range = (time_range or "").lower() or None
        self.start_date, self.end_date = start_date or "", end_date or ""
        self.max_results = max(5, min(int(max_results), 20))
        self.include_images = include_images
        self.include_image_descriptions = include_image_descriptions
        self.include_raw_content = include_raw_content
        self.include_domains = [d.strip() for d in (include_domains or []) if d.strip()]
        self.exclude_domains = [d.strip() for d in (exclude_domains or []) if d.strip()]
        self.country = (country or "").strip()
        self.include_favicon = include_favicon
        self.exact_match = exact_match

    # -- derived helpers ---------------------------------------------------
    def window(self) -> tuple[str | None, str | None]:
        start, end = self.start_date or None, self.end_date or None
        if self.time_range:
            days = TIME_RANGE_DAYS.get(self.time_range, 30)
            start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return start, end

    def date_window_active(self) -> bool:
        return bool(self.window()[0] or self.window()[1])

    def news_bias(self) -> bool:
        if self.topic in ("news",):
            return True
        if self.time_range in ("day", "week"):
            return True
        return False

    def kl(self) -> str:
        return DDG_KL.get(self.country.lower(), "us-en" if not self.country else "")

    def cc(self) -> str:
        return BING_CC.get(self.country.lower(), "")

    def build_query(self, *, site_operator: bool = True) -> str:
        q = normalize_query(self.query)
        if self.exact_match:
            q = f'"{q}"'
        if site_operator and self.include_domains:
            sites = " OR ".join(f"site:{d}" for d in self.include_domains[:10])
            q = f"{q} ({sites})" if len(self.include_domains) > 1 else f"{q} site:{self.include_domains[0]}"
        if self.exclude_domains:
            q += " " + " ".join(f"-site:{d}" for d in self.exclude_domains[:5])
        return q


def _ddg_df(opts: SearchOpts) -> str:
    return {"day": "d", "week": "w", "month": "m", "year": "y"}.get(opts.time_range or "", "")


def _bing_filter(opts: SearchOpts) -> str:
    return {"day": 'ex1:"ez1"', "week": 'ex1:"ez2"', "month": 'ex1:"ez3"',
            "year": 'ex1:"ez4"'}.get(opts.time_range or "", "")


# ---------------------------------------------------------------------------
# engine adapters — each returns a list of raw dicts
# ---------------------------------------------------------------------------
def eng_ddg_html(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    data = {"q": opts.build_query(), "kl": opts.kl() or "us-en"}
    if df := _ddg_df(opts):
        data["df"] = df
    r = http.post("https://html.duckduckgo.com/html/", data=data, ttl=600,
                  min_interval=1.5, attempts=1, timeout=9.0)
    soup = BeautifulSoup(r["text"], "lxml")
    out = []
    for blk in soup.select("div.result")[:pool]:
        a = blk.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=")[-1].split("&")[0])
        if not href.startswith("http"):
            continue
        sn = blk.select_one(".result__snippet")
        text = sn.get_text(" ", strip=True) if sn else ""
        out.append({"title": strip_tags(a.get_text(" ", strip=True)), "url": href,
                    "content": text, "engine": "duckduckgo"})
    return out


def _bing_decode(href: str) -> str:
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if m:
        b = m.group(1)
        b += "=" * (-len(b) % 4)
        try:
            dec = base64.b64decode(b).decode("utf-8", "replace")
            if dec.startswith("http"):
                return dec
        except Exception:
            pass
    return href


def _bing_news_url(href: str) -> str:
    """Bing News RSS wraps targets in /news/apiclick.aspx?...&url=<encoded>."""
    if "apiclick" in href or "/ck/a" in href:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        for key in ("url", "u"):
            val = (qs.get(key) or [""])[0]
            if val.startswith("http"):
                return val
            if key == "u" and val.startswith("a1"):
                b = val[2:] + "=" * (-len(val[2:]) % 4)
                try:
                    dec = base64.b64decode(b).decode("utf-8", "replace")
                    if dec.startswith("http"):
                        return dec
                except Exception:
                    pass
    return href


def eng_bing_web(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    # Bing on this network ignores `site:` and degrades the SERP, so keep the raw query.
    params = {"q": opts.build_query(site_operator=False), "count": "30", "setlang": "en"}
    if cc := opts.cc():
        params["cc"] = cc
    if flt := _bing_filter(opts):
        params["filters"] = flt
    r = http.get("https://www.bing.com/search", params=params, ttl=600, min_interval=1.2)
    soup = BeautifulSoup(r["text"], "lxml")
    out = []
    for li in soup.select("li.b_algo")[:pool]:
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        url = _bing_decode(a["href"])
        if not url.startswith("http"):
            continue
        sn = li.select_one(".b_caption p") or li.select_one("div.b_caption") or li.select_one("p")
        date = None
        if sn:
            date = parse_date(sn.get_text(" ", strip=True))
        out.append({"title": strip_tags(a.get_text(" ", strip=True)), "url": url,
                    "content": sn.get_text(" ", strip=True) if sn else "",
                    "engine": "bing", "published_date": date})
    return out


def eng_bing_rss(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    params = {"q": opts.build_query(site_operator=False), "count": "30", "format": "rss",
              "setlang": "en"}
    if cc := opts.cc():
        params["cc"] = cc
    r = http.get("https://www.bing.com/search", params=params, ttl=600, min_interval=1.2)
    soup = BeautifulSoup(r["text"], "xml")
    out = []
    for it in soup.find_all("item")[:pool]:
        url = (it.link.get_text(strip=True) if it.link else "")
        if not url.startswith("http"):
            continue
        desc = it.description.get_text(" ", strip=True) if it.description else ""
        out.append({"title": strip_tags(it.title.get_text(" ", strip=True)) if it.title else "",
                    "url": url, "content": strip_tags(desc), "engine": "bing"})
    return out


SEARX_STATIC = (
    "https://search.femboy.ad", "https://search.lumy.live",
    "https://searx.dresden.network", "https://searxng.buffon.cloud",
    "https://anonsearch.win", "https://sx.xo.st", "https://sx.catgirl.cloud",
    "https://search.pereira.is",
)
# Backwards-compatible alias: the live pool is rebuilt from the registry below.
SEARX_INSTANCES = SEARX_STATIC
SEARX_TR = {"day": "day", "week": "week", "month": "month", "year": "year"}
_searx_pool: tuple[float, list[str]] | None = None
_searx_warm: list[str] = []
# Rolling honesty score per instance: public instances range from excellent to
# "serves the previous visitor's cached query". Persisted to disk so the next
# CLI run starts already knowing which instances answer from this host.
_searx_quality: dict[str, float] = {}
_SEARX_QUALITY_FILE = "searx-quality.json"
_SEARX_BLOCKED_BELOW = 0.12


def _load_searx_quality() -> dict[str, float]:
    if _searx_quality:
        return _searx_quality
    try:
        with open(os.path.join(CACHE_DIR, _SEARX_QUALITY_FILE), encoding="utf-8") as fh:
            _searx_quality.update({k: float(v) for k, v in json.load(fh).items()})
    except Exception:
        pass
    return _searx_quality


def _save_searx_quality() -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, _SEARX_QUALITY_FILE)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_searx_quality, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def searx_instances(http: Http) -> list[str]:
    """Public SearXNG instances, refreshed daily from the searx.space registry.

    Any single instance dies or starts blocking without warning, so the pool is
    discovered rather than hard-coded; the static list seeds it because those
    instances are known to answer from this host.
    """
    global _searx_pool
    now = time.time()
    if _searx_pool and now - _searx_pool[0] < 86400:
        return _searx_pool[1]
    urls = list(SEARX_STATIC)
    try:
        r = http.get("https://searx.space/data/instances.json", ttl=86400,
                     attempts=2, min_interval=0.2)
        data = json.loads(r["text"])
        scored = []
        for url, meta in (data.get("instances") or {}).items():
            url = (url or "").rstrip("/")
            if not url.startswith("https://") or not isinstance(meta, dict):
                continue
            if url in urls:
                continue
            if (meta.get("network_type") or "normal") != "normal":
                continue
            if ((meta.get("http") or {}).get("status_code")) != 200:
                continue
            timing = (meta.get("timing") or {}).get("search") or {}
            succ = timing.get("success_percentage") or 0
            med = ((timing.get("all") or {}).get("median")) or 99
            if succ < 50:
                continue
            scored.append((succ, -float(med), url))
        scored.sort(reverse=True)
        urls += [u for _, _, u in scored]
    except Exception:
        pass
    _searx_pool = (now, urls[:40])
    return _searx_pool[1]


def eng_searx(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    """Federated metasearch: public SearXNG instances, HTML endpoint.

    The JSON API is disabled on public instances, so results are parsed from the
    rendered page. Most public instances answer with an empty result page (their
    own upstream engines are dead), so a batch is probed concurrently and the
    batch whose results actually match the query wins — a healthy-looking
    instance can also hand back another visitor's cached SERP.
    """
    params = {"q": opts.build_query(), "categories": "news" if opts.news_bias() else "general",
              "language": "en", "safesearch": "1"}
    if opts.time_range in SEARX_TR:
        params["time_range"] = SEARX_TR[opts.time_range]
    elif opts.date_window_active():
        params["time_range"] = "year"
    if cc := opts.cc():
        params["countries"] = cc
    cand = searx_instances(http)
    quality = _load_searx_quality()
    order = [i for i in _searx_warm if i in cand] + [i for i in cand if i not in _searx_warm]
    order.sort(key=lambda i: -quality.get(i, 0.5))  # stable: ties keep warm order
    ranked = [i for i in order if quality.get(i, 0.5) >= _SEARX_BLOCKED_BELOW]
    order = ranked or order

    def probe(inst: str) -> tuple[str, list[dict]]:
        try:
            r = http.get(inst.rstrip("/") + "/search", params=params, ttl=600,
                         min_interval=1.0, attempts=1, timeout=15.0)
        except FetchError:
            return inst, []
        if r["status"] != 200 or len(r["text"]) < 4000:
            return inst, []
        soup = BeautifulSoup(r["text"], "lxml")
        rows: list[dict] = []
        for art in soup.select("article.result"):
            a = art.select_one("h3 a[href]") or art.select_one("a.url_header[href]")
            if not a:
                continue
            url = a["href"].strip()
            if not url.startswith("http") or "web.archive.org" in url:
                continue
            snip = art.select_one("p.content")
            when = art.select_one("time.published_date") or art.select_one("time")
            row = {"title": strip_tags(a.get_text(" ", strip=True)),
                   "url": url,
                   "content": strip_tags(snip.get_text(" ", strip=True)) if snip else "",
                   "engine": "searx"}
            if when:
                row["published_date"] = parse_date(when.get_text(" ", strip=True)) or ""
            rows.append(row)
            if len(rows) >= pool:
                break
        return inst, rows

    def batch(insts: list[str]) -> list[tuple[str, float, float, list[dict]]]:
        out: list[tuple[str, float, float, list[dict]]] = []
        if not insts:
            return out
        with ThreadPoolExecutor(max_workers=min(12, len(insts))) as ex:
            futs = {ex.submit(probe, inst): inst for inst in insts}
            for fut in as_completed(futs):
                try:
                    inst, rows = fut.result()
                except Exception:  # noqa: BLE001 - one bad instance must not kill the batch
                    continue
                if not rows:
                    continue
                rels = [relevance(opts.query, r["title"], r["content"], r["url"]) for r in rows]
                top, avg = max(rels), sum(rels) / len(rels)
                quality[inst] = round(0.65 * quality.get(inst, 0.5) + 0.35 * (0.6 * top + 0.4 * avg), 3)
                out.append((inst, top, avg, rows))
        return out

    seen = batch(order[:10])
    if (max((t[1] for t in seen), default=0.0) < 0.4
            and opts.search_depth not in ("ultra-fast", "fast")):
        seen += batch(order[10:34])
    if not seen:
        _save_searx_quality()
        return []
    seen.sort(key=lambda t: (-t[1], -t[2], order.index(t[0])))
    inst, top, _avg, rows = seen[0]
    if top >= 0.4:
        if inst not in _searx_warm:
            _searx_warm.insert(0, inst)
        del _searx_warm[6:]
    _save_searx_quality()
    return rows


def eng_google_news(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    q = opts.query
    if opts.exact_match:
        q = f'"{q}"'
    start, end = opts.window()
    if opts.time_range:
        q += f" when:{TIME_RANGE_DAYS[opts.time_range]}d"
    elif start:
        q += f" after:{start}"
    if end:
        q += f" before:{end}"
    if opts.include_domains:
        q += " " + " OR ".join(f"site:{d}" for d in opts.include_domains[:5])
    gl = (opts.country or "United States").lower()
    gl = {"united states": "US", "united kingdom": "GB", "india": "IN", "australia": "AU",
          "canada": "CA", "germany": "DE", "france": "FR", "japan": "JP", "thailand": "TH",
          "vietnam": "VN", "brazil": "BR", "spain": "ES", "italy": "IT"}.get(gl, "US")
    r = http.get("https://news.google.com/rss/search",
                 params={"q": q, "hl": "en-US", "gl": gl, "ceid": f"{gl}:en"},
                 ttl=300, min_interval=1.0)
    soup = BeautifulSoup(r["text"], "xml")
    out = []
    for it in soup.find_all("item")[:pool]:
        link = it.link.get_text(strip=True) if it.link else ""
        src = it.source.get_text(" ", strip=True) if it.source else ""
        pub = it.pubDate.get_text(strip=True) if it.pubDate else ""
        date = None
        if pub:
            try:
                date = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
            except ValueError:
                date = parse_date(pub)
        title = strip_tags(it.title.get_text(" ", strip=True)) if it.title else ""
        # source is usually "Headline - Source"; keep the headline clean.
        if src and title.endswith(f" - {src}"):
            title = title[: -(len(src) + 3)]
        out.append({"title": title, "url": link, "content": f"{src}: {title}" if src else title,
                    "engine": "google-news", "published_date": date})
    return out


def eng_bing_news(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    params = {"q": opts.build_query(site_operator=False), "format": "rss", "count": "30"}
    if opts.time_range:
        params["interval"] = {"day": "1", "week": "7", "month": "30", "year": "365"}[opts.time_range]
    r = http.get("https://www.bing.com/news/search", params=params, ttl=300, min_interval=1.0)
    soup = BeautifulSoup(r["text"], "xml")
    out = []
    for it in soup.find_all("item")[:pool]:
        url = it.link.get_text(strip=True) if it.link else ""
        url = _bing_news_url(url)
        if not url.startswith("http"):
            continue
        pub = it.pubDate.get_text(strip=True) if it.pubDate else ""
        date = None
        if pub:
            try:
                date = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
            except ValueError:
                date = parse_date(pub)
        out.append({"title": strip_tags(it.title.get_text(" ", strip=True)) if it.title else "",
                    "url": url, "content": strip_tags(it.description.get_text(" ", strip=True))
                    if it.description else "", "engine": "bing-news", "published_date": date})
    return out


def eng_wikipedia(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    lang = "en"
    if opts.country:
        lang = {"germany": "de", "france": "fr", "spain": "es", "italy": "it", "russia": "ru",
                "japan": "ja", "china": "zh", "thailand": "th", "vietnam": "vi", "portugal": "pt",
                "poland": "pl", "netherlands": "nl", "sweden": "sv"}.get(opts.country.lower(), "en")
    r = http.get(f"https://{lang}.wikipedia.org/w/api.php",
                 params={"action": "query", "list": "search", "srsearch": opts.query,
                         "format": "json", "srlimit": str(min(pool, 20)), "srprop": "snippet"},
                 ttl=3600, min_interval=0.3)
    data = r["text"]
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return []
    out = []
    for hit in payload.get("query", {}).get("search", [])[:pool]:
        title = hit.get("title", "")
        out.append({"title": title,
                    "url": f"https://{lang}.wikipedia.org/wiki/"
                           + urllib.parse.quote(title.replace(" ", "_")),
                    "content": strip_tags(hit.get("snippet", "")), "engine": "wikipedia"})
    return out


def eng_stackexchange(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    if not (DEV_RE.search(opts.query) or _dom(opts, "stackoverflow.com")):
        return []
    r = http.get("https://api.stackexchange.com/2.3/search/advanced",
                 params={"order": "desc", "sort": "relevance", "q": opts.query,
                         "site": "stackoverflow", "pagesize": str(min(pool, 15))},
                 ttl=1800, min_interval=0.3,
                 headers={"Accept-Encoding": "gzip"})
    try:
        payload = json.loads(r["text"])
    except json.JSONDecodeError:
        return []
    return [{"title": strip_tags(i.get("title", "")), "url": i.get("link", ""),
             "content": f"Asked {i.get('question_id')} on Stack Overflow; "
                        f"{i.get('answer_count', 0)} answers, {i.get('score', 0)} score.",
             "engine": "stackexchange"}
            for i in payload.get("items", [])[:pool] if i.get("link")]


DEV_RE = re.compile(r"\b(python|javascript|java|c\+\+|c#|rust|golang|typescript|sql|error|"
                    r"exception|numpy|pandas|asyncio|docker|kubernetes|git|react|vue|node|"
                    r"linux|bash|regex|http|api|library|package|module|function|class|code|"
                    r"install|pip|npm|debug|bug|compile|algorithm)\b", re.I)
TECH_RE = re.compile(r"\b(software|startup|ai|llm|model|gpu|cpu|server|cloud|database|"
                     r"framework|protocol|standard|privacy|security|breach|open.?source|"
                     r"release|version|benchmark|chip|semiconductor|internet|web)\b", re.I)
SCHOLAR_RE = re.compile(r"\b(paper|papers|research|study|studies|theory|theorem|survey|"
                        r"analysis|evidence|journal|arxiv|doi|academic|scientific|hypothesis|"
                        r"experiment|clinical|statistics|review of literature)\b", re.I)


def _dom(opts: SearchOpts, domain: str) -> bool:
    """True when a vertical API is explicitly requested via include_domains."""
    return any(d.lower().lstrip(".").endswith(domain) for d in opts.include_domains)


def eng_hackernews(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    if not (TECH_RE.search(opts.query) or DEV_RE.search(opts.query)
            or _dom(opts, "news.ycombinator.com") or opts.search_depth == "advanced"):
        return []
    params = {"query": opts.query, "hitsPerPage": str(min(pool, 20))}
    if opts.time_range:
        # Algolia filters on created_at_i (unix); restrict to the window.
        days = TIME_RANGE_DAYS[opts.time_range]
        params["numericFilters"] = f"created_at_i>{int(time.time()) - days * 86400}"
    r = http.get("https://hn.algolia.com/api/v1/search", params=params, ttl=900,
                 min_interval=0.3)
    try:
        payload = json.loads(r["text"])
    except json.JSONDecodeError:
        return []
    out = []
    for hit in payload.get("hits", [])[:pool]:
        title = strip_tags(hit.get("title") or hit.get("story_title") or "")
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not title or not url.startswith("http"):
            continue
        points = hit.get("points") or 0
        comments = hit.get("num_comments") or 0
        date = None
        if hit.get("created_at"):
            date = str(hit["created_at"])[:10]
        out.append({"title": title, "url": url,
                    "content": f"{points} points, {comments} comments on Hacker News"
                               + (f" ({date})" if date else ""),
                    "engine": "hackernews", "published_date": date})
    return out


def eng_github(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    if not (DEV_RE.search(opts.query) or _dom(opts, "github.com")):
        return []
    r = http.get("https://api.github.com/search/repositories",
                 params={"q": opts.query, "per_page": str(min(pool, 20))},
                 ttl=3600, min_interval=0.4, attempts=1,
                 headers={"Accept": "application/vnd.github+json"})
    try:
        payload = json.loads(r["text"])
    except json.JSONDecodeError:
        return []
    out = []
    for item in payload.get("items", [])[:pool]:
        if not item.get("html_url"):
            continue
        desc = strip_tags(item.get("description") or "")
        lang = item.get("language") or ""
        stars = item.get("stargazers_count", 0)
        out.append({"title": item.get("full_name", ""), "url": item["html_url"],
                    "content": " ".join(x for x in [desc, lang and f"Language: {lang}",
                                                    f"{stars} stars"] if x),
                    "engine": "github",
                    "published_date": (item.get("pushed_at") or "")[:10] or None})
    return out


def eng_openalex(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    if not SCHOLAR_RE.search(opts.query):
        return []
    r = http.get("https://api.openalex.org/works",
                 params={"search": opts.query, "per-page": str(min(pool, 20)),
                         "select": "id,display_name,publication_date,cited_by_count,"
                                   "primary_location,authorships"},
                 ttl=3600, min_interval=0.3, attempts=1)
    try:
        payload = json.loads(r["text"])
    except json.JSONDecodeError:
        return []
    out = []
    for w in payload.get("results", [])[:pool]:
        url = (w.get("primary_location") or {}).get("landing_page_url") or w.get("id", "")
        if not url:
            continue
        authors = ", ".join((a.get("author") or {}).get("display_name", "")
                            for a in (w.get("authorships") or [])[:3])
        out.append({"title": strip_tags(w.get("display_name") or ""), "url": url,
                    "content": f"{authors} — cited by {w.get('cited_by_count', 0)} "
                               f"(OpenAlex)",
                    "engine": "openalex",
                    "published_date": w.get("publication_date")})
    return out


def eng_arxiv(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    if not (SCHOLAR_RE.search(opts.query) or _dom(opts, "arxiv.org")):
        return []
    phrase = opts.query.replace('"', "")
    r = http.get("http://export.arxiv.org/api/query",
                 params={"search_query": f'all:"{phrase}"', "max_results": str(min(pool, 20)),
                         "sortBy": "relevance"}, ttl=3600, min_interval=0.6, attempts=1)
    soup = BeautifulSoup(r["text"], "xml")
    out = []
    for entry in soup.find_all("entry")[:pool]:
        url = entry.id.get_text(strip=True) if entry.id else ""
        if not url.startswith("http"):
            continue
        out.append({"title": strip_tags(entry.title.get_text(" ", strip=True)) if entry.title else "",
                    "url": url,
                    "content": strip_tags(entry.summary.get_text(" ", strip=True))[:600]
                    if entry.summary else "",
                    "engine": "arxiv",
                    "published_date": (entry.published.get_text(strip=True)
                                       if entry.published else "")[:10] or None})
    return out


def eng_site_index(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    """Domain-restricted search answered by the target sites themselves.

    Keyless SERPs on this network ignore `site:`, so for include_domains we ask
    each site directly: its Sphinx search index, its own search page, or its sitemap.
    """
    if not opts.include_domains:
        return []
    from .crawl import site_search  # local import avoids a cycle
    out: list[dict] = []
    for dom in opts.include_domains[:3]:
        try:
            rows = site_search(http, dom, opts.query, pool, log=lambda *a: None)
        except Exception:  # noqa: BLE001
            continue
        for row in rows:
            # no snippet in a site index; search() backfills it from the page
            out.append({"title": row.get("title") or host_of(row["url"]), "url": row["url"],
                        "content": "", "engine": "site-index"})
    return out


def eng_baidu(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    r = http.get("https://www.baidu.com/s", params={"wd": opts.query, "rn": "20"},
                 ttl=600, min_interval=1.5, attempts=2)
    soup = BeautifulSoup(r["text"], "lxml")
    out = []
    for blk in soup.select("div.result, div.c-container")[:pool]:
        a = blk.select_one("h3 a")
        if not a:
            continue
        url = blk.get("mu") or a.get("href", "")
        if not url.startswith("http"):
            continue
        # /s?wd= results wrap the target in baidu.com/link or /baidu.php redirects
        # whose payload is encrypted, so a wrapper URL is useless to the caller.
        if host_of(url).endswith("baidu.com"):
            continue
        sn = blk.select_one(".c-abstract, .content-right_2s9OH, [class*=abstract]")
        out.append({"title": strip_tags(a.get_text(" ", strip=True)), "url": url,
                    "content": sn.get_text(" ", strip=True) if sn else "", "engine": "baidu"})
    return out


def eng_freeserp(http: Http, opts: SearchOpts, pool: int) -> list[dict]:
    """FreeSerp: a free, keyless web index (no signup, no token).

    When its web engine is momentarily unavailable the API silently answers from
    a *domain* index instead — those rows are site cards, not pages, so they are
    discarded rather than emitted as junk results.
    """
    size = max(10, min(30, max(opts.max_results, pool)))
    r = http.get("https://freeserp.ai/api.php",
                 params={"index": "web", "q": opts.query, "size": str(size)},
                 ttl=600, min_interval=0.4)
    try:
        data = json.loads(r["text"])
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict) or data.get("index") != "web" or data.get("engine_fallback"):
        return []
    out: list[dict] = []
    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        url = row.get("url") or ""
        if not url.startswith("http"):
            continue
        out.append({"title": strip_tags(str(row.get("title") or "")), "url": url,
                    "content": strip_tags(str(row.get("snippet") or "")),
                    "published_date": row.get("published_at") or "",
                    "engine": "freeserp"})
        if len(out) >= max(pool, opts.max_results):
            break
    return out


ENGINES = [
    # order = the order engines are tried at a given depth (see DEPTH_ENGINES);
    # the third field is the ranking prior, which is independent of it.
    ("freeserp", eng_freeserp, 1.00),
    ("searx", eng_searx, 0.96),
    ("site-index", eng_site_index, 0.80),
    ("hackernews", eng_hackernews, 0.78),
    ("wikipedia", eng_wikipedia, 0.74),
    ("github", eng_github, 0.72),
    ("stackexchange", eng_stackexchange, 0.70),
    # DDG html endpoint is hard rate-limited (202/timeout) from most hosts, so
    # it is kept as a deep-search filler rather than a primary source.
    ("duckduckgo", eng_ddg_html, 0.90),
    ("openalex", eng_openalex, 0.66),
    ("arxiv", eng_arxiv, 0.64),
    ("baidu", eng_baidu, 0.55),
    # Bing's HTML SERP and web RSS are deliberately absent: on this network an
    # upstream proxy poisons them, so /search answers with results for queries
    # nobody asked (see eng_bing_web). Bing *News* RSS is unaffected.
]

NEWS_ENGINES = [
    # Bing News yields real publisher URLs; Google News wraps them in an
    # unresolvable /rss/articles/<token> redirect, so it ranks lower as a source.
    ("bing-news", eng_bing_news, 1.00),
    ("searx", eng_searx, 0.92),
    ("google-news", eng_google_news, 0.80),
    ("duckduckgo", eng_ddg_html, 0.72),
    ("hackernews", eng_hackernews, 0.66),
]

# Mirrors and reference pages that rank well but rarely answer the question.
MIRROR_RE = re.compile(r"(?:^|\.)(?:redlib\.[a-z.]+|nitter\.[a-z.]+|libreddit\.[a-z.]+|"
                       r"teddit\.[a-z.]+|old\.reddit\.com|archive\.of|docsbot\.ai|"
                       r"runebook\.dev|python3\.pl|python-reference\.readthedocs\.io)$")
DICT_RE = re.compile(r"(?:^|\.)(?:collinsdictionary\.com|dictionary\.cambridge\.com|"
                     r"merriam-webster\.com|dictionary\.com|thesaurus\.com|vocabulary\.com|"
                     r"urbandictionary\.com|dict\.longdo\.com|wordreference\.com)$")
DEFINITIONAL_RE = re.compile(r"\b(defin\w*|meaning\w*|synonym\w*|means|translate)\b", re.I)
# Auto-generated SERP/social index pages (/discover/, /ideas/, /tag/...): they
# rank in keyless engines but are never the page a caller wants to read.
AGG_RE = re.compile(r"/(?:discover|ideas|searches|results|hashtags?|topics?|tags?|trends|tools)/|"
                    r"/(?:search|discover|ideas|topics|tags?|trends|tools)$")
# Canonical reference hosts. A library's own documentation should rank above blog
# coverage of it; keyless SERPs rank by term overlap and put the blog first, so the
# official host gets a small, fixed lift.
AUTHORITY_RE = re.compile(r"(?:^|\.)(?:docs\.python\.org|peps\.python\.org|docs\.django\.org|"
                          r"docs\.pythondisclosure\.com|numpy\.org|pandas\.pydata\.org|"
                          r"docs\.sqlalchemy\.org|www\.postgresql\.org|dev\.mysql\.com|man7\.org|"
                          r"docs\.rust-lang\.org|doc\.rust-lang\.org|docs\.rs|kotlin\.lang\.org|"
                          r"developer\.mozilla\.org|mdn\.web\.io|web\.dev|docs\.oracle\.com|"
                          r"openjdk\.org|docs\.angular\.io|angular\.dev|react\.dev|vue\.org|"
                          r"learn\.microsoft\.com|developer\.apple\.com|www\.gnu\.org|"
                          r"docs\.docker\.com|kubernetes\.io|www\.ietf\.org|rfc-editor\.org)$|"
                          r"(?:^|\.)readthedocs\.io$")


def domain_prior(url: str, query: str = "") -> float:
    """Multiplier that demotes mirrors, dictionaries and SERP-index pages."""
    host = host_of(url)
    if MIRROR_RE.search(host):
        return 0.55
    if DICT_RE.search(host) and not DEFINITIONAL_RE.search(query or ""):
        return 0.45
    if AGG_RE.search(urllib.parse.urlsplit(url).path or ""):
        return 0.5
    if AUTHORITY_RE.search(host):
        return 1.12
    return 1.0


DEPTH_ENGINES = {
    "ultra-fast": 2, "fast": 3, "basic": 5, "advanced": 8,
}


def search(http: Http, opts: SearchOpts, *, log=None) -> dict:
    """Aggregate the keyless engines into one ranked, deduplicated result set."""
    log = log or (lambda *a: None)
    pool = opts.max_results * (4 if opts.search_depth == "advanced" else 3)
    table = NEWS_ENGINES if opts.news_bias() else ENGINES
    n_engines = DEPTH_ENGINES.get(opts.search_depth, 3)
    if opts.include_domains:
        # Keyless SERPs ignore `site:`, so domain-restricted queries lean on the
        # site's own index plus official APIs (arXiv, GitHub, SO, Wikipedia, HN).
        keep = ("freeserp", "searx", "duckduckgo", "google-news", "bing-news", "site-index",
                "wikipedia", "github", "arxiv", "stackexchange", "hackernews", "openalex")
        table = [e for e in table if e[0] in keep] or table
        n_engines = max(n_engines, len(table))

    merged: dict[str, dict] = {}
    healthy = 0

    def absorb(rows: list[dict], prior: float) -> None:
        for rank, row in enumerate(rows):
            url = row.get("url")
            if not url:
                continue
            key = norm_url(url)
            if key in merged:
                prev = merged[key]
                if len(row.get("content", "")) > len(prev.get("content", "")):
                    prev["content"] = row["content"]
                prev["engines"].append(row["engine"])
                prev["engine_prior"] = max(prev["engine_prior"], prior * (1 - rank / (pool + 3)))
                continue
            merged[key] = {
                "title": row.get("title") or host_of(url),
                "url": url,
                "content": row.get("content", ""),
                "score": 0.0,
                "engine": row["engine"],
                "engines": [row["engine"]],
                "engine_prior": prior * (1 - rank / (pool + 3)),
                "published_date": row.get("published_date"),
            }

    for name, fn, prior in table[:n_engines]:
        try:
            rows = fn(http, opts, pool)
        except FetchError as exc:
            log(f"engine {name}: unavailable ({exc.reason})")
            continue
        except Exception as exc:  # noqa: BLE001 - one bad engine must not kill search
            log(f"engine {name}: parse failure ({type(exc).__name__})")
            continue
        if rows:
            healthy += 1
        log(f"engine {name}: {len(rows)} results")
        absorb(rows, prior)

    # Query refinement: keyless SERPs drift on natural-language questions (a
    # "what is X?" query can return dictionary entries for X), so a weak first
    # pass is retried with the distinctive phrase quoted.
    if opts.search_depth != "ultra-fast" and len(key_terms(opts.query)) >= 2:
        scored = sorted((relevance(opts.query, m["title"], m["content"], m["url"])
                         * domain_prior(m["url"], opts.query) for m in merged.values()),
                        reverse=True)
        best = scored[0] if scored else 0.0
        good = sum(1 for s in scored if s >= 0.35)
        core = " ".join(key_terms(opts.query))
        quoted = opts.query.startswith('"') and opts.query.endswith('"')
        weak = best < 0.45 or good < min(3, opts.max_results)
        if weak and core and not quoted and not opts.exact_match:
            opts2 = SearchOpts(f'"{core}"', search_depth=opts.search_depth, topic=opts.topic,
                               max_results=opts.max_results, time_range=opts.time_range,
                               start_date=opts.start_date, end_date=opts.end_date,
                               include_domains=opts.include_domains,
                               exclude_domains=opts.exclude_domains, country=opts.country)
            try:
                gained = 0
                for fn in (eng_freeserp, eng_searx, eng_ddg_html):
                    rows = fn(http, opts2, pool)
                    before = len(merged)
                    absorb(rows, 0.95)
                    gained += len(merged) - before
                    log(f'refined query "{core}" via {fn.__name__}: '
                        f'{len(rows)} results, {len(merged) - before} new')
                    if gained >= 3:
                        break
            except Exception:  # noqa: BLE001
                pass

    results = list(merged.values())

    # domain filters
    if opts.include_domains:
        results = [r for r in results
                   if any(domain_match(r["url"], d) for d in opts.include_domains)]
    if opts.exclude_domains:
        results = [r for r in results
                   if not any(domain_match(r["url"], d) for d in opts.exclude_domains)]

    # relevance + freshness scoring
    start, end = opts.window()
    dated = []
    for r in results:
        rel = relevance(opts.query, r["title"], r["content"], r["url"])
        r["_rel"] = rel
        r["score"] = round(min(1.0, 0.62 * rel + 0.30 * r["engine_prior"] + 0.08)
                           * domain_prior(r["url"], opts.query), 4)
        if opts.date_window_active():
            if r.get("published_date"):
                inwin = (not start or r["published_date"] >= start) and \
                        (not end or r["published_date"] <= end)
                if inwin:
                    r["score"] = round(min(1.0, r["score"] + 0.12), 4)
                else:
                    r["score"] = round(r["score"] * 0.35, 4)
                dated.append(r)
    if opts.date_window_active() and len(dated) >= opts.max_results:
        results = dated

    # news bias: prefer news domains when the query is time-sensitive
    if opts.news_bias():
        results.sort(key=lambda r: (r["score"] + (0.10 if host_of(r["url"]) in NEWS_HOSTS else 0.0),
                                    r["published_date"] or ""), reverse=True)
    else:
        results.sort(key=lambda r: r["score"], reverse=True)

    # Keyless engines answer with off-topic filler next to real hits, so drop the
    # filler whenever enough on-topic candidates survived the refinement passes.
    strong = [r for r in results if r["_rel"] >= 0.15]
    if len(strong) >= 3:
        results = strong

    # Google News RSS hands out news.google.com/rss/articles/… redirect links that
    # an agent cannot cite or reuse. Bing News and SearXNG give the publisher URL,
    # so the redirect rows only fill a gap they leave.
    if opts.topic == "news":
        direct = [r for r in results if (r.get("engines") or [r.get("engine")]) != ["google-news"]]
        if len(direct) >= opts.max_results:
            results = direct

    results = results[:opts.max_results]

    # A result should carry a real content chunk rather than a one-line SERP
    # snippet, so thin rows get an excerpt pulled from the page itself. That
    # excerpt also makes the relevance score fair: a docs page whose SERP snippet
    # never mentions the query term is otherwise outranked by a blog post.
    if opts.search_depth != "advanced":
        from .extract import extract_one
        for r in [x for x in results if len(x["content"]) < 400][:8]:
            got = extract_one(http, r["url"], fmt="markdown", depth="basic",
                              query=opts.query, max_chars=1600)
            if got and got.get("content"):
                r["content"] = _best_excerpt(got["content"], opts.query)[:520]

    # advanced depth: enrich content by pulling the page itself
    if opts.search_depth == "advanced" or opts.include_raw_content:
        from .extract import extract_one
        for r in results:
            got = extract_one(http, r["url"], fmt="markdown",
                              depth="advanced" if opts.search_depth == "advanced" else "basic")
            if got:
                if opts.search_depth == "advanced" and len(got["content"]) > len(r["content"]):
                    r["content"] = _best_excerpt(got["content"], opts.query)
                if opts.include_raw_content:
                    r["raw_content"] = got["content"]

    # Stamp each result with a short stable handle like "e1bc48-00" and print it as
    # an ID: line; agents quote it back when citing a source.
    for i, r in enumerate(results):
        r["id"] = hashlib.sha1(r["url"].encode("utf-8")).hexdigest()[:6] + f"-{i:02d}"

    payload = {"query": opts.query, "results": results, "response_time": 0.0,
                "engine_health": healthy, "search_depth": opts.search_depth,
                "topic": opts.topic}
    for r in results:
        r.pop("_rel", None)
    if opts.include_images:
        payload["images"] = images(http, opts)
    if opts.search_depth == "advanced":
        payload["answer"] = _answer(results, opts.query, http)
    return payload


def _best_excerpt(markdown: str, query: str) -> str:
    from .core import chunk_text, rerank_chunks
    chunks = chunk_text(markdown, size=900)
    picked = rerank_chunks(chunks, query, keep=2)
    return "\n\n".join(picked)[:1500]


def _answer(results: list[dict], query: str, http: Http | None = None) -> str:
    """Lead advanced search with a short synthesized answer.

    The local model does that when one is reachable; otherwise the most
    query-relevant sentences are stitched together instead.
    """
    if not results:
        return ""
    ctx = []
    for i, r in enumerate(results[:5], 1):
        body = re.sub(r"\s+", " ", r["content"]).strip()[:700]
        if body:
            ctx.append(f"[{i}] {r['title']}: {body}")
    if not ctx:
        return ""
    if http is not None:
        try:
            from .research import _llm_chat, _llm_endpoint
            ep = _llm_endpoint(http)
            if ep:
                base, model = ep
                prompt = ("Answer the question in 2-4 sentences using only the excerpts. "
                          "Cite the excerpts inline as [n]. No preamble, no headings.\n\n"
                          f"Question: {query}\n\nExcerpts:\n" + "\n\n".join(ctx))
                got = _llm_chat(http, base, model, prompt, max_tokens=260, temperature=0.2)
                text = re.sub(r"\s+", " ", got or "").strip()
                qtok = set(tokenize(query))
                if (40 <= len(text) <= 900 and qtok
                        and qtok & set(tokenize(text))):
                    return text
        except Exception:  # noqa: BLE001 - an answer is optional, never fatal
            pass
    qtok = set(tokenize(query))
    picked: list[str] = []
    for r in results[:5]:
        for sent in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", r["content"])):
            stok = set(tokenize(sent))
            if len(sent) < 60 or len(sent) > 300 or not (qtok & stok):
                continue
            picked.append(sent)
            break
        if len(picked) >= 3:
            break
    return " ".join(picked)[:700]


_MURL_RE = re.compile(r'murl(?:&quot;|"):(?:&quot;|")(https?://[^"&]+?)(?:&quot;|")')
_PURL_RE = re.compile(r'purl(?:&quot;|"):(?:&quot;|")(https?://[^"&]+?)(?:&quot;|")')


def _bing_images(http: Http, opts: SearchOpts) -> list[dict]:
    """Bing image SERP: each card carries murl (image) and purl (source page)."""
    r = http.get("https://www.bing.com/images/search",
                 params={"q": opts.query, "form": "HDRSC2", "count": "35"},
                 ttl=1800, min_interval=1.0)
    text = r["text"]
    out: list[dict] = []
    seen: set[str] = set()
    for m in _MURL_RE.finditer(text):
        img = m.group(1).replace("&amp;", "&")
        if img in seen or not img.startswith("http"):
            continue
        seen.add(img)
        page = ""
        for pm in reversed(_PURL_RE.findall(text[max(0, m.start() - 400):m.start()])):
            page = pm.replace("&amp;", "&")
            break
        out.append({"url": img, "description": page})
        if len(out) >= opts.max_results:
            break
    return out


def _commons_images(http: Http, opts: SearchOpts) -> list[dict]:
    """Wikimedia Commons: keyless, returns real file + thumbnail URLs."""
    r = http.get("https://commons.wikimedia.org/w/api.php",
                 params={"action": "query", "format": "json", "generator": "search",
                         "gsrsearch": f"filetype:bitmap {opts.query}", "gsrlimit": "10",
                         "prop": "imageinfo", "iiprop": "url", "iiurlwidth": "1024"},
                 ttl=3600, min_interval=0.5)
    pages = ((json.loads(r["text"]).get("query") or {}).get("pages") or {})
    out = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (page.get("imageinfo") or [{}])[0]
        img = info.get("thumburl") or info.get("url")
        if img:
            out.append({"url": img, "description": page.get("title", "")})
        if len(out) >= opts.max_results:
            break
    return out


def images(http: Http, opts: SearchOpts) -> list[dict]:
    """Keyless image search: Bing image SERP, Wikimedia Commons as fallback."""
    out: list[dict] = []
    for fetch in (_bing_images, _commons_images):
        try:
            out = fetch(http, opts)
        except Exception:
            out = []
        if out:
            break
    if opts.include_image_descriptions:
        for i, item in enumerate(out):
            item["description"] = item.get("description") or f"{opts.query} image {i + 1}"
    return out


def favicon_url(url: str) -> str:
    return f"https://icons.duckduckgo.com/ip3/{host_of(url)}.ico"
