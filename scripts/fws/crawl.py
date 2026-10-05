"""Site traversal: crawl and map.

Discovery order: sitemap.xml (plus robots.txt Sitemap: refs) first, then BFS
over in-page links. max_depth / max_breadth / limit / select_paths /
select_domains / allow_external are the traversal controls; `instructions`
biases which links are expanded by ranking anchor text and URL tokens.
"""
from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from .core import (FetchError, Http, absolutize, domain_match, host_of, norm_url,
                   relevance, strip_tags, tokenize)

SKIP_EXT = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|ico|css|js|mjs|json|xml|zip|gz|tar|rar|7z|exe|dmg|"
    r"mp3|mp4|mov|avi|mkv|webm|woff2?|ttf|eot|docx?|xlsx?|pptx?)$", re.I)
DOC_EXT = re.compile(r"\.(pdf|epub|txt|md|rst)$", re.I)


def _registrable(host: str) -> str:
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    two = ".".join(parts[-2:])
    if two in {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
               "co.jp", "or.jp", "ne.jp", "co.in", "co.nz", "com.br", "com.mx",
               "com.sg", "com.hk", "co.kr", "com.tr", "com.ua", "co.za", "com.vn"}:
        return ".".join(parts[-3:])
    return two


def _compile(patterns: list[str] | None) -> list[re.Pattern]:
    out = []
    for p in patterns or []:
        try:
            out.append(re.compile(p))
        except re.error:
            continue
    return out


def _link_score(url: str, anchor: str, instructions: str | None, query: str = "",
                base_dir: str = "", seed_host: str = "") -> float:
    path = urllib.parse.urlsplit(url).path
    s = 0.0
    # stay on the seed host: subdomain hops are noise unless asked for
    if seed_host and host_of(url) != seed_host:
        s -= 0.30
    if instructions:
        s += relevance(instructions, anchor, path.replace("/", " "), url)
    if query:
        s += 0.5 * relevance(query, anchor, path.replace("/", " "), url)
    toks = set(tokenize(path + " " + anchor))
    if instructions:
        s += 0.15 * len(toks & set(tokenize(instructions)))
    # crawl depth stays local: pages beside the seed page are the useful ones
    if base_dir:
        here = path.rsplit("/", 1)[0] + "/"
        if here == base_dir:
            s += 0.30
        elif here.startswith(base_dir):
            s += 0.10
    if re.fullmatch(r"/(?:[a-z]{2}(?:-[A-Za-z]{2})?|\d+(?:\.\d+)?)/", path):
        s -= 0.60
    # prefer documentation-ish paths, penalise login/privacy/tag noise
    low = url.lower()
    if re.search(r"/(docs?|documentation|guides?|tutorial|blog|news|articles?|reference|api)/", low):
        s += 0.08
    if re.search(r"(login|signin|signup|register|privacy|terms|cookie|cart|checkout|"
                 r"/tags?/|/author/|\?)", low):
        s -= 0.12
    depth = len([p for p in urllib.parse.urlsplit(url).path.split("/") if p])
    s -= 0.01 * depth
    return s


def _sitemap_urls(http: Http, base: str, cap: int) -> list[str]:
    p = urllib.parse.urlsplit(base)
    roots = [f"{p.scheme}://{p.netloc}/sitemap.xml", f"{p.scheme}://{p.netloc}/sitemap_index.xml"]
    # discover from robots.txt
    try:
        rob = http.get(f"{p.scheme}://{p.netloc}/robots.txt", ttl=86400, min_interval=0.3,
                       attempts=1)
        for m in re.finditer(r"(?im)^sitemap:\s*(\S+)", rob["text"]):
            if m.group(1).startswith("http"):
                roots.insert(0, m.group(1))
    except Exception:
        pass

    found: list[str] = []
    seen_root: set[str] = set()
    for root in roots[:6]:
        if root in seen_root:
            continue
        seen_root.add(root)
        try:
            r = http.get(root, ttl=86400, min_interval=0.3, attempts=1)
        except Exception:
            continue
        ctype = (r.get("content_type") or "").lower()
        text = r["text"]
        if "xml" not in ctype and "<urlset" not in text and "<sitemapindex" not in text:
            continue
        if "<sitemapindex" in text:
            subs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)[:8]
            for sub in subs:
                try:
                    rs = http.get(sub, ttl=86400, min_interval=0.3, attempts=1)
                except Exception:
                    continue
                found.extend(re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", rs["text"]))
                if len(found) >= cap:
                    break
        else:
            found.extend(re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text))
        if len(found) >= cap:
            break
    out, seen = [], set()
    for u in found:
        u = u.strip()
        if not u.startswith("http"):
            continue
        key = norm_url(u)
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out[:cap]


