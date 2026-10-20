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


_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)</script>')
_ROW_RE = re.compile(r"^([0-9a-z]+):(.*)$", re.S)


def _iter_flight_rows(html: str) -> Iterable[Any]:
    """Yield every JSON-decodable RSC flight row found in the page HTML.

    Next.js streams the flight payload as `self.__next_f.push([1,"<js string>"])`.
    One push may carry several rows separated by real newlines; each row looks like
    `<hex-id>:<json>`. Both the chunk-level string and the per-row JSON are decoded
    defensively, so a single malformed row no longer aborts the whole extraction.
    """
    for match in _PUSH_RE.finditer(html):
        try:
            decoded = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(decoded, str):
            continue
        for raw_row in decoded.split("\n"):
            row = raw_row.strip()
            if not row:
                continue
            body = None
            mm = _ROW_RE.match(row)
            if mm:
                body = mm.group(2)
            for candidate in (body, row):
                if not candidate:
                    continue
                try:
                    yield json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue


def _looks_like_model_list(lst: Any) -> bool:
    if not isinstance(lst, list) or len(lst) < 10:
        return False
    head = lst[0]
    if not isinstance(head, dict):
        return False
    # 新版载荷用 initialModels(slug + intelligenceIndex),
    # 旧版载荷用 models(slug + modelCreatorId),两者都认。
    return "slug" in head and ("intelligenceIndex" in head or "modelCreatorId" in head)


def extract_models_from_page(html: str) -> list[dict[str, Any]]:
    """Return the richest model list embedded in the leaderboard page.

    AA 改版(2026-10)后,表格数据挂在 `initialModels` 上,而模型表里不再带
    `modelCreatorId`。这里同时兼容 old/new 两种载荷,并取最长的那份列表
    (页面里还有 modelsAndReleases 的变体索引等短列表,需要排除)。
    """
    best: list[dict[str, Any]] | None = None
    for obj in _iter_flight_rows(html):
        for node in iter_nested(obj):
            if not isinstance(node, dict):
                continue
            for key in ("initialModels", "models"):
                lst = node.get(key)
                if not _looks_like_model_list(lst):
                    continue
                if best is None or len(lst) > len(best):
                    best = lst
    if best is None:
        raise RuntimeError("Could not locate detailed llm models payload in page HTML")
    return clean_value(best)


def extract_release_index(html: str) -> dict[str, Any]:
    """Build slug -> release metadata (releaseDate / creator slug / logo) index.

    新版表格列表不再逐条带 releaseDate,发布日期只在 `modelsAndReleases` 里:
      releases: [{slug, name, deprecated, releaseDate, creator{slug,name,logo}}]
      models  : [{slug, name, releaseSlug}]   # 变体 slug -> 版本 slug
    """
    releases: dict[str, Any] = {}
    variant_map: dict[str, str] = {}
    for obj in _iter_flight_rows(html):
        for node in iter_nested(obj):
            if not isinstance(node, dict):
                continue
            mr = node.get("modelsAndReleases")
            if not isinstance(mr, dict):
                continue
            for rel in mr.get("releases") or []:
                if isinstance(rel, dict) and rel.get("slug"):
                    releases[rel["slug"]] = rel
            for var in mr.get("models") or []:
                if isinstance(var, dict) and var.get("slug") and var.get("releaseSlug"):
                    variant_map[var["slug"]] = var["releaseSlug"]
    return {"releases": releases, "variants": variant_map}


def _eval_total_tokens(eval_token_counts: Any) -> int | None:
    """Sum input+output tokens across all eval tasks (proxy for eval thoroughness)."""
    if not isinstance(eval_token_counts, dict):
        return None
    total = 0
    for task in eval_token_counts.values():
        if isinstance(task, dict):
            total += (task.get("inputTokens") or 0) + (task.get("outputTokens") or 0)
    return total


