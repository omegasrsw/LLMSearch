---
name: free-web-search
description: PRIMARY web-search skill for all projects — keyless web search, extract, crawl, site-map and multi-source research through one command-line tool (search/extract/crawl/map/research/check). Costs nothing and needs no API key: every result comes from free public endpoints. Use for any web search, "look this up online", reading a specific URL, crawling or enumerating a site's pages, or a cited deep-research report.
whenToUse: Default for every web task in every project — the user asks to search the web, find sources, check something current, read a URL, crawl/map a documentation site, or produce a researched report. No key, no quota, no billing: use it first, always.
metadata:
  script: scripts/fws.py
  requires: python with requests, beautifulsoup4, lxml, trafilatura and markdownify; optional local OpenAI-compatible LLM (FWS_LLM_BASE_URL) for research synthesis
  cost: free — no API key, no quota, public endpoints only
  repo: https://github.com/<owner>/websearch_free
---

# Keyless web search (`free-web-search`)

**This is the default web-search path for every project.** It costs nothing, needs
no API key and no account, and never calls a paid search API — everything below
runs on free public endpoints.

## Script

`scripts/fws.py`, resolved against **the base directory of this skill** — the
harness reports it when the skill loads ("Base directory for this skill: …").
Never assume a home directory: the skill may live in `~/.agents/skills/`,
`~/.claude/skills/`, `~/.codex/skills/`, `~/.hermes/skills/`, a project
`.agents/skills/`, or any custom skill root.

Set it once per shell, then every command is `python "$S" <subcommand> …`:

```bash
# bash / zsh — replace with the reported base directory if it differs
S="$HOME/.agents/skills/free-web-search/scripts/fws.py"
```

```powershell
# PowerShell
$S = "$env:USERPROFILE\.agents\skills\free-web-search\scripts\fws.py"
```

Subcommands: `search`, `extract`, `crawl`, `map`, `research`, `check`.
Use `python3` instead of `python` on macOS/Linux if `python` is not on PATH.

Smoke-test the whole stack (deps, every engine, the local LLM) before anything else:

```bash
python "$S" check
```

Nothing here needs a key. Results come from public endpoints: the FreeSerp free
web index, SearXNG instances, Bing/Google News RSS, Bing image search, Wikipedia,
StackExchange, GitHub, Hacker News, OpenAlex, arXiv, Baidu, and a site's own
search/sitemap index.

## Files

Everything the skill needs is already in this directory — nothing is downloaded at
install time. Copy all of it (some installers only copy files named here):

- `scripts/fws.py` — the CLI entry point; the only file you run
- `scripts/fws/__init__.py` — package marker
- `scripts/fws/core.py` — HTTP session, disk cache, ranking, chunking, local-LLM probe
- `scripts/fws/engines.py` — the free search engines and result aggregation
- `scripts/fws/extract.py` — URL → readable text (trafilatura, PDF, Wayback/AMP fallbacks)
- `scripts/fws/crawl.py` — `crawl` / `map` traversal and site-scoped search
- `scripts/fws/research.py` — the multi-source research pipeline
- `scripts/mcp_server.py` — optional MCP stdio shim for harnesses that speak MCP
  instead of skills; the CLI never uses it

## Global flags (work before or after the subcommand)

- `--timeout N` per-request timeout seconds (default 20)
- `--verbose` / `-v` progress to **stderr** (which engines answered, timings) — always add it when a result looks thin, so you can see what actually ran
- `--json` API-shaped JSON instead of the plain-text layout

## search

```bash
python $S search "asyncio vs threading python"
python $S search "openai announcement" --topic news --time-range week --max-results 8
python $S search "paddle ocr" --include-domains github.com --include-raw-content
python $S search "what is retrieval augmented evaluation" --search-depth advanced -v
```

Flags and defaults: `--search-depth basic|advanced|fast|ultra-fast`
(default `basic`), `--topic general|news`, `--time-range day|week|month|year`,
`--start-date`/`--end-date` (`YYYY-MM-DD`), `--max-results` 5..20 (default 5),
`--include-images`, `--include-image-descriptions`, `--include-raw-content`,
`--include-domains` / `--exclude-domains` (comma-separated), `--country` (full name),
`--include-favicon`, `--exact-match`.

Output layout: optional `Answer:`, then
`Detailed Results:` with `Title:` / `URL:` / `Content:` (plus `ID:`, `Raw Content:`,
`Favicon:` and an `Images:` block when requested).

## extract

```bash
python $S extract "https://docs.python.org/3/library/asyncio-task.html"
python $S extract "https://a.com/x" "https://b.com/y" --extract-depth advanced
python $S extract --urls-file links.txt --query "rate limiting" --max-chars 4000
```

Accepts URLs positionally, or `--url`, `--urls-csv`, `--urls-file`.
`--extract-depth basic|advanced`, `--format markdown|text` (default `markdown`),
`--include-images`, `--include-favicon`, `--query` (re-ranks extracted chunks),
`--max-chars` (0 = no truncation). Output uses the same `Detailed Results:` block.
Main text comes from `trafilatura`, with a bs4/markdownify fallback, a PDF text
path, and Wayback/AMP fallbacks when a site blocks the direct fetch.