def _page_links(http: Http, url: str) -> list[tuple[str, str]]:
    r = http.get(url, ttl=1800, min_interval=0.5, attempts=2)
    ctype = (r.get("content_type") or "").lower()
    if "html" not in ctype and "<html" not in r["text"][:2000].lower():
        return [], ""
    soup = BeautifulSoup(r["text"], "lxml")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    pairs = []
    for a in soup.find_all("a", href=True):
        href = absolutize(url, a["href"])
        if not href:
            continue
        pairs.append((href, strip_tags(a.get_text(" ", strip=True))[:120]))
    return pairs, title


def _filter_ok(url: str, base: str, *, allow_external: bool, sel_paths: list[re.Pattern],
               sel_domains: list[re.Pattern]) -> bool:
    if SKIP_EXT.search(urllib.parse.urlsplit(url).path):
        return False
    h = host_of(url)
    if not h:
        return False
    if not allow_external and _registrable(h) != _registrable(host_of(base)):
        return False
    if sel_domains and not any(rx.search(h) for rx in sel_domains):
        return False
    if sel_paths:
        path = urllib.parse.urlsplit(url).path or "/"
        full = urllib.parse.urlunsplit(urllib.parse.urlsplit(url)[:2] + (path, "", ""))
        if not any(rx.search(path) or rx.search(full) or rx.search(url) for rx in sel_paths):
            return False
    return True


def traverse(http: Http, url: str, *, max_depth: int = 1, max_breadth: int = 20,
             limit: int = 50, instructions: str | None = None,
             select_paths: list[str] | None = None, select_domains: list[str] | None = None,
             allow_external: bool = True, want_content: bool = True,
             fmt: str = "markdown", depth: str = "basic", log=None) -> dict:
    """Shared engine for crawl (want_content=True) and map (False)."""
    log = log or (lambda *a: None)
    max_depth = max(1, int(max_depth))
    max_breadth = max(1, int(max_breadth))
    limit = max(1, int(limit))
    sel_paths, sel_domains = _compile(select_paths), _compile(select_domains)

    base = url if re.match(r"^https?://", url) else "https://" + url
    p = urllib.parse.urlsplit(base)
    base = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))
    base_dir = p.path.rsplit("/", 1)[0] + "/"
    seed_host = p.netloc

    visited: set[str] = set()
    pages: list[dict] = []
    frontier: list[tuple[int, str, str, float]] = [(0, base, "", 1.0)]

    # select_paths selects which pages are *returned*, not which pages are walked:
    # a site's asyncio pages usually sit behind an index page that does not match
    # the pattern, so filtering during traversal starves the crawl.
    def traversable(u: str) -> bool:
        return _filter_ok(u, base, allow_external=allow_external,
                          sel_paths=[], sel_domains=sel_domains)

    def wanted(u: str) -> bool:
        return _filter_ok(u, base, allow_external=allow_external,
                          sel_paths=sel_paths, sel_domains=sel_domains)

    def score(u: str, anchor: str) -> float:
        s = _link_score(u, anchor, instructions, base_dir=base_dir, seed_host=seed_host)
        if sel_paths and any(rx.search(urllib.parse.urlsplit(u).path or "/") for rx in sel_paths):
            s += 0.6
        return s

    # sitemap-first: sitemaps give the structure, but the seed page's own
    # neighbourhood is ranked above the sitemap bulk.
    smap = _sitemap_urls(http, base, cap=min(limit * 4, 400))
    if smap:
        log(f"sitemap: {len(smap)} candidate urls")
        for u in smap:
            if traversable(u):
                frontier.append((1, u, "", score(u, "")))
    else:
        log("sitemap: none found, BFS only")

    # With select_paths the matching pages can sit a level deeper than the
    # index page that links them, so priority outranks depth.
    queue = sorted(frontier, key=lambda t: (-t[3], t[0]) if sel_paths else (t[0], -t[3]))

    processed = 0
    while queue and len(pages) < limit and processed < max(limit * 6, 24):
        d, u, anchor, _prio = queue.pop(0)
        key = norm_url(u)
        if key in visited or d > max_depth:
            continue
        visited.add(key)
        processed += 1
        try:
            links, title = _page_links(http, u)
        except FetchError as exc:
            log(f"skip {u}: {exc.reason}")
            continue
        except Exception as exc:  # noqa: BLE001
            log(f"skip {u}: {type(exc).__name__}")
            continue
        if want_content:
            from .extract import extract_one
            got = extract_one(http, u, fmt=fmt, depth=depth)
            page = {"url": u, "title": (got or {}).get("title") or title or host_of(u),
                    "content": (got or {}).get("content", "")}
        else:
            page = {"url": u, "title": title or host_of(u), "content": ""}
        if d == 0 or wanted(u):
            pages.append(page)
            log(f"visited[{len(pages)}] d={d} {u}")
        else:
            log(f"through d={d} {u}")

        if d >= max_depth or len(pages) >= limit:
            continue
        ranked = sorted(
            [(l, a) for l, a in links if norm_url(l) not in visited and traversable(l)],
            key=lambda la: score(la[0], la[1]),
            reverse=True)
        for l, a in ranked[:max_breadth]:
            queue.append((d + 1, l, a, score(l, a)))
        # breadth-first by depth, best-scored page first within each depth
        queue.sort(key=lambda t: (-t[3], t[0]) if sel_paths else (t[0], -t[3]))

    pages = pages[:limit]
    if want_content:
        from .engines import favicon_url
        for pg in pages:
            pg["favicon"] = favicon_url(pg["url"])
    return {"base_url": base, "results": pages,
            "discovered": len(visited), "sitemap_used": bool(smap)}


