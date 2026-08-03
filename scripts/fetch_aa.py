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

    print(f"[fetch] wrote {llms_path} ({llms_path.stat().st_size/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
