"""Deep multi-source research, keyless.

Pipeline: decompose -> search many sub-queries -> extract evidence -> synthesise.
Synthesis uses a local OpenAI-compatible endpoint when one is reachable
(FWS_LLM_BASE_URL, else probed localhost/LAN candidates); otherwise it emits a
cited evidence dossier that the calling agent can reason over directly.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse

from .core import (FetchError, Http, chunk_text, host_of, now_iso, relevance, strip_tags,
                   tokenize)
from .engines import SearchOpts, favicon_url, search

MODEL_BUDGET = {"mini": 300, "pro": 900, "auto": 420}
MODEL_PLAN = {
    "mini": {"subqueries": 4, "per_query": 5, "sources": 6, "depth": "basic",
             "extract_depth": "basic", "chars_per_source": 2600},
    "auto": {"subqueries": 6, "per_query": 5, "sources": 10, "depth": "advanced",
             "extract_depth": "advanced", "chars_per_source": 3200},
    "pro": {"subqueries": 9, "per_query": 8, "sources": 16, "depth": "advanced",
            "extract_depth": "advanced", "chars_per_source": 4200},
}
LLM_CANDIDATES = [
    os.environ.get("FWS_LLM_BASE_URL", ""),
    "http://127.0.0.1:8080/v1",
    "http://127.0.0.1:1234/v1",
    "http://10.147.17.169:1234/v1",
]
LLM_MODEL = os.environ.get("FWS_LLM_MODEL", "")

ANGLES = [
    "{q}", "{q} latest developments", "{q} analysis", "{q} statistics data",
    "{q} criticism limitations", "{q} examples case study", "{q} how it works",
    "{q} comparison alternatives", "{q} future outlook predictions",
]


def _llm_endpoint(http: Http) -> tuple[str, str] | None:
    for base in LLM_CANDIDATES:
        if not base:
            continue
        try:
            r = http.get(base.rstrip("/") + "/models", ttl=300, min_interval=0.0,
                         timeout=3.0, attempts=1)
            data = json.loads(r["text"])
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            if models:
                return base.rstrip("/"), LLM_MODEL or models[0]
        except Exception:
            continue
    return None


def _llm_chat(http: Http, base: str, model: str, prompt: str, *, max_tokens: int = 1400,
              temperature: float = 0.3) -> str | None:
    """Chat with a local OpenAI-compatible endpoint.

    Reasoning-style local models can spend the entire budget on their chain of
    thought and answer with an empty ``content``, so reasoning is switched off
    first (two server dialects) and only then is a plain, larger request tried.
    Chain-of-thought text is never returned as the answer.
    """
    attempts = (
        ({"reasoning_effort": "none"}, max_tokens),
        ({"chat_template_kwargs": {"enable_thinking": False}}, max_tokens),
        ({}, max_tokens * 3),
    )
    for extra, budget in attempts:
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature, "max_tokens": budget, "stream": False}
        body.update(extra)
        try:
            r = http.post(f"{base}/chat/completions", json=body,
                          headers={"Content-Type": "application/json"}, ttl=0,
                          min_interval=0.0, timeout=240.0, attempts=1)
            data = json.loads(r["text"])
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if text:
                return text
        except Exception:
            continue
    return None


STOPWORDS = {
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "is", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "can", "could", "should", "would", "will", "shall", "may", "might", "must",
    "have", "has", "had", "the", "a", "an", "of", "to", "in", "on", "for",
    "and", "or", "but", "not", "with", "about", "into", "from", "between",
    "vs", "versus", "it", "its", "this", "that", "these", "those", "there",
    "here", "as", "at", "by", "if", "then", "than", "so", "such", "some",
    "any", "all", "more", "most", "much", "many", "main", "best", "top",
    "new", "latest", "please", "give", "tell", "me", "you", "your", "my",
    "we", "our", "they", "their", "him", "her", "his", "she", "get", "gets",
    "make", "makes", "made", "use", "used", "using", "way", "ways", "thing",
    "things", "really", "just", "like", "know", "want", "need", "help",
}


def _key_terms(query: str) -> list[str]:
    """Content words of the question — the seeds for sub-query variants."""
    out, seen = [], set()
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9+.\-_]{2,}", query):
        low = tok.lower()
        if low in STOPWORDS or low in seen:
            continue
        seen.add(low)
        out.append(low)
    return out[:6]


def _entities(query: str) -> list[str]:
    caps = re.findall(r"\b[A-Z][A-Za-z0-9&.\-]{2,}(?:\s+[A-Z][A-Za-z0-9&.\-]{2,}){0,3}\b", query)
    out, seen = [], set()
    for c in caps:
        c = c.strip()
        if c.lower() in STOPWORDS:
            continue
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out[:4]


def _llm_subqueries(http: Http, query: str, n: int, llm) -> list[str]:
    """Ask the local model to split the question into distinct search queries."""
    if not llm or n < 2:
        return []
    base, lmodel = llm
    prompt = (
        f"Split this research question into {n} short, distinct web search queries "
        f"(3-8 words each). Different angles: definitions, comparisons, numbers, "
        f"recent news, expert opinion. Output one query per line, no numbering, "
        f"no commentary.\n\nQuestion: {query}"
    )
    text = _llm_chat(http, base, lmodel, prompt, max_tokens=200, temperature=0.2)
    if not text:
        return []
    keep = set(_key_terms(query))
    out = []
    for line in re.split(r"[\r\n]+", text):
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip().strip('"')
        # reject drifting rewrites: a usable sub-query must keep a content term
        if 3 <= len(line) <= 90 and (set(tokenize(line)) & keep):
            out.append(line)
    return out[:n]


def _subqueries(http: Http, query: str, n: int, llm=None) -> list[str]:
    out, seen = [], set()

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", s).strip()
        if s and len(s) <= 120 and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)

    add(query)
    for sq in _llm_subqueries(http, query, n - 1, llm):
        add(sq)
    terms = _key_terms(query)
    # keyword-pair variants keep breadth when no LLM decomposition is available
    for i in range(len(terms)):
        for j in range(i + 1, len(terms)):
            if len(out) >= n:
                break
            add(f"{terms[i]} {terms[j]}")
    for ent in _entities(query):
        if len(out) >= n:
            break
        add(ent)
    for a in ANGLES[1:]:
        if len(out) >= n:
            break
        add(a.format(q=query))
    return out[:n]


def _gather(http: Http, query: str, plan: dict, budget_end: float, log, llm=None) -> tuple:
    """Return (evidence, source_index)."""
    sources: dict[str, dict] = {}
    subs = _subqueries(http, query, plan["subqueries"], llm)
    log(f"sub-queries: {subs}")
    for i, sq in enumerate(subs):
        if time.time() > budget_end:
            log("time budget reached during search phase")
            break
        opts = SearchOpts(sq, search_depth=plan["depth"], max_results=plan["per_query"])
        try:
            res = search(http, opts, log=lambda m: log(f"  [{sq}] {m}"))
        except Exception as exc:  # noqa: BLE001
            log(f"  [{sq}] search failed: {exc}")
            continue
        for r in res.get("results", []):
            url = r["url"]
            sc = r.get("score", 0.5) + (0.05 if r.get("engines") and len(r["engines"]) > 1 else 0)
            if url not in sources or sc > sources[url]["score"]:
                sources[url] = {"url": url, "title": r.get("title") or host_of(url),
                                "snippet": r.get("content", ""), "score": sc,
                                "via": [sq]}
            else:
                sources[url]["via"].append(sq)
        log(f"[{i + 1}/{len(subs)}] {sq} -> {len(sources)} unique sources")

    # re-score against the original question: sub-query hits drift, and the
    # report must answer what was actually asked.
    for s in sources.values():
        own = relevance(query, s.get("title", ""), s.get("snippet", ""), s["url"])
        s["_own"] = own
        s["score"] = 0.55 * s["score"] + 0.45 * own
    ranked = sorted(sources.values(), key=lambda s: s["score"], reverse=True)
    # Junk guard: keyless engines answer with off-topic filler right next to real
    # hits, and a report built from a stray row is worse than one with fewer sources.
    on_topic = [s for s in ranked if s["_own"] >= 0.12]
    if len(on_topic) >= 3:
        ranked = on_topic
    # keep domain diversity: at most 3 pages per host
    picked, per_host = [], {}
    for s in ranked:
        h = host_of(s["url"])
        if per_host.get(h, 0) >= 3:
            continue
        per_host[h] = per_host.get(h, 0) + 1
        picked.append(s)
        if len(picked) >= plan["sources"]:
            break
    if len(picked) < plan["sources"]:
        for s in ranked:
            if s not in picked:
                picked.append(s)
                if len(picked) >= plan["sources"]:
                    break

    from .extract import extract_one
    evidence: list[dict] = []
    for i, s in enumerate(picked):
        if time.time() > budget_end:
            log("time budget reached during extraction phase")
            break
        got = extract_one(http, s["url"], fmt="markdown", depth=plan["extract_depth"],
                          query=query, max_chars=plan["chars_per_source"])
        if not got or not got.get("content"):
            evidence.append({"url": s["url"], "title": s["title"],
                             "content": s["snippet"], "score": s["score"]})
        else:
            evidence.append({"url": s["url"], "title": got.get("title") or s["title"],
                             "content": got["content"], "score": s["score"]})
        log(f"evidence[{i + 1}/{len(picked)}] {s['url']}")
    for s in picked:
        s.pop("_own", None)
    return evidence, picked


def _extractive_summary(evidence: list[dict], query: str, limit: int = 900) -> str:
    qtok = set(tokenize(query))
    scored = []
    for i, ev in enumerate(evidence):
        for sent in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", ev["content"])):
            sent = sent.strip()
            if len(sent) < 60 or len(sent) > 320:
                continue
            stok = set(tokenize(sent))
            if not stok:
                continue
            overlap = len(qtok & stok) / max(len(qtok), 1)
            bonus = 0.15 if ev["score"] > 0.6 else 0.0
            scored.append((overlap + bonus - 0.002 * i, sent, ev["url"]))
    scored.sort(reverse=True)
    out, seen = [], set()
    for _, sent, url in scored:
        key = sent.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{sent} [{len(out) + 1}]")
        if sum(len(o) for o in out) > limit:
            break
    return "\n\n".join(out)


def _dossier(query: str, evidence: list[dict], plan: dict, model: str, elapsed: float,
             note: str = "no local LLM endpoint was reachable") -> str:
    lines = [f"# Research Report: {query}", "",
             f"Generated {now_iso()} — keyless pipeline, model tier `{model}`, "
             f"{len(evidence)} sources, {elapsed:.1f}s.", "",
             "## Executive Summary", "", _extractive_summary(evidence, query) or
             "(no extractive summary available)", "", "## Evidence by Source", ""]
    for i, ev in enumerate(evidence, start=1):
        body = re.sub(r"\s+", " ", ev["content"]).strip()
        lines.append(f"### [{i}] {ev['title']}")
        lines.append(f"Source: {ev['url']} (relevance {ev['score']:.2f})")
        lines.append("")
        lines.append(body[:1400])
        lines.append("")
    lines.append("## Sources")
    lines.append("")
    for i, ev in enumerate(evidence, start=1):
        lines.append(f"{i}. [{ev['title']}]({ev['url']}) — {host_of(ev['url'])}")
    lines.append("")
    lines.append("---")
    lines.append(f"Synthesis note: {note}, so this report is a cited evidence dossier. "
                 "Rank, reconcile and answer from the evidence above.")
    return "\n".join(lines)


def _synthesise(http: Http, query: str, evidence: list[dict], llm, model: str) -> str | None:
    if not llm:
        return None
    base, lmodel = llm
    corpus, used, ids = [], 0, []
    for i, ev in enumerate(evidence, start=1):
        block = f"[{i}] {ev['title']} — {ev['url']}\n{re.sub(chr(10), ' ', ev['content'])[:1800]}"
        if used + len(block) > 24000:
            break
        used += len(block)
        corpus.append(block)
        ids.append(str(i))
    prompt = (
        f"Write a thorough research report answering: {query}\n\n"
        "Rules: use ONLY the numbered sources below; cite them inline as [n]; "
        "start with a 2-3 sentence executive summary; then themed sections with markdown "
        "headings; end with a 'Sources' list mapping [n] to URL. Be specific, include numbers, "
        "dates and named entities found in the sources.\n\nSOURCES:\n" + "\n\n".join(corpus)
    )
    text = _llm_chat(http, base, lmodel, prompt, max_tokens=2000)
    if text:
        return text + f"\n\n_Report synthesised by local model `{lmodel}` from " \
                      f"{len(corpus)} sources; no paid API used._"
    return None


def research(http: Http, input_text: str, model: str = "auto", *, log=None) -> dict:
    log = log or (lambda *a: None)
    model = (model or "auto").lower()
    if model not in MODEL_PLAN:
        model = "auto"
    plan = MODEL_PLAN[model]
    started = time.time()
    budget_end = started + MODEL_BUDGET[model]
    log(f"research model={model} budget={MODEL_BUDGET[model]}s")

    llm = _llm_endpoint(http)
    log(f"local LLM: {llm[1] if llm else 'none (heuristic split + evidence dossier)'}")

    evidence, _ = _gather(http, input_text, plan, budget_end, log, llm)
    if not evidence:
        return {"ok": False, "error": "no evidence gathered — engines unavailable or query "
                                      "too narrow", "content": "", "sources": []}

    content = _synthesise(http, input_text, evidence, llm, model)
    if not content:
        note = ("the local model returned no usable text" if llm
                else "no local LLM endpoint was reachable")
        content = _dossier(input_text, evidence, plan, model, time.time() - started, note)
    return {"ok": True, "content": content, "model": model,
            "sources": [{"url": e["url"], "title": e["title"], "favicon": favicon_url(e["url"])}
                        for e in evidence],
            "elapsed": round(time.time() - started, 1), "synthesis": "llm" if llm else "extractive"}
