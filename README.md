# free-web-search

Free web **search / extract / crawl / map / research** for AI agents — one Python
CLI plus an [Agent Skills](https://agentskills.dev) manifest, installable into
Claude Code, DeepSeek Harness, OpenAI Codex, Hermes Agent, OpenCode, Cursor,
OpenClaw, or any agent that can run a shell command.

| | |
|---|---|
| **API key** | none — ever |
| **Cost / quota** | zero; every endpoint is free and public |
| **Runtime** | Python 3.9+ and five pip packages |
| **Works offline-ish** | no: it needs internet, but no account, no token, no billing |
| **License** | MIT |

It gives an agent the five web verbs it actually needs:

| verb | what it does |
|---|---|
| `search` | ranked web results from a pool of free engines, deduplicated and re-ranked |
| `extract` | readable main text of one or more URLs (Markdown), incl. PDFs and Wayback fallbacks |
| `crawl` | walk a site from a base URL and return page content |
| `map` | enumerate a site's URLs (sitemap + BFS) before you decide what to read |
| `research` | decompose a question, gather across engines, return a cited report |
| `check` | self-test: dependencies, every engine, the local LLM |

Everything is local and transparent: `-v` prints which engines answered, `--json`
gives machine-readable output, and the disk cache is plain files you can delete.

---

## Contents

1. [Requirements](#requirements)
2. [Install in 60 seconds](#install-in-60-seconds)
3. [Install it by talking to your agent](#install-it-by-talking-to-your-agent)
4. [Per-harness install paths](#per-harness-install-paths)
5. [What this skill does — and how it works under the hood](#what-this-skill-does--and-how-it-works-under-the-hood)
6. [Verify the install](#verify-the-install)
7. [Teach the agent to use it by default](#teach-the-agent-to-use-it-by-default)
8. [Usage reference](#usage-reference)
9. [How it works (and what "free" costs you)](#how-it-works-and-what-free-costs-you)
10. [Environment variables](#environment-variables)
11. [Troubleshooting](#troubleshooting)
12. [Politeness, privacy, and limits](#politeness-privacy-and-limits)
13. [Update and uninstall](#update-and-uninstall)
14. [Repository layout](#repository-layout)

---

## Requirements

- **Python 3.9+** (3.8 technically parses — the code uses `from __future__ import
  annotations` — but 3.9+ is what is supported; developed and tested on 3.12).
  `python`, `python3` or `py` on `PATH` all work.
- **Five pip packages** ([requirements.txt](requirements.txt)):
  `requests`, `beautifulsoup4`, `lxml`, `trafilatura`, `markdownify`.
  Optional: `pypdf` for PDF text in `extract` — the import is guarded, so without
  it a PDF simply yields no text instead of crashing.
- **No API key, no signup, no paid service.** The engines are free public
  endpoints: FreeSerp's free web index, public SearXNG instances, Wikipedia,
  GitHub, Hacker News, StackExchange, OpenAlex, arXiv, Baidu, Bing News RSS,
  Google News RSS, Bing image search, DuckDuckGo's HTML endpoint, and a site's own
  search page / sitemap / Sphinx `searchindex.js`.
- **Optional, and only for `research` + `Answer:`**: a local OpenAI-compatible
  LLM endpoint (LM Studio, llama.cpp server, Ollama's `/v1`, vLLM, …). Without
  one, `research` still returns a ranked evidence dossier and `Answer:` falls back
  to extractive sentences. Nothing is sent anywhere except the search engines.

---

## Install in 60 seconds

Pick the block that matches your OS. `--deps` / `-Deps` installs the pip packages;
without it the skill still runs if the packages are already present.

### Linux / macOS / WSL / Git Bash

```bash
git clone https://github.com/OWNER/websearch_free.git   # replace OWNER, or use a local path
cd websearch_free
chmod +x install.sh
./install.sh --deps          # auto-detects your agent's skill directory
```

### Windows (PowerShell 7+)

```powershell
git clone https://github.com/OWNER/websearch_free.git   # replace OWNER, or use a local path
cd websearch_free
.\install.ps1 -Deps          # auto-detects your agent's skill directory
```

Both installers do the same four things:

1. copy `SKILL.md` + `scripts/` into `<skill-root>/free-web-search/`
   (auto-detecting the first skill root that exists — see the table below),
2. optionally `pip install -r requirements.txt`,
3. run `fws.py check` and a live `search` smoke test **from the installed copy**,
4. tell you to restart the agent so it re-scans its skill roots.

One harness-specific detail: a root ending in `.hermes/skills` is automatically
nested to `.hermes/skills/tools/free-web-search`, because Hermes expects
`<category>/<skill>/SKILL.md`. Everything else installs flat.

Useful installer flags (identical in both scripts):

| `install.sh` | `install.ps1` | meaning |
|---|---|---|
| `--dest DIR` | `-Dest DIR` | install into `DIR/free-web-search` (repeatable) |
| `--project [DIR]` | `-Project DIR` | install into `DIR/.agents/skills/free-web-search` (project-local) |
| `--all` | `-All` | install into **every** recognised skill root that exists |
| `--link` | `-Link` | symlink / junction instead of a copy — edit the clone, agents see it live |
| `--deps` | `-Deps` | `python -m pip install -r requirements.txt` |
| `--no-check` | `-NoCheck` | skip the post-install health check |
| `--dry-run` | `-DryRun` | print what would happen, change nothing |
| `-h` | — | help |

`install.ps1` also takes `-Python <path>` if your interpreter is not on `PATH`;
`install.sh` honours `PYTHON=/path/to/python`.

---

## Install it by talking to your agent

This repo is written so an agent can install itself. Paste the whole block into
Claude Code, Codex, DeepSeek Harness, Hermes, OpenCode, or any agent with shell
access, and it will clone, install, verify, and wire itself up. Replace `OWNER`
with the repo owner (or point it at a local path you already cloned).

````text
Install the free-web-search skill into yourself. Do it step by step and report the
output of each step. No API key is involved; if anything asks for one, stop.

1. Get the code (skip if this checkout is already here):
     git clone https://github.com/OWNER/websearch_free.git && cd websearch_free

2. Install the skill into your own skill directory. Find the right root for your
   harness from README.md "Per-harness install paths", then run the installer:
     ./install.sh --deps --dest <your-skill-root>        # bash
     .\install.ps1 -Deps -Dest <your-skill-root>         # PowerShell
   If your harness has no skill system, skip the installer and note the absolute
   path of scripts/fws.py instead.

3. Verify with the skill's own health check (must report engines healthy):
     python <skill-root>/free-web-search/scripts/fws.py check
   Then prove a real lookup works:
     python <skill-root>/free-web-search/scripts/fws.py search "what is a SearXNG instance" --max-results 3 -v

4. Make it your default web path: append the contents of AGENTS.snippet.md to your
   instruction file (AGENTS.md / CLAUDE.md / ~/.dsh/AGENTS.md / ~/.codex/AGENTS.md),
   replacing <SKILL_DIR> with the installed skill directory.

5. Tell me: the installed path, the `check` summary line (engines healthy N/M), the
   local LLM line if any, and that a restart is needed for the skill to be picked up.

Do not add any paid or key-based web service to anything. Only the free engines in
this repo.
````

PowerShell-flavoured variant of step 2 for Windows agents:

```powershell
git clone https://github.com/OWNER/websearch_free.git; cd websearch_free
.\install.ps1 -Deps -Dest "$env:USERPROFILE\.agents\skills"
python "$env:USERPROFILE\.agents\skills\free-web-search\scripts\fws.py" check
```

---

## Per-harness install paths

The installers know these roots; pass `--dest` / `-Dest` to be explicit, or copy
the folder by hand — a skill is just a directory with `SKILL.md` at its top.

| Harness | Skill root (user-level) | Project-level | Notes |
|---|---|---|---|
| **Claude Code** | `~/.claude/skills/free-web-search/` | `<repo>/.claude/skills/`, nested `<subdir>/.claude/skills/` | also picks up plugin skills; symlinked skill dirs work; `--add-dir <dir>` makes `<dir>/.claude/skills` visible. Avoid the reserved names `synced` and `anthropic-skills`. |
| **DeepSeek Harness (DSH)** | `~/.dsh/skills/` or `~/.agents/skills/` | `<projectRoot>/.dsh/skills/`, `<projectRoot>/.agents/skills/` | extra dirs via `customSkillDirs` in config; bundled skills ship with the harness. |
| **OpenAI Codex** | `~/.codex/skills/` | `<repo>/.codex/skills/`, and `.agents/skills/` at repo/parent/user scope in current builds | `agents/openai.yaml` is Codex-only metadata; this skill does not need it. |
| **Hermes Agent (Nous)** | `~/.hermes/skills/<category>/free-web-search/` | — | `hermes skills install <src>` accepts `owner/repo/skill`, `skills-sh/…`, `well-known:https://…`, or a direct URL. **URL/GitHub installs copy only the files SKILL.md explicitly references** under `references/ templates/ scripts/ assets/ examples/` — which is why SKILL.md has a [Files] section listing every script. Lock file: `skills/.hub/lock.json`. Use `--category tools`. |
| **OpenCode** | `~/.config/opencode/skills/` (also reads `~/.claude/skills`, `~/.agents/skills`) | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` | extra sources via the `skills` array in `opencode.json(c)`; the frontmatter `name` must equal the directory name, 1–64 chars, lowercase-hyphen — `free-web-search` already does. |
| **Cursor** | `~/.cursor/skills/` | `.cursor/skills/` | |
| **OpenClaw** | `~/.openclaw/skills/` | `.openclaw/skills/` | |
| **Any agent following the Agent Skills spec** | `~/.agents/skills/` | `.agents/skills/` | the portable convention; this is the installer's default fallback. |
| **An agent with a shell but no skill system** | — | — | clone anywhere, then paste [AGENTS.snippet.md](AGENTS.snippet.md) into its instruction file with the absolute path to `scripts/fws.py`. |
| **An MCP-only harness** | — | — | run [scripts/mcp_server.py](scripts/mcp_server.py) as an MCP stdio server (see below). |

### Hermes, explicitly

```bash
# recommended: plain copy, whole bundle (the installer nests it under tools/ for you)
./install.sh --dest ~/.hermes/skills        # → ~/.hermes/skills/tools/free-web-search/

# or let Hermes fetch it (only files named in SKILL.md are copied — they all are)
hermes skills install OWNER/websearch_free/free-web-search --category tools
```

### MCP-only harnesses (optional shim)

If your harness speaks MCP but has no skill system, expose the same five verbs as
MCP tools (`web_search`, `web_extract`, `web_crawl`, `web_map`, `web_research`)
with a stdio server that has **no extra dependencies**:

```json
{
  "mcpServers": {
    "free-web-search": {
      "command": "python",
      "args": ["/absolute/path/to/free-web-search/scripts/mcp_server.py"]
    }
  }
}
```

DeepSeek Harness equivalent (`~/.dsh/config.json` → `mcp.servers`):

```json
{ "name": "free-web-search", "command": "python",
  "args": ["/absolute/path/to/free-web-search/scripts/mcp_server.py"] }
```

Handshake check without an MCP client (three JSON lines in, three out):

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"web_search","arguments":{"query":"SearXNG","search_depth":"ultra-fast"}}}' \
 | python /absolute/path/to/free-web-search/scripts/mcp_server.py
```

The skill (SKILL.md + CLI) is still the recommended integration: no server
process, no protocol, and the agent can read the engine-reality notes.

---

## What this skill does — and how it works under the hood

**What it is.** A skill is two things: a `SKILL.md` manifest your agent loads into
context (name, description, when to use it, the exact commands, the gotchas), and a
folder of code it points at. Here the code is one Python CLI, `scripts/fws.py`,
with five web verbs — `search`, `extract`, `crawl`, `map`, `research` — plus a
`check` self-test. Nothing runs as a service, nothing holds a key, and the agent
drives it the same way it drives `git`: run a command, read stdout, branch on the
exit code.

**What it buys you over a raw `curl`.** A single query is fanned out over a pool of
free public engines instead of one, and the answers are merged into one ranked,
deduplicated list with real excerpts — so the agent gets usable, citable sources in
one command, and `-v` shows which engines actually answered.

**Under the hood, `search` in six steps:**

1. **Fan-out.** `--search-depth` picks how many engines run
   (`ultra-fast` 2 → `fast` 3 → `basic` 5 → `advanced` 8). FreeSerp's free index
   and a pool of public SearXNG instances lead; Wikipedia, GitHub, Hacker News,
   StackExchange, OpenAlex, arXiv, Baidu and news RSS fill in the gaps. SearXNG
   instances are probed concurrently.
2. **Site-scoped search when you ask for it.** `--include-domains` does not rely on
   a `site:` operator (keyless engines answer that with a junk domain-index page
   that gets thrown away). Instead the tool queries the target site's own search
   page, its sitemap, or its Sphinx `searchindex.js`, plus the official APIs.
3. **Dedupe and re-rank.** URLs are normalised and collapsed, then scored by query
   relevance times a per-engine prior. Auto-generated SERP/social index pages
   (`/discover/`, `/ideas/`, `/tag/…`), mirror front-ends and dictionary entries are
   demoted; canonical documentation hosts (docs.python.org, MDN, man7.org, docs.rs,
   readthedocs, …) get a lift, so real docs outrank blog coverage.
4. **Deepening (advanced only).** Thin SERP snippets are replaced by a real excerpt
   fetched from the page, and an `Answer:` line is synthesised with inline `[n]`
   citations.
5. **Cache.** Every response is stored on disk for 15 minutes, so retries, `-v`
   re-runs and follow-up calls cost nothing and add no load on the engines.
6. **Learned instance quality.** Public SearXNG instances are scored per host and
   the scores persist in `searx-quality.json`; the pool is refreshed daily from
   searx.space and instances below `0.12` are skipped. That is what keeps a
   free, third-party pool from serving junk the second week in a row.

**`extract`** fetches a URL and returns readable Markdown: `trafilatura` for main
text, a `bs4`/`markdownify` fallback, a PDF path, and Wayback/AMP fallbacks when a
site blocks the direct fetch. **`map`** enumerates a site (sitemap first, then
breadth-first HTML links, filtered by regex) so the agent can see the shape of a
site before spending requests on it; **`crawl`** is `map` plus extraction, bounded
by `--max-depth` / `--max-breadth` / `--limit`. **`research`** decomposes a
question into sub-queries, runs the search/extract pipeline across engines, and
returns a cited report — written by a local OpenAI-compatible LLM if one is
reachable, otherwise returned as a ranked evidence dossier so it never hard-fails.

**Contract the agent can rely on:** stable plain-text blocks (`Answer:` /
`Detailed Results:` / `Title:` / `URL:` / `Content:` …) or `--json`; progress on
stderr only; exit `0` success, `1` hard failure with
`free-web-search error: <reason>`, `2` bad usage, `130` interrupted.

The full engine table, priors and tuning knobs are in
[How it works (and what "free" costs you)](#how-it-works-and-what-free-costs-you).

---

## Verify the install

```bash
python <skill-dir>/scripts/fws.py check
```

Real output from a healthy machine:

```text
free-web-search health check
==============================================================
  [ok]      python package: trafilatura
  [ok]      python package: bs4
  [ok]      python package: lxml
  [ok]      python package: markdownify
  [ok]      python package: requests
--------------------------------------------------------------
  [ok]      engine freeserp: 5 results
           e.g. https://www.testmuai.com/blog/python-asyncio/
  [ok]      engine searx: 5 results
           e.g. https://www.python.org/
  [ok]      engine site-index: 5 results
           e.g. https://docs.python.org/3/library/asyncio-api-index.html
  [ok]      engine hackernews: 5 results
  [ok]      engine wikipedia: 3 results
  [ok]      engine github: 5 results
  [ok]      engine stackexchange: 5 results
  [warn]      engine duckduckgo: 0 results
  [ok]      engine openalex: 5 results
  [ok]      engine arxiv: 5 results
  [ok]      engine baidu: 2 results
  [ok]      engine bing-news: 5 results
  [ok]      engine google-news: 5 results
--------------------------------------------------------------
  local LLM for research synthesis: http://127.0.0.1:8080/v1 (qwen3.8-flash-next-iq3_s)
==============================================================
engines healthy: 12/13   cache: %LOCALAPPDATA%/free-web-search/cache
```

`[warn] engine duckduckgo: 0 results` is **expected** on most hosts: DuckDuckGo's
HTML endpoint hard rate-limits datacenter/agent traffic (HTTP 202 or timeout). It
is only a deep-search filler, so 12/13 is a healthy score. `check` exits 0 unless
a dependency is missing.

Then prove the whole path works end to end:

```bash
S=<skill-dir>/scripts/fws.py
python $S search "asyncio task groups python 3.12" -v          # ranked results + engine telemetry
python $S extract "https://docs.python.org/3/library/asyncio-task.html" --max-chars 1200
python $S map   "https://docs.python.org/3/library/asyncio.html" --max-depth 2 --limit 20
python $S crawl "https://docs.python.org/3/library/asyncio.html" --max-depth 1 --max-breadth 5
python $S research "Compare self-hosted metasearch options." --model mini
```

---

## Teach the agent to use it by default

Installing the files is half the job; the other half is telling the agent this is
its **default** web path, so it does not fall back to a built-in or key-based tool
the first time a result looks thin. Append [AGENTS.snippet.md](AGENTS.snippet.md)
to whichever instruction file your harness reads:

| Harness | Instruction file |
|---|---|
| Claude Code | `CLAUDE.md` (project) or `~/.claude/CLAUDE.md` |
| DeepSeek Harness | `AGENTS.md` (project) or `~/.dsh/AGENTS.md` |
| OpenAI Codex | `AGENTS.md` (project) or `~/.codex/AGENTS.md` |
| OpenCode | `AGENTS.md`, or `instructions` in `opencode.json(c)` |
| Hermes Agent | its system/user instruction file |
| anything else | whatever global instruction file it already reads |

Replace `<SKILL_DIR>` with the installed directory. The snippet is short on
purpose: default to this tool, add `-v` when results look thin, retry with
`--search-depth advanced` / a quoted phrase / `--include-domains` before declaring
failure, and never substitute a key-based web API.

---

## Usage reference

Set the path once per shell, then every command is `python "$S" <verb> …`
(use `python3` on macOS/Linux if `python` is not on `PATH`):

```bash
S="$HOME/.agents/skills/free-web-search/scripts/fws.py"     # bash
```
```powershell
$S = "$env:USERPROFILE\.agents\skills\free-web-search\scripts\fws.py"   # PowerShell
```

### Global flags (accepted before *or* after the subcommand)

| flag | meaning |
|---|---|
| `--timeout N` | per-request timeout seconds (default 20) |
| `--verbose`, `-v` | progress on **stderr**: which engines answered, timings, cache hits. Add it whenever a result set looks thin |
| `--json` | structured payload instead of the plain-text block |

### search

```bash
python $S search "asyncio vs threading python"
python $S search "openai announcement" --topic news --time-range week --max-results 8
python $S search "paddle ocr" --include-domains github.com --include-raw-content
python $S search "what is retrieval augmented evaluation" --search-depth advanced -v
```

| flag | values / default |
|---|---|
| `--search-depth` | `ultra-fast` (2 engines) · `fast` (3) · `basic` (5, default) · `advanced` (8 + page fetches + synthesized `Answer:`) |
| `--topic` | `general` (default) · `news` (swaps in the news engine set) |
| `--time-range` | `day` · `week` · `month` · `year` |
| `--start-date` / `--end-date` | `YYYY-MM-DD`; overrides `--time-range` |
| `--max-results` | 5–20 (default 5) |
| `--include-domains` / `--exclude-domains` | comma-separated |
| `--include-images` / `--include-image-descriptions` | image block |
| `--include-raw-content` | full extracted page text per result |
| `--include-favicon` | `Favicon:` line per result |
| `--country` | full name, e.g. `"United States"` |
| `--exact-match` | require the phrase |

Text output shape:

```text
Answer: …                                  # advanced depth only
Detailed Results:

Title: …
ID: eafa73-00
URL: https://…
Content: …
Raw Content: …                             # --include-raw-content
Favicon: https://…                         # --include-favicon

Images:

[1] URL: https://…
   Description: …
```

### extract

```bash
python $S extract "https://example.com/a" "https://example.com/b"
python $S extract --urls-file links.txt --query "rate limiting" --max-chars 4000
python $S extract "https://arxiv.org/pdf/2507.07188v3" --extract-depth advanced
```

URLs positionally, or `--url` / `--urls-csv` / `--urls-file`. Flags:
`--extract-depth basic|advanced`, `--format markdown|text`, `--include-images`,
`--include-favicon`, `--query` (re-ranks extracted chunks), `--max-chars`
(0 = no truncation). Main text comes from `trafilatura`, with a
`bs4`/`markdownify` fallback, a PDF path, and Wayback/AMP fallbacks when a site
blocks the direct fetch.

### crawl / map

```bash
python $S map   "https://docs.python.org/3/library/asyncio.html" --max-depth 2 --limit 25
python $S crawl "https://docs.python.org/3/library/asyncio.html" --max-depth 1 --max-breadth 5 \
  --instructions "pages about tasks and structured concurrency"
```

Shared flags: `--max-depth 1`, `--max-breadth 20`, `--limit 50`, `--instructions`,
`--select-paths` / `--select-domains` (regex, comma-separated),
`--allow-external true|false`. `crawl` adds `--extract-depth`, `--format`,
`--include-favicon`. `map` first (cheap), then `crawl` the URLs you want.

### research

```bash
python $S research "Compare SearXNG, Searx and self-hosted metasearch options." --model auto
```

`--model mini|pro|auto` (default `auto`) sets the budget: how many sub-queries,
engines and sources are gathered. It decomposes the question, searches and
extracts across engines, then writes a cited report (Executive Summary, themed
`###` sections, `[n]` inline citations, numbered Sources). Synthesis uses the
local OpenAI-compatible endpoint; with none reachable it returns the ranked
evidence dossier instead of failing.

### check

`python $S check` — dependency import test, one live query per engine with a
sample URL from each, and the local-LLM probe. Exit 0 unless a dependency is
missing.

### Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | hard failure — `free-web-search error: <reason>` on stderr, plus a pointer to `check` |
| 2 | bad command-line usage |
| 130 | interrupted (Ctrl-C) |

---

## How it works (and what "free" costs you)

**Engine pool, in the order they are tried** (the third number is the ranking
prior, independent of order — see `ENGINES` in
[scripts/fws/engines.py](scripts/fws/engines.py)):

| engine | prior | notes |
|---|---|---|
| `freeserp` | 1.00 | free keyless web index; real snippets and dates; ~0.2 s warm |
| `searx` | 0.96 | public SearXNG instances, probed concurrently; pool refreshed daily from searx.space; per-instance quality scored and persisted |
| `duckduckgo` | 0.90 | HTML endpoint, hard rate-limited from most hosts → deep-search filler only |
| `site-index` | 0.80 | the target site's own search page / sitemap / Sphinx `searchindex.js` |
| `hackernews` | 0.78 | Algolia HN search API |
| `wikipedia` | 0.74 | REST search |
| `github` | 0.72 | repo/code search API (unauthenticated) |
| `stackexchange` | 0.70 | StackExchange API |
| `openalex` | 0.66 | scholarly metadata |
| `arxiv` | 0.64 | preprints |
| `baidu` | 0.55 | CJK coverage |
| news set (`--topic news`) | | `bing-news` 1.00, `searx` 0.92, `google-news` 0.80, `duckduckgo` 0.72, `hackernews` 0.66 |

- **Depth selects breadth**: `DEPTH_ENGINES = {ultra-fast: 2, fast: 3, basic: 5,
  advanced: 8}`. `advanced` also fetches each page to replace thin SERP snippets
  with a real excerpt and synthesizes the `Answer:` line.
- **SearXNG quality is learned**: instance scores are persisted in
  `searx-quality.json` inside the cache dir; instances below `0.12` are skipped
  until the daily refresh re-probes them. That is what keeps a public-instance
  pool from serving you junk.
- **Bing's HTML SERP is deliberately excluded**: on the networks this was built on
  an upstream proxy poisons it, returning results for queries nobody asked. Bing
  *News* RSS and Bing image search are different endpoints and are used normally.
- **Ranking is not just SERP order**: results are deduplicated by URL, re-scored by
  query relevance, demoted for auto-generated SERP/social index pages
  (`/discover/`, `/ideas/`, `/tag/…`, `/trends/`), mirror front-ends and dictionary
  entries, and lifted for canonical documentation hosts (docs.python.org, MDN,
  man7.org, docs.rs, readthedocs, …).
- **`--include-domains` does not rely on `site:`**: keyless SERPs answer such a
  query with a domain index that gets discarded, so the skill instead queries the
  target site's own search page / sitemap / `searchindex.js` plus the official
  APIs. Excellent on documentation sites; weakest on blogs with no index.
- **Latency**: ~2–5 s for a normal `basic` search; a cold SearXNG pool can take
  ~20 s; `ultra-fast` is usually under 1 s. Results are cached on disk for 15 min
  so retries are free.

---

## Environment variables

| variable | default | meaning |
|---|---|---|
| `FWS_CACHE_DIR` | `%LOCALAPPDATA%\free-web-search\cache` (Windows), `~/free-web-search/cache` (macOS/Linux) | disk cache; also holds `searx-quality.json` |
| `FWS_CACHE_TTL` | `900` | cache seconds |
| `FWS_NO_CACHE` | unset | `1` bypasses the cache entirely |
| `FWS_LLM_BASE_URL` | unset | OpenAI-compatible base URL for `research` and `Answer:`. Probe order: `$FWS_LLM_BASE_URL`, `http://127.0.0.1:8080/v1`, `http://127.0.0.1:1234/v1` |
| `FWS_LLM_MODEL` | auto-detected | pins the model id |
| `FWS_TIMEOUT` | `30` | per-request timeout used by the MCP shim (the CLI uses `--timeout`) |
| `FWS_VERBOSE` | unset | `1` enables engine telemetry in the MCP shim |
| `PYTHON` | `python3`/`python` | interpreter used by `install.sh` |

---

## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `ModuleNotFoundError: trafilatura` (or bs4/lxml/markdownify) | deps not installed | `python -m pip install -r requirements.txt`, or re-run the installer with `--deps` / `-Deps`. In a venv, install into the venv the agent actually runs |
| `engine searx: 0 results` everywhere | public instances blocked or the pool went stale | re-run with `-v`; delete `searx-quality.json` in the cache dir to force a re-probe; set `FWS_NO_CACHE=1` to skip cached junk; expect the daily refresh from searx.space |
| `engine duckduckgo: 0 results` in `check` | hard rate limiting (202/timeout) | expected; ignore it — 12/13 is healthy |
| results are off-topic | SERP filler from a weak engine | re-run with `-v`, put the distinctive phrase in double quotes, add `--search-depth advanced`, or narrow with `--include-domains` |
| `--include-domains` returns nothing | the site has no searchable index | crawl it instead: `map` then `crawl` with `--select-paths` |
| `map`/`crawl` return too few pages | pages sit behind an index page | `--max-depth 2`; remember `--select-paths` filters what is *returned*, not what is walked |
| `research` returns a dossier, not a report | no local LLM reachable | start an OpenAI-compatible server, or set `FWS_LLM_BASE_URL`; the dossier is still usable evidence |
| `Answer:` missing | only produced at `--search-depth advanced` | add the flag |
| slow first search | cold SearXNG pool | wait it out, or use `--search-depth ultra-fast` for a quick pass |
| mojibake / `UnicodeEncodeError` on Windows | console codepage | the CLI forces UTF-8 on stdout; if you pipe into another tool, set `PYTHONIOENCODING=utf-8` |
| `python` not found | interpreter not on PATH | `install.ps1 -Python <path>`, or `PYTHON=<path> ./install.sh` |
| skill not picked up | the harness scanned its roots before install | restart the agent / start a new session; confirm the dir contains `SKILL.md` at its top |
| Hermes install is missing files | URL/GitHub installs copy only files named in SKILL.md | use the `## Files` section (it names every script), or install with `./install.sh --dest ~/.hermes/skills` |

---

## Politeness, privacy, and limits

- The tool sends your query string to public search endpoints — the same thing a
  browser does. Do not search for secrets, credentials, or private data.
- It is rate-limit aware: per-host pacing, retries with backoff, a 15-minute disk
  cache, and a concurrency cap on SearXNG probes. Do not disable the cache to
  hammer public instances.
- Respect the sites you `extract`/`crawl`: keep `--max-depth` and `--limit` small,
  honour robots intent, and prefer `map` (cheap) over `crawl` (expensive).
- No telemetry, no analytics, no phoning home: the only outbound requests are the
  search engines, the pages you ask for, and (optionally) your own local LLM.

---

## Update and uninstall

```bash
# update (a copy install; re-run the installer to overwrite the skill dir)
git pull && ./install.sh            # or: .\install.ps1

# live-editing: install once as a symlink/junction, then just git pull
./install.sh --link                 # or: .\install.ps1 -Link

# uninstall
rm -rf ~/.agents/skills/free-web-search          # the skill dir you installed into
Remove-Item -Recurse -Force "$env:USERPROFILE\.agents\skills\free-web-search"   # PowerShell
# optional: pip uninstall trafilatura markdownify lxml beautifulsoup4
# optional: delete the cache dir (see Environment variables)
```

Uninstalling the skill does not remove the clone; the clone is also directly
runnable (`python scripts/fws.py …`) if you prefer a path-based setup.

---

## Repository layout

```text
websearch_free/
├── SKILL.md              # the Agent Skills manifest the harness reads
├── README.md             # this file
├── AGENTS.snippet.md     # paste-ready "use this by default" instruction block
├── install.sh            # Linux/macOS/WSL installer
├── install.ps1           # Windows PowerShell installer
├── requirements.txt      # 5 required + 1 optional pip package
├── LICENSE               # MIT
├── .gitignore
└── scripts/
    ├── fws.py            # CLI entry point: search|extract|crawl|map|research|check
    ├── mcp_server.py     # optional dependency-free MCP stdio shim
    └── fws/
        ├── __init__.py
        ├── core.py       # HTTP session, disk cache, ranking, chunking, LLM probe
        ├── engines.py    # the free engines + result aggregation
        ├── extract.py    # URL → readable text (trafilatura, PDF, Wayback/AMP)
        ├── crawl.py      # crawl / map traversal, site-scoped search
        └── research.py   # multi-source research pipeline
```

`SKILL.md` frontmatter carries `name`, `description`, `whenToUse`, and
`metadata.{script,requires,cost,repo}` — the fields the Agent Skills loaders use.
`metadata.repo` is a placeholder until you push the repo; update it to your remote.

## License

MIT — see [LICENSE](LICENSE).