## crawl / map

```bash
python $S map "https://docs.python.org/3/library/asyncio.html" --max-depth 2 --limit 25
python $S crawl "https://docs.python.org/3/library/asyncio.html" --max-depth 1 --max-breadth 5 \
  --instructions "pages about tasks and structured concurrency"
```

Shared flags, defaults: `--max-depth 1`, `--max-breadth 20`,
`--limit 50`, `--instructions`, `--select-paths` / `--select-domains` (regex,
comma-separated), `--allow-external true|false`. `crawl` adds `--extract-depth`,
`--format` and `--include-favicon`. Output: `Crawl Results:` / `Site Map Results:`
with `Base URL:` and the numbered `[n] URL:` list. Link ranking uses the sitemap, anchor text, the
`instructions`/query terms, and penalises version-switcher and sibling-subdomain
pages, so `map`/`crawl` stay on topic.

## research

```bash
python $S research "Compare SearXNG, Searx and self-hosted metasearch options." --model auto
```

`--model mini|pro|auto` (default `auto`) sets the budget: how many sub-queries,
engines and sources are gathered. It decomposes the question, searches and
extracts across engines, then writes a cited report (Executive Summary, themed
`###` sections, `[n]` inline citations, numbered Sources). Synthesis runs on the
local OpenAI-compatible endpoint — `http://127.0.0.1:8080/v1` by default,
overridable with `FWS_LLM_BASE_URL`. With no local LLM reachable it still returns
the ranked evidence dossier instead of failing.

## Engine reality — read this before judging a result set

- **FreeSerp (`index=web`) is the primary general engine**: a free, keyless web
  index with real snippets and dates, ~0.2 s when warm. It needs no token, and it
  is queried with plain parameters only — when its web engine is briefly
  unavailable the API silently answers from a *domain* index instead, and those
  rows are discarded rather than shown. **SearXNG is the co-primary**: public
  instances are probed concurrently (pool refreshed daily from searx.space), the
  batch whose results actually match the query wins, and per-instance quality is
  scored and persisted, so instances serving stale or empty pages get skipped.
  Expect ~2-5 s overall; a cold SearXNG pool can take ~20 s.
- **Bing's HTML SERP is poisoned on this network** (it returns results for
  unrelated queries), so it is deliberately not used for `search`. Bing *News*
  RSS and Bing image search are different endpoints and are used normally.
- `html.duckduckgo.com` is hard rate-limited from most hosts (202/timeout) and is
  only a deep-search filler; `--search-depth advanced` is where it gets tried.
- `--include-domains` cannot rely on `site:` in keyless SERPs (FreeSerp answers
  such a query with its domain index, which is dropped), so it leans on the
  target site's own search page / sitemap / Sphinx `searchindex.js` plus the
  official APIs (GitHub, arXiv, StackExchange, Wikipedia, HN, OpenAlex). Best for
  docs sites; weakest for blogs with no index.
- Google News RSS links are `news.google.com/rss/articles/<token>` redirects that
  are not resolvable offline. Bing News and SearXNG give the publisher URL, so
  once those fill the request the redirect rows are dropped entirely.
- Auto-generated SERP/social index pages (`/discover/`, `/ideas/`, `/tag/…`,
  `/trends/`, `/tools/…`), mirror front-ends and dictionary entries are demoted in
  ranking, because keyless engines surface them far more often than they should.
  A library's own documentation host (docs.python.org, MDN, man7,
  docs.rs, readthedocs, …) gets a small lift, so real documentation outranks thin
  aggregators; thin SERP snippets are replaced with a real excerpt from the page.
- `--select-paths` / `--select-domains` filter which pages are *returned*, not
  which pages are walked — the crawler still passes through index pages to reach
  them, and matching pages are visited first. Pages only appear within
  `--max-depth` hops of the base URL, so use `--max-depth 2` when the pages you
  want sit behind an index page (docs sites usually do).
- `Answer:` is only produced for `--search-depth advanced`: the local model
  synthesises 2-4 sentences from the retrieved excerpts with inline `[n]`
  citations, falling back to extractive sentences when no local model is up.
- If a query returns junk, re-run with `-v`, then try the distinctive phrase in
  double quotes, or `--search-depth advanced`, or narrow with `--include-domains`.

## Tuning knobs (environment)

- `FWS_CACHE_DIR` — disk cache location. Default: `%LOCALAPPDATA%\free-web-search\cache`
  on Windows, `~/free-web-search/cache` on macOS/Linux (it also holds
  `searx-quality.json`, the learned instance ranking)
- `FWS_CACHE_TTL` — cache seconds (default 900); `FWS_NO_CACHE=1` bypasses it
- `FWS_LLM_BASE_URL` — OpenAI-compatible base URL used by `research` and `Answer:`
  (probed in order: `$FWS_LLM_BASE_URL`, `http://127.0.0.1:8080/v1`,
  `http://127.0.0.1:1234/v1`); `FWS_LLM_MODEL` pins the model id

## Failure contract

Any hard error prints `free-web-search error: <reason>` plus a pointer to
`fws check` / the README troubleshooting section on stderr, and exits 1 — so a
caller can branch on the exit code instead of parsing prose.
