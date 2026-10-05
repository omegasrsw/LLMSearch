# Paste this block into your agent's instruction file
# (AGENTS.md / CLAUDE.md / ~/.dsh/AGENTS.md / ~/.codex/AGENTS.md / opencode instructions)
# and replace <SKILL_DIR> with where you installed the skill, e.g.
#   ~/.agents/skills/free-web-search        (Linux/macOS)
#   $HOME\.agents\skills\free-web-search    (Windows)

## Web search — default tool: `free-web-search` (free, keyless)

For any web task — searching, "look this up online", checking something current,
reading a specific URL, crawling or enumerating a site, or producing a researched
report — use the `free-web-search` skill.

Entry point: `python <SKILL_DIR>/scripts/fws.py <subcommand> …`
Subcommands: `search`, `extract`, `crawl`, `map`, `research`, `check`.

Rules:
- No API key, no quota, no cost. Free public engines only (FreeSerp, SearXNG,
  Wikipedia, GitHub, Hacker News, StackExchange, arXiv, OpenAlex, news RSS, …).
- Add `-v` whenever a result set looks thin, so you can see which engines answered.
- Before concluding the search failed, retry with `--search-depth advanced`,
  a quoted distinctive phrase, `--include-domains`, or `--topic news`.
- Prefer `extract` over guessing URLs, and `map` before `crawl` on an unfamiliar site.
- Do not use any paid or key-based web API for these tasks; this skill covers them.
- Health check: `python <SKILL_DIR>/scripts/fws.py check` (deps + every engine + local LLM).