def crawl(http: Http, url: str, **kw) -> dict:
    kw.setdefault("want_content", True)
    return traverse(http, url, **kw)


def map_site(http: Http, url: str, **kw) -> dict:
    kw["want_content"] = False
    return traverse(http, url, **kw)


# ---------------------------------------------------------------------------
# site-scoped search (for search --include-domains)
#
# Keyless SERPs on this network ignore the `site:` operator, so domain-restricted
# search is answered by the site itself: its Sphinx search index, its own search
# page, or its sitemap.
# ---------------------------------------------------------------------------
SEARCH_PATHS = (
    "/search?q={q}", "/search/?q={q}", "/search?query={q}", "/?s={q}",
    "/search.php?q={q}", "/search?search={q}", "/en/stable/search/?q={q}",
    "/search/index.html?q={q}", "/?q={q}", "/search/?search={q}",
)
NAV_PATHS = re.compile(
    r"^/(about|contact|help|login|signin|signup|account|pricing|privacy|terms|"
    r"categories|tags|authors|archive|feed|feeds|page|index|sitemap)(/|$)", re.I)


def _site_base(domain: str) -> str:
    dom = domain.strip().rstrip("/")
    if dom.startswith("http"):
        return dom
    return "https://" + dom.lstrip(".")


def _rank_paths(urls_and_titles: list[tuple[str, str]], query: str, pool: int) -> list[dict]:
    qtok = set(tokenize(query))
    scored = []
    for url, title in urls_and_titles:
        path = urllib.parse.urlsplit(url).path
        words = set(tokenize(path.replace("/", " ").replace("-", " ").replace("_", " ")))
        words |= set(tokenize(title))
        if not words or not qtok:
            continue
        overlap = len(qtok & words) / len(qtok)
        if overlap <= 0:
            continue
        scored.append((overlap, url, title))
    scored.sort(key=lambda t: -t[0])
    return [{"url": u, "title": t, "content": "", "_site_score": s} for s, u, t in scored[:pool]]


