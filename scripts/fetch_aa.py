#!/usr/bin/env python3
"""
Fetch Artificial Analysis LLM leaderboard data from the public page payload.

Source : https://artificialanalysis.ai/leaderboards/models  (public web page, no API key required)
Output : data/llms.json   (normalized model list + meta)
         data/latest.json (pointer with fetched_at)

Data provided by Artificial Analysis — attribution required: https://artificialanalysis.ai/
"""

from __future__ import annotations

import json
import math
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PAGE_URL = "https://artificialanalysis.ai/leaderboards/models"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


def http_get(url: str, accept: str = "text/html,*/*;q=0.8") -> bytes:
    req = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def clean_value(value: Any) -> Any:
    """Turn RSC '$undefined' markers into None and strip unneeded wrappers."""
    if value == "$undefined":
        return None
    if isinstance(value, dict):
        return {k: clean_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_value(v) for v in value]
    return value


def iter_nested(obj: Any) -> Iterable[Any]:
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from iter_nested(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_nested(v)


def extract_models_from_page(html: str) -> list[dict[str, Any]]:
    pattern = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)</script>')
    for match in pattern.finditer(html):
        try:
            decoded = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if '"models":' not in decoded or ":" not in decoded:
            continue
        payload = decoded.split(":", 1)[1]
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for node in iter_nested(obj):
            if not isinstance(node, dict):
                continue
            models = node.get("models")
            if isinstance(models, list) and models and isinstance(models[0], dict) and "modelCreatorId" in models[0]:
                return clean_value(models)
    raise RuntimeError("Could not locate detailed llm models payload in page HTML")


def _eval_total_tokens(eval_token_counts: Any) -> int | None:
    """Sum input+output tokens across all eval tasks (proxy for eval thoroughness)."""
    if not isinstance(eval_token_counts, dict):
        return None
    total = 0
    for task in eval_token_counts.values():
        if isinstance(task, dict):
            total += (task.get("inputTokens") or 0) + (task.get("outputTokens") or 0)
    return total


def normalize(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": model.get("id"),
        "name": model.get("name"),
        "shortName": model.get("shortName") or model.get("name"),
        "slug": model.get("slug"),
        "releaseDate": model.get("releaseDate"),
        "isReasoning": model.get("isReasoning"),
        "deprecated": model.get("deprecated", False),
        "creator": {
            "name": model.get("modelCreatorName"),
            "slug": model.get("modelCreatorSlug"),
            "country": model.get("modelCreatorCountry"),
            "color": model.get("modelCreatorColor"),
        },
        "evaluations": {
            "intelligenceIndex": model.get("intelligenceIndex"),
            "intelligenceIndexIsEstimated": model.get("intelligenceIndexIsEstimated"),
            "codingIndex": model.get("codingIndex"),
            "agenticIndex": model.get("agenticIndex"),
            "terminalbenchHard": model.get("terminalbenchHard"),
            "terminalbenchV21": model.get("terminalbenchV21"),
            "tau2": model.get("tau2"),
            "tauBanking": model.get("tauBanking"),
            "scicode": model.get("scicode"),
            "lcr": model.get("lcr"),
            "omniscience": model.get("omniscience"),
            "omniscienceAccuracy": model.get("omniscienceAccuracy"),
            "ifbench": model.get("ifbench"),
            "hle": model.get("hle"),
            "gpqa": model.get("gpqa"),
            "critpt": model.get("critpt"),
            "apexAgents": model.get("apexAgents"),
            "itbenchSre": model.get("itbenchSre"),
            "gdpvalNormalized": model.get("gdpvalNormalized"),
            "mmmuPro": model.get("mmmuPro"),
        },
        "evalStats": {
            "totalTokens": _eval_total_tokens(model.get("evalTokenCounts")),
            "isEstimated": model.get("intelligenceIndexIsEstimated"),
        },
        "pricing": {
            "price1mInputTokens": model.get("price1mInputTokens"),
            "price1mOutputTokens": model.get("price1mOutputTokens"),
            "price1mBlended3To1": model.get("price1mBlended0To3To1"),
        },
        "performance": {
            "medianOutputTokensPerSecond": model.get("medianOutputTokensPerSecond"),
            "medianTimeToFirstTokenSeconds": model.get("medianTimeToFirstTokenSeconds"),
        },
        "contextWindowTokens": model.get("contextWindowTokens"),
        "totalParameters": model.get("totalParameters"),
        "activeParameters": model.get("activeParameters"),
        "isOpenWeights": model.get("isOpenWeights"),
        "commercialAllowed": model.get("commercialAllowed"),
        "licenseName": model.get("licenseName"),
        "huggingfaceUrl": model.get("huggingfaceUrl"),
        "openrouterApiId": model.get("openrouterApiId"),
    }