def normalize(model: dict[str, Any], release_index: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize one AA row into this repo's schema (tolerant of both AA payload shapes)."""
    index = release_index or {}
    releases = index.get("releases") or {}
    variants = index.get("variants") or {}
    slug = model.get("slug")
    release = releases.get(slug) or releases.get(variants.get(slug) or "") or {}
    rel_creator = release.get("creator") or {}

    eval_tokens = _eval_total_tokens(model.get("evalTokenCounts"))
    is_estimated = model.get("intelligenceIndexIsEstimated")

    return {
        "id": model.get("id") or slug,
        "name": model.get("name"),
        "shortName": model.get("shortName") or model.get("name"),
        "slug": slug,
        "releaseDate": model.get("releaseDate") or release.get("releaseDate"),
        "isReasoning": model.get("isReasoning"),
        "deprecated": model.get("deprecated", False) or release.get("deprecated", False),
        "creator": {
            "name": model.get("modelCreatorName") or rel_creator.get("name"),
            "slug": model.get("modelCreatorSlug") or rel_creator.get("slug"),
            "country": model.get("modelCreatorCountry"),
            "color": model.get("modelCreatorColor"),
            "logo": model.get("modelCreatorLogo") or rel_creator.get("logo"),
        },
        "evaluations": {
            "intelligenceIndex": model.get("intelligenceIndex"),
            "intelligenceIndexIsEstimated": is_estimated,
            # AA 2026-10 改版后表格载荷不再含 codingIndex / agenticIndex,
            # 保持字段存在(值为 None),前端据此自动隐藏对应列并重新归一综合分。
            "codingIndex": model.get("codingIndex"),
            "agenticIndex": model.get("agenticIndex"),
            "terminalbenchHard": model.get("terminalbenchHard"),
            # 新版字段名是 terminalBench21/terminalBench40(注意大小写)
            "terminalbenchV21": model.get("terminalbenchV21") or model.get("terminalBench21"),
            "terminalBench40": model.get("terminalBench40"),
            "tau2": model.get("tau2"),
            "tauBanking": model.get("tauBanking"),
            "scicode": model.get("scicode"),
            "lcr": model.get("lcr"),
            "omniscience": model.get("omniscience"),
            "omniscienceAccuracy": model.get("omniscienceAccuracy"),
            "omniscienceNonHallucination": model.get("omniscienceNonHallucination"),
            "ifbench": model.get("ifbench"),
            "hle": model.get("hle"),
            "gpqa": model.get("gpqa"),
            "critpt": model.get("critpt"),
            "apexAgents": model.get("apexAgents"),
            "itbenchSre": model.get("itbenchSre"),
            "gdpvalNormalized": model.get("gdpvalNormalized"),
            "mmmuPro": model.get("mmmuPro"),
        },
        # evalTokenCounts 在新载荷里也没有了,此时给 None 而不是 0,
        # 让前端区分「没有这项数据」与「评测规模为 0」。
        "evalStats": (
            {"totalTokens": eval_tokens, "isEstimated": is_estimated}
            if eval_tokens is not None
            else None
        ),
        "pricing": {
            "price1mInputTokens": model.get("price1mInputTokens"),
            "price1mOutputTokens": model.get("price1mOutputTokens"),
            "price1mBlended3To1": model.get("price1mBlended0To3To1"),
            "cacheHitPrice": model.get("cacheHitPrice"),
            "cacheWritePrice": model.get("cacheWritePrice"),
        },
        "performance": {
            "medianOutputTokensPerSecond": model.get("medianOutputTokensPerSecond"),
            "medianTimeToFirstTokenSeconds": model.get("medianTimeToFirstTokenSeconds"),
            "medianReasoningTimeSeconds": model.get("medianReasoningTimeSeconds"),
        },
        "contextWindowTokens": model.get("contextWindowTokens"),
        "totalParameters": model.get("totalParameters"),
        "activeParameters": model.get("activeParameters"),
        "paramClass": model.get("paramClass"),
        "priceClass": model.get("priceClass"),
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


RETENTION_DAYS = 180


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
        entry["last_seen"] = now
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

    # 清理长期不再出现的模型,但保留宽限期:
    # AA 新版表格不再返回 deprecated 模型,若按「不在本轮榜单就删」会一次性抹掉
    # 数百条资历档案;改为 last_seen 超过 RETENTION_DAYS 才清。
    cur_slugs = {m.get("slug") for m in models if m.get("slug")}
    kept: dict[str, Any] = {}
    for slug, entry in stab["models"].items():
        if slug in cur_slugs:
            kept[slug] = entry
            continue
        entry.setdefault("last_seen", entry.get("first_seen") or now)
        try:
            age_days = (datetime.fromisoformat(now) - datetime.fromisoformat(entry["last_seen"])).days
        except Exception:
            age_days = 0
        if age_days <= RETENTION_DAYS:
            kept[slug] = entry
    stab["models"] = kept

    with open(stab_path, "w", encoding="utf-8") as f:
        json.dump(stab, f, ensure_ascii=False)


MIN_EXPECTED_MODELS = 50


def main() -> int:
    print(f"[fetch] GET {PAGE_URL}")
    html = http_get(PAGE_URL).decode("utf-8", "ignore")
    print(f"[fetch] page bytes: {len(html)}")
    raw_models = extract_models_from_page(html)
    print(f"[fetch] raw models: {len(raw_models)}")
    if len(raw_models) < MIN_EXPECTED_MODELS:
        # 防呆:载荷结构漂移时宁可整轮失败也不要写坏快照
        raise RuntimeError(
            f"Only {len(raw_models)} models extracted (< {MIN_EXPECTED_MODELS}); refusing to write snapshot"
        )

    release_index = extract_release_index(html)
    print(f"[fetch] release index: {len(release_index['releases'])} releases, "
          f"{len(release_index['variants'])} variants")

    models = [normalize(m, release_index) for m in raw_models]
    now = datetime.now(timezone.utc).isoformat()

    n = len(models) or 1
    coverage = {
        "releaseDate": sum(1 for m in models if m.get("releaseDate")),
        "codingIndex": sum(1 for m in models if m["evaluations"].get("codingIndex") is not None),
        "agenticIndex": sum(1 for m in models if m["evaluations"].get("agenticIndex") is not None),
        "terminal": sum(
            1
            for m in models
            if m["evaluations"].get("terminalbenchV21") is not None
            or m["evaluations"].get("terminalbenchHard") is not None
        ),
        "evalStats": sum(1 for m in models if (m.get("evalStats") or {}).get("totalTokens")),
    }
    print("[fetch] field coverage: " + ", ".join(f"{k}={v}/{len(models)}" for k, v in coverage.items()))


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
                    "payload": "initialModels" if raw_models and "modelCreatorId" not in raw_models[0] else "models",
                    "field_coverage": coverage,
                    "note": (
                        "AA 2026-10 改版后表格载荷不再包含 codingIndex/agenticIndex/evalTokenCounts,"
                        "综合分按可用维度自动重新归一;deprecated 模型不再出现在源页面中。"
                    ),
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
