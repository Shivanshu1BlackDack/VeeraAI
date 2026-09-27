#!/usr/bin/env python3
"""Build submission.jsonl from expanded/test_pairs.json using deterministic compose (no LLM)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from composer import compose_message  # noqa: E402


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    expanded = ROOT / "expanded"
    pairs_path = expanded / "test_pairs.json"
    if not pairs_path.exists():
        print("Run: python scripts/expand_dataset.py first", file=sys.stderr)
        return 1

    data = load_json(pairs_path)
    pairs = data.get("pairs") or []

    categories = {}
    for f in (expanded / "categories").glob("*.json"):
        d = load_json(f)
        categories[d["slug"]] = d

    out_lines = []
    for p in pairs:
        tid = p["trigger_id"]
        mid = p["merchant_id"]
        cid = p.get("customer_id")
        tr_path = expanded / "triggers" / f"{tid}.json"
        m_path = expanded / "merchants" / f"{mid}.json"
        if not tr_path.exists() or not m_path.exists():
            continue
        trigger = load_json(tr_path)
        merchant = load_json(m_path)
        slug = merchant.get("category_slug")
        category = categories.get(slug, {})
        customer = None
        if cid:
            cp = expanded / "customers" / f"{cid}.json"
            if cp.exists():
                customer = load_json(cp)

        # Deterministic template path (no API key)
        import os

        os.environ.pop("LLM_API_KEY", None)
        result = compose_message(
            category,
            merchant,
            trigger,
            customer,
            use_llm=False,
            now_iso="2026-05-02T12:00:00+05:30",
        )
        line = {
            "test_id": p.get("test_id"),
            "body": result.get("body"),
            "cta": result.get("cta"),
            "send_as": result.get("send_as"),
            "suppression_key": result.get("suppression_key"),
            "rationale": result.get("rationale"),
        }
        out_lines.append(json.dumps(line, ensure_ascii=False))

    sub_path = ROOT / "submission.jsonl"
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + ("\n" if out_lines else ""))
    print(f"Wrote {len(out_lines)} lines to {sub_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