def _composite(model: dict[str, Any]) -> float | None:
    """Same composite formula as the front-end (int 35% / coding 25% / agentic 20% / terminal 20%)."""
    ev = model["evaluations"]
    t = ev.get("terminalbenchV21")
    if t is None:
        t = ev.get("terminalbenchHard")
    t100 = t * 100 if isinstance(t, (int, float)) else None
    dims = [
        (ev.get("intelligenceIndex"), 0.35),
        (ev.get("codingIndex"), 0.25),
        (ev.get("agenticIndex"), 0.20),
        (t100, 0.20),
    ]
    s = w = 0.0
    for v, wt in dims:
        if v is not None and isinstance(v, (int, float)) and math.isfinite(v):
            s += v * wt
            w += wt
    return s / w if w > 0 else None


def update_stability(models: list[dict[str, Any]], now: str) -> None:
    """Maintain data/stability.json: per-model composite-score volatility + first-seen date.
    Compared against the previous snapshot (data/llms.json) to measure rank stability."""
    stab_path = DATA_DIR / "stability.json"
    stab: dict[str, Any] = {"updated_at": now, "models": {}}
    if stab_path.exists():
        try:
            stab = json.loads(stab_path.read_text(encoding="utf-8"))
        except Exception:
            stab = {"updated_at": now, "models": {}}
    stab["updated_at"] = now  # 每次运行都刷新时间戳
    stab.setdefault("models", {})

    prev: dict[str, dict[str, Any]] = {}
    llms_path = DATA_DIR / "llms.json"
    if llms_path.exists():
        try:
            for m in json.loads(llms_path.read_text(encoding="utf-8"))["models"]:
                prev[m.get("slug")] = m
        except Exception:
            prev = {}

    for m in models:
        slug = m.get("slug")
        if not slug:
            continue
        cur = _composite(m)
        if cur is None:
            continue
        entry = stab["models"].setdefault(slug, {"deltas": [], "first_seen": now, "changes": 0})
        if slug in prev:
            pc = _composite(prev[slug])
            if pc is not None:
                d = round(abs(cur - pc), 2)
                deltas = list(entry.get("deltas") or [])
                deltas.append(d)
                entry["deltas"] = deltas[-6:]  # keep last 6 snapshots
                if d > 0.5:
                    entry["changes"] = int(entry.get("changes", 0)) + 1
        else:
            # first time this model appears in our snapshots
            if not entry.get("deltas"):
                entry["deltas"] = []
            if not entry.get("first_seen"):
                entry["first_seen"] = now

    # prune models that disappeared from the leaderboard
    cur_slugs = {m.get("slug") for m in models if m.get("slug")}
    stab["models"] = {k: v for k, v in stab["models"].items() if k in cur_slugs}

    with open(stab_path, "w", encoding="utf-8") as f:
        json.dump(stab, f, ensure_ascii=False)


def main() -> int:
    print(f"[fetch] GET {PAGE_URL}")
    html = http_get(PAGE_URL).decode("utf-8", "ignore")
    print(f"[fetch] page bytes: {len(html)}")
    raw_models = extract_models_from_page(html)
    print(f"[fetch] raw models: {len(raw_models)}")

    models = [normalize(m) for m in raw_models]
    now = datetime.now(timezone.utc).isoformat()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    llms_path = DATA_DIR / "llms.json"
    with open(llms_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "source": PAGE_URL,
                    "fetched_at": now,
                    "model_count": len(models),
                    "attribution": "Data provided by Artificial Analysis — https://artificialanalysis.ai/",
                },
                "models": models,
            },
            f,
            ensure_ascii=False,
        )
    with open(DATA_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump({"fetched_at": now, "model_count": len(models)}, f, ensure_ascii=False)

    update_stability(models, now)
    print(f"[fetch] wrote {llms_path} ({llms_path.stat().st_size/1024/1024:.1f} MB) + stability.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
