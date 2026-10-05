#!/usr/bin/env python3
"""free-web-search CLI — keyless web search, extract, crawl, map and research.

Subcommands: search | extract | crawl | map | research | check
Everything runs on free, public, no-key endpoints. Plain-text output is a stable
`Answer:` / `Detailed Results:` block that agents can parse without a JSON reader;
`--json` emits the structured payload instead.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fws.core import Http, truncate  # noqa: E402


# ---------------------------------------------------------------------------
# text output formatters
# ---------------------------------------------------------------------------
def format_results(response: dict) -> str:
    out = []
    if response.get("answer"):
        out.append(f"Answer: {response['answer']}")
    out.append("Detailed Results:")
    for r in response.get("results", []):
        out.append(f"\nTitle: {r.get('title', '')}")
        if r.get("id"):
            out.append(f"ID: {r['id']}")
        out.append(f"URL: {r.get('url', '')}")
        out.append(f"Content: {r.get('content', '')}")
        if r.get("raw_content"):
            out.append(f"Raw Content: {r['raw_content']}")
        if r.get("favicon"):
            out.append(f"Favicon: {r['favicon']}")
    if response.get("images"):
        out.append("\nImages:")
        for i, image in enumerate(response["images"]):
            if isinstance(image, str):
                out.append(f"\n[{i + 1}] URL: {image}")
            else:
                out.append(f"\n[{i + 1}] URL: {image.get('url', '')}")
                if image.get("description"):
                    out.append(f"   Description: {image['description']}")
    return "\n".join(out)


def format_crawl_results(response: dict) -> str:
    out = ["Crawl Results:", f"Base URL: {response.get('base_url', '')}", "\nCrawled Pages:"]
    for i, page in enumerate(response.get("results", [])):
        out.append(f"\n[{i + 1}] URL: {page.get('url', '')}")
        raw = page.get("raw_content")
        if raw:
            out.append(f"Content: {raw[:200] + '...' if len(raw) > 200 else raw}")
        if page.get("favicon"):
            out.append(f"Favicon: {page['favicon']}")
    return "\n".join(out)


def format_map_results(response: dict) -> str:
    out = ["Site Map Results:", f"Base URL: {response.get('base_url', '')}", "\nMapped Pages:"]
    for i, page in enumerate(response.get("results", [])):
        out.append(f"\n[{i + 1}] URL: {page if isinstance(page, str) else page.get('url', '')}")
    return "\n".join(out)


def format_research_results(response: dict) -> str:
    if response.get("error"):
        return f"Research Error: {response['error']}"
    return response.get("content") or "No research results available"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def str2bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def list_arg(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def make_logger(verbose: bool):
    def log(msg: str) -> None:
        if verbose:
            print(f"[fws] {msg}", file=sys.stderr)
    return log


def http_from_args(args) -> Http:
    return Http(timeout=float(args.timeout))


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------
def cmd_search(args) -> int:
    from fws.engines import SearchOpts, favicon_url, search
    log = make_logger(args.verbose)
    opts = SearchOpts(
        args.query,
        search_depth=args.search_depth, topic=args.topic, time_range=args.time_range,
        start_date=args.start_date, end_date=args.end_date, max_results=args.max_results,
        include_images=args.include_images,
        include_image_descriptions=args.include_image_descriptions,
        include_raw_content=args.include_raw_content,
        include_domains=list_arg(args.include_domains),
        exclude_domains=list_arg(args.exclude_domains),
        country=args.country, include_favicon=args.include_favicon,
        exact_match=args.exact_match,
    )
    t0 = time.time()
    payload = search(http_from_args(args), opts, log=log)
    payload["response_time"] = round(time.time() - t0, 3)
    if opts.include_favicon:
        for r in payload["results"]:
            r["favicon"] = favicon_url(r["url"])
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not payload["results"]:
            print(f"Search Error: no results for {opts.query!r} — all engines refused or "
                  f"returned nothing. Retry with a shorter query or --search-depth advanced.")
            return 1
        print(format_results(payload))
    return 0


def cmd_extract(args) -> int:
    from fws.extract import extract_urls
    log = make_logger(args.verbose)
    urls = list(args.urls or [])
    if args.urls_file:
        with open(args.urls_file, "r", encoding="utf-8") as fh:
            urls += [ln.strip() for ln in fh if ln.strip()]
    if args.url:
        urls.append(args.url)
    urls = urls + list_arg(args.urls_csv)
    if not urls:
        print("Extract Error: no URLs supplied", file=sys.stderr)
        return 2
    results = extract_urls(
        http_from_args(args), urls, fmt=args.format, depth=args.extract_depth,
        include_images=args.include_images, include_favicon=args.include_favicon,
        query=args.query, max_chars=args.max_chars, log=log)
    payload = {"results": results, "response_time": 0.0, "base_url": ""}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_results(payload))
    return 0 if any(not r.get("failed") for r in results) else 1


def cmd_crawl(args) -> int:
    from fws.crawl import crawl
    log = make_logger(args.verbose)
    res = crawl(
        http_from_args(args), args.url, max_depth=args.max_depth, max_breadth=args.max_breadth,
        limit=args.limit, instructions=args.instructions,
        select_paths=list_arg(args.select_paths), select_domains=list_arg(args.select_domains),
        allow_external=args.allow_external, fmt=args.format, depth=args.extract_depth, log=log)
    for pg in res["results"]:
        pg["raw_content"] = pg.pop("content", "")
    if args.include_favicon:
        from fws.engines import favicon_url
        for pg in res["results"]:
            pg["favicon"] = favicon_url(pg["url"])
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        if not res["results"]:
            print(f"Crawl Error: no pages crawled from {args.url} — check the URL, "
                  f"--select-paths, or raise --max-depth/--limit.")
            return 1
        print(format_crawl_results(res))
    return 0


def cmd_map(args) -> int:
    from fws.crawl import map_site
    log = make_logger(args.verbose)
    res = map_site(
        http_from_args(args), args.url, max_depth=args.max_depth, max_breadth=args.max_breadth,
        limit=args.limit, instructions=args.instructions,
        select_paths=list_arg(args.select_paths), select_domains=list_arg(args.select_domains),
        allow_external=args.allow_external, log=log)
    res["results"] = [pg["url"] for pg in res["results"]]
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        if not res["results"]:
            print(f"Map Error: no pages mapped from {args.url} — check the URL, "
                  f"--select-paths, or raise --max-depth/--limit.")
            return 1
        print(format_map_results(res))
    return 0


def cmd_research(args) -> int:
    from fws.research import research
    log = make_logger(args.verbose)
    res = research(http_from_args(args), args.input, model=args.model, log=log)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(format_research_results(res))
    return 0 if res.get("ok") else 1


def cmd_check(args) -> int:
    from fws.engines import ENGINES, NEWS_ENGINES, SearchOpts
    log = make_logger(args.verbose)
    http = Http(timeout=float(args.timeout))
    print("free-web-search health check")
    print("=" * 62)
    for mod in ("trafilatura", "bs4", "lxml", "markdownify", "requests"):
        try:
            __import__(mod)
            print(f"  [ok]      python package: {mod}")
        except ImportError:
            print(f"  [missing] python package: {mod}  -> pip install {mod}")
    print("-" * 62)
    probes = {
        "freeserp": ("python asyncio tutorial", {}),
        "searx": ("python asyncio tutorial", {}),
        "duckduckgo": ("python asyncio tutorial", {}),
        "baidu": ("python asyncio 教程", {}),
        "hackernews": ("python asyncio", {}),
        "wikipedia": ("asyncio event loop", {}),
        "github": ("asyncio", {}),
        "stackexchange": ("python asyncio error", {}),
        "openalex": ("large language model survey", {}),
        "arxiv": ("large language model survey", {}),
        "site-index": ("asyncio", {"include_domains": ["docs.python.org"]}),
        "google-news": ("OpenAI", {"time_range": "week"}),
        "bing-news": ("OpenAI", {"time_range": "week"}),
    }
    seen, ok = set(), 0
    for name, fn, _prior in list(ENGINES) + list(NEWS_ENGINES):
        if name in seen:
            continue
        seen.add(name)
        q, kw = probes.get(name, ("python asyncio tutorial", {}))
        try:
            rows = fn(http, SearchOpts(q, **kw), 5)
            if rows:
                ok += 1
            print(f"  [{'ok' if rows else 'warn'}]      engine {name}: {len(rows)} results")
            if rows:
                print(f"           e.g. {rows[0]['url'][:78]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [fail]    engine {name}: {type(exc).__name__}: {exc}")
    print("-" * 62)
    from fws.research import _llm_endpoint
    llm = _llm_endpoint(http)
    print(f"  local LLM for research synthesis: {llm[0]} ({llm[1]})" if llm else
          "  local LLM: none reachable -> research emits a cited evidence dossier")
    print("=" * 62)
    print(f"engines healthy: {ok}/{len(seen)}   cache: "
          f"{os.environ.get('FWS_CACHE_DIR', '%LOCALAPPDATA%/free-web-search/cache')}")
    return 0 if ok >= 2 else 1


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fws", description="Keyless web search/extract/crawl/map/research "
                                "(free public engines, no API key).")
    # Global flags work before *and* after the subcommand; SUPPRESS keeps the
    # pre-subcommand value when the subcommand omits the flag.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--timeout", default=argparse.SUPPRESS,
                        help="per-request timeout seconds (default 20)")
    common.add_argument("--verbose", "-v", action="store_true", default=argparse.SUPPRESS,
                        help="progress to stderr")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit API-shaped JSON instead of text")
    p.add_argument("--timeout", default=20, help="per-request timeout seconds (default 20)")
    p.add_argument("--verbose", "-v", action="store_true", help="progress to stderr")
    p.add_argument("--json", action="store_true", help="emit API-shaped JSON instead of text")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search the web across free engines", parents=[common])
    s.add_argument("query")
    s.add_argument("--search-depth", default="basic",
                   choices=["basic", "advanced", "fast", "ultra-fast"])
    s.add_argument("--topic", default="general", choices=["general", "news"])
    s.add_argument("--time-range", default=None, choices=["day", "week", "month", "year"])
    s.add_argument("--start-date", default="", help="YYYY-MM-DD")
    s.add_argument("--end-date", default="", help="YYYY-MM-DD")
    s.add_argument("--max-results", type=int, default=5, help="5..20 (default 5)")
    s.add_argument("--include-images", action="store_true")
    s.add_argument("--include-image-descriptions", action="store_true")
    s.add_argument("--include-raw-content", action="store_true")
    s.add_argument("--include-domains", default="")
    s.add_argument("--exclude-domains", default="")
    s.add_argument("--country", default="", help='full name, e.g. "United States"')
    s.add_argument("--include-favicon", action="store_true")
    s.add_argument("--exact-match", action="store_true")
    s.set_defaults(func=cmd_search)

    e = sub.add_parser("extract", help="read the main text of one or more URLs", parents=[common])
    e.add_argument("urls", nargs="*", help="one or more URLs")
    e.add_argument("--url", default="", help="single URL")
    e.add_argument("--urls-csv", default="", help="comma-separated URLs")
    e.add_argument("--urls-file", default="", help="file with one URL per line")
    e.add_argument("--extract-depth", default="basic", choices=["basic", "advanced"])
    e.add_argument("--include-images", action="store_true")
    e.add_argument("--format", default="markdown", choices=["markdown", "text"])
    e.add_argument("--include-favicon", action="store_true")
    e.add_argument("--query", default=None, help="rerank extracted chunks by relevance")
    e.add_argument("--max-chars", type=int, default=0, help="0 = no truncation")
    e.set_defaults(func=cmd_extract)

    for name, fn, extra in (("crawl", cmd_crawl, True), ("map", cmd_map, False)):
        c = sub.add_parser(name, help=f"{name} a site from a base URL", parents=[common])
        c.add_argument("url")
        c.add_argument("--max-depth", type=int, default=1)
        c.add_argument("--max-breadth", type=int, default=20)
        c.add_argument("--limit", type=int, default=50)
        c.add_argument("--instructions", default=None)
        c.add_argument("--select-paths", default="", help="regex, comma-separated")
        c.add_argument("--select-domains", default="", help="regex, comma-separated")
        c.add_argument("--allow-external", type=str2bool, default=True)
        if extra:
            c.add_argument("--extract-depth", default="basic", choices=["basic", "advanced"])
            c.add_argument("--format", default="markdown", choices=["markdown", "text"])
            c.add_argument("--include-favicon", action="store_true")
        c.set_defaults(func=fn)

    r = sub.add_parser("research", help="cited multi-source research report", parents=[common])
    r.add_argument("input")
    r.add_argument("--model", default="auto", choices=["mini", "pro", "auto"])
    r.set_defaults(func=cmd_research)

    c = sub.add_parser("check", help="self-test: deps, engines, local LLM", parents=[common])
    c.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"free-web-search error: {exc}\n"
              f"Run `python scripts/fws.py check` to test dependencies and engines; "
              f"see README.md -> Troubleshooting.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