def _sphinx_search(http: Http, base: str, query: str, pool: int) -> list[dict]:
    """Sphinx docs (docs.python.org, most ReadTheDocs) ship a full JS search index."""
    import json as _json
    index_url = None
    try:
        page = http.get(base, ttl=1800, min_interval=0.3, attempts=1)
        m = re.search(r'["\']([^"\']*searchindex\.js)["\']', page["text"])
        if m:
            index_url = absolutize(page["url"], m.group(1)) or urllib.parse.urljoin(
                base, m.group(1))
    except FetchError:
        pass
    if not index_url:
        try:
            probe = http.get(base.rstrip("/") + "/searchindex.js", ttl=1800,
                             min_interval=0.3, attempts=1)
            index_url = probe["url"]
        except FetchError:
            return []
    try:
        raw = http.get(index_url, ttl=3600, min_interval=0.3, attempts=1)["text"]
    except FetchError:
        return []
    m = re.search(r"Search\.setIndex\((.*)\)\s*;?\s*$", raw.strip(), re.S)
    body = m.group(1) if m else raw
    try:
        data = _json.loads(body)
    except _json.JSONDecodeError:
        try:
            data = _json.loads(re.sub(r'([{,])\s*([A-Za-z_]\w*)\s*:', r'\1"\2":', body))
        except _json.JSONDecodeError:
            return []
    docnames = data.get("docnames") or []
    titles = data.get("titles") or []
    terms = data.get("terms") or {}
    prefix = index_url[: -len("searchindex.js")]
    hits: dict[int, float] = {}
    for tok in tokenize(query):
        docs = terms.get(tok)
        if docs is None:
            # Sphinx stems terms, so fall back to a prefix match.
            docs = []
            for term, d in terms.items():
                if term.startswith(tok[:4]) or tok.startswith(term[:4]):
                    docs = docs + (d if isinstance(d, list) else [d])
        for d in (docs if isinstance(docs, list) else [docs]):
            if isinstance(d, int) and 0 <= d < len(docnames):
                hits[d] = hits.get(d, 0) + 1.0
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    qtok = set(tokenize(query))
    scored = []
    for d, n in ranked:
        doc, title = docnames[d], (titles[d] if d < len(titles) else docnames[d])
        words = set(tokenize(doc.replace("/", " ").replace("-", " ").replace("_", " ")))
        words |= set(tokenize(title))
        path_bonus = len(qtok & words) / max(len(qtok), 1)
        scored.append((n / max(len(qtok), 1) + 1.2 * path_bonus, d, title))
    scored.sort(key=lambda t: -t[0])
    out = []
    for score, d, title in scored[:pool]:
        url = prefix + docnames[d] + (".html" if not docnames[d].endswith(".html") else "")
        out.append({"url": url, "title": strip_tags(title), "content": "",
                    "_site_score": round(score, 3)})
    return out


def _search_page(http: Http, base: str, query: str, pool: int) -> list[dict]:
    q = urllib.parse.quote_plus(query)
    found: list[tuple[str, str]] = []
    for tmpl in SEARCH_PATHS[:6]:
        url = urllib.parse.urljoin(base.rstrip("/") + "/", tmpl.format(q=q))
        try:
            r = http.get(url, ttl=900, min_interval=0.4, attempts=1)
        except FetchError:
            continue
        if r["status"] >= 400:
            continue
        soup = BeautifulSoup(r["text"], "lxml")
        rows = []
        for a in soup.find_all("a", href=True):
            href = absolutize(r["url"], a["href"]) or ""
            if not href.startswith("http") or not domain_match(href, host_of(base)):
                continue
            path = urllib.parse.urlsplit(href).path
            if NAV_PATHS.match(path) or SKIP_EXT.search(path):
                continue
            text = a.get_text(" ", strip=True)
            if not text or len(text) < 8:
                continue
            rows.append((href, strip_tags(text)))
        seen, uniq = set(), []
        for u, t in rows:
            k = norm_url(u)
            if k not in seen:
                seen.add(k)
                uniq.append((u, t))
        if len(uniq) >= 3:
            found = uniq
            break
    return _rank_paths(found, query, pool)


def _sitemap_search(http: Http, base: str, query: str, pool: int) -> list[dict]:
    res = map_site(http, base, max_depth=1, max_breadth=30, limit=150,
                   log=lambda *a: None)
    return _rank_paths([(p["url"], p.get("title") or "") for p in res.get("results", [])],
                       query, pool)


def site_search(http: Http, domain: str, query: str, pool: int = 10, *, log=None) -> list[dict]:
    """Find pages about `query` inside `domain` without relying on a SERP."""
    log = log or (lambda *a: None)
    base = _site_base(domain)
    for label, fn in (("sphinx", _sphinx_search), ("search-page", _search_page),
                      ("sitemap", _sitemap_search)):
        try:
            rows = fn(http, base, query, pool)
        except Exception as exc:  # noqa: BLE001
            log(f"site-search {domain} {label}: {type(exc).__name__}")
            continue
        if rows:
            log(f"site-search {domain} via {label}: {len(rows)} hits")
            return rows
    log(f"site-search {domain}: nothing found")
    return []
