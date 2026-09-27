#!/usr/bin/env python3
"""
Internal bulk stress probe: random (or cross-wired) trigger × merchant × customer,
POST /v1/tick, flag weak outputs. Does not replace judge_simulator — finds template gaps.

Usage (bot must be running):
  python scripts/bulk_probe.py --runs 80
  python scripts/bulk_probe.py --runs 100 --crosswire --sparse-prob 0.35 --out probe.jsonl

Fast template-only path (optional):
  set LLM_API_KEY=  or use env without key for deterministic templates.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest

# Repo root on path for signals.*
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from signals import body_banned_internal, body_banned_vague, body_has_taboo  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_json(
    method: str,
    base: str,
    path: str,
    body: Optional[dict],
    timeout: float,
) -> Tuple[Optional[dict], Optional[str], float]:
    url = f"{base.rstrip('/')}{path}"
    t0 = time.time()
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urlrequest.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8")
        return json.loads(raw), None, (time.time() - t0) * 1000
    except urlerror.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8")), f"HTTP {e.code}", (time.time() - t0) * 1000
        except Exception:
            return None, f"HTTP {e.code}", (time.time() - t0) * 1000
    except Exception as e:
        return None, str(e), (time.time() - t0) * 1000


def load_categories(dataset_dir: Path) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    cat_dir = dataset_dir / "categories"
    for p in sorted(cat_dir.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        slug = data.get("slug") or p.stem
        out[slug] = data
    return out


def load_seed_merchants(dataset_dir: Path) -> List[dict]:
    raw = json.loads((dataset_dir / "merchants_seed.json").read_text(encoding="utf-8"))
    return list(raw.get("merchants") or [])


def load_seed_triggers(dataset_dir: Path) -> List[dict]:
    raw = json.loads((dataset_dir / "triggers_seed.json").read_text(encoding="utf-8"))
    return list(raw.get("triggers") or [])


def load_seed_customers(dataset_dir: Path) -> List[dict]:
    raw = json.loads((dataset_dir / "customers_seed.json").read_text(encoding="utf-8"))
    return list(raw.get("customers") or [])


def customers_for_merchant(customers: List[dict], merchant_id: str) -> List[dict]:
    return [c for c in customers if c.get("merchant_id") == merchant_id]


def push_context(base: str, scope: str, cid: str, version: int, payload: dict) -> Tuple[bool, str]:
    body = {
        "scope": scope,
        "context_id": cid,
        "version": version,
        "payload": payload,
        "delivered_at": _utc_now_iso(),
    }
    data, err, _ = _request_json("POST", base, "/v1/context", body, 30.0)
    if err and "HTTP" in err and data is None:
        return False, err
    if not data or not data.get("accepted"):
        return False, str(data or err or "rejected")
    return True, "ok"


def push_context_adaptive(base: str, scope: str, cid: str, payload: dict) -> Tuple[bool, str]:
    """Bump version when bot already holds the same context_id (long-lived uvicorn)."""
    version = 1
    for _ in range(24):
        body = {
            "scope": scope,
            "context_id": cid,
            "version": version,
            "payload": payload,
            "delivered_at": _utc_now_iso(),
        }
        data, err, _ = _request_json("POST", base, "/v1/context", body, 30.0)
        if data and data.get("accepted"):
            return True, "ok"
        if isinstance(data, dict) and data.get("reason") == "stale_version":
            cv = data.get("current_version")
            if cv is not None:
                version = int(cv) + 1
                continue
        return False, str(data or err or "rejected")
    return False, "stale_version_retry_exhausted"


def seed_store(base: str, categories: Dict[str, dict], merchants: List[dict], customers: List[dict]) -> None:
    for slug, cat in categories.items():
        ok, msg = push_context_adaptive(base, "category", slug, cat)
        if not ok:
            raise SystemExit(f"Failed to push category {slug}: {msg}")
    for m in merchants:
        mid = m.get("merchant_id")
        if not mid:
            continue
        ok, msg = push_context_adaptive(base, "merchant", mid, m)
        if not ok:
            raise SystemExit(f"Failed to push merchant {mid}: {msg}")
    for c in customers:
        cid = c.get("customer_id")
        if not cid:
            continue
        ok, msg = push_context_adaptive(base, "customer", cid, c)
        if not ok:
            raise SystemExit(f"Failed to push customer {cid}: {msg}")


def tick(base: str, trigger_ids: List[str], now_iso: str) -> Tuple[Optional[dict], Optional[str], float]:
    body = {"now": now_iso, "available_triggers": trigger_ids}
    return _request_json("POST", base, "/v1/tick", body, 60.0)


def sparse_dict(d: dict, rng: random.Random, drop_prob: float) -> dict:
    """Randomly drop top-level keys from payload (stress). Keeps at least one key if possible."""
    if not d or drop_prob <= 0:
        return copy.deepcopy(d)
    keys = list(d.keys())
    kept = {k: copy.deepcopy(d[k]) for k in keys if rng.random() > drop_prob}
    if not kept and keys:
        k = rng.choice(keys)
        kept[k] = copy.deepcopy(d[k])
    return kept


def synthetic_trigger(tr: dict, rng: random.Random) -> dict:
    t = copy.deepcopy(tr)
    suf = uuid.uuid4().hex[:10]
    t["id"] = f"bulk_{suf}"
    base_sk = (t.get("suppression_key") or t.get("kind") or "trg")[:40]
    t["suppression_key"] = f"bulk_probe:{base_sk}:{suf}"
    return t


def apply_crosswire(
    t: dict,
    merchant: dict,
    customers: List[dict],
    rng: random.Random,
    customer_prob: float,
) -> None:
    mid = merchant.get("merchant_id")
    t["merchant_id"] = mid
    cands = customers_for_merchant(customers, mid)
    if t.get("scope") == "customer" and cands and rng.random() < customer_prob:
        t["customer_id"] = rng.choice(cands).get("customer_id")
    else:
        t["customer_id"] = None
        if t.get("scope") == "customer":
            t["scope"] = "merchant"


def normalize_body(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def specificity_hints(body: str) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []
    if re.search(r"\d", body):
        score += 1
        reasons.append("has_digit")
    if "₹" in body or "%" in body:
        score += 1
        reasons.append("rupee_or_pct")
    if len(body.split()) >= 18:
        score += 1
        reasons.append("long_copy")
    return score, reasons


def taboos_for_merchant(categories: Dict[str, dict], merchant: dict) -> List[str]:
    slug = merchant.get("category_slug") or ""
    cat = categories.get(slug) or {}
    v = cat.get("voice") or {}
    return list(v.get("vocab_taboo") or v.get("taboos") or [])


def analyze_body(
    body: Optional[str],
    taboos: List[str],
    seen_bodies: Dict[str, int],
) -> List[str]:
    flags: List[str] = []
    if body is None:
        return ["no_body"]
    b = body.strip()
    if not b:
        flags.append("blank_body")
        return flags
    nb = normalize_body(b)
    seen_bodies[nb] = seen_bodies.get(nb, 0) + 1
    if seen_bodies[nb] > 1:
        flags.append("duplicate_output")
    if body_banned_internal(b):
        flags.append("awkward_internal_wording")
    if body_banned_vague(b):
        flags.append("awkward_vague_crm")
    if body_has_taboo(b, taboos):
        flags.append("category_taboo_hit")
    sc, _ = specificity_hints(b)
    if sc < 2:
        flags.append("low_specificity")
    return flags


def main() -> None:
    ap = argparse.ArgumentParser(description="Bulk POST /v1/tick stress probe")
    ap.add_argument("--bot-url", default=os.environ.get("BOT_URL", "http://127.0.0.1:8080"))
    ap.add_argument("--runs", type=int, default=80)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--crosswire", action="store_true", help="Random merchant unrelated to seed trigger row")
    ap.add_argument("--honor-links", action="store_true", help="Use each trigger's native merchant_id")
    ap.add_argument("--sparse-prob", type=float, default=0.0, help="Per-key drop prob for trigger payload")
    ap.add_argument("--customer-prob", type=float, default=0.4, help="If crosswired customer scope, attach random customer")
    ap.add_argument(
        "--now-iso",
        default=os.environ.get("BULK_PROBE_NOW", "2026-04-25T12:00:00Z"),
        help="Tick clock (must be before most expires_at in seed)",
    )
    ap.add_argument("--out", type=str, default="", help="Append JSONL results to this file")
    ap.add_argument("--skip-seed", action="store_true", help="Assume contexts already loaded in bot")
    args = ap.parse_args()

    if args.honor_links:
        args.crosswire = False
    elif not args.crosswire:
        args.crosswire = True

    rng = random.Random(args.seed)

    dataset_dir = ROOT / "dataset"
    categories = load_categories(dataset_dir)
    merchants = load_seed_merchants(dataset_dir)
    triggers = load_seed_triggers(dataset_dir)
    customers = load_seed_customers(dataset_dir)

    if not merchants or not triggers:
        raise SystemExit("Missing merchants or triggers in dataset/")

    base = args.bot_url.rstrip("/")
    print(f"[bulk_probe] bot={base} runs={args.runs} crosswire={args.crosswire} sparse_prob={args.sparse_prob}", flush=True)

    if not args.skip_seed:
        print("[bulk_probe] pushing all categories, merchants, customers…", flush=True)
        seed_store(base, categories, merchants, customers)
    else:
        print("[bulk_probe] --skip-seed: not pushing contexts", flush=True)

    hz, err, _ = _request_json("GET", base, "/v1/healthz", None, 10.0)
    if err or not hz:
        raise SystemExit(f"healthz failed: {err} {hz}")

    seen_bodies: Dict[str, int] = {}
    summary_flags: Dict[str, int] = {}
    rows_out: List[dict] = []

    for i in range(args.runs):
        tr_base = rng.choice(triggers)
        if args.crosswire:
            merchant = rng.choice(merchants)
        else:
            mid = tr_base.get("merchant_id")
            merchant = next((m for m in merchants if m.get("merchant_id") == mid), rng.choice(merchants))

        t = synthetic_trigger(tr_base, rng)
        if args.crosswire:
            apply_crosswire(t, merchant, customers, rng, args.customer_prob)

        if args.sparse_prob > 0 and isinstance(t.get("payload"), dict):
            t["payload"] = sparse_dict(t["payload"], rng, args.sparse_prob)

        tid = t["id"]
        ok, msg = push_context(base, "trigger", tid, 1, t)
        if not ok:
            row = {
                "run": i + 1,
                "error": f"context_trigger:{msg}",
                "synthetic_trigger_id": tid,
                "trigger_kind": t.get("kind"),
            }
            rows_out.append(row)
            for f in ("context_push_failed",):
                summary_flags[f] = summary_flags.get(f, 0) + 1
            continue

        data, err2, lat = tick(base, [tid], args.now_iso)
        if err2 and not data:
            row = {
                "run": i + 1,
                "error": f"tick:{err2}",
                "synthetic_trigger_id": tid,
                "trigger_kind": t.get("kind"),
            }
            rows_out.append(row)
            summary_flags["tick_http_error"] = summary_flags.get("tick_http_error", 0) + 1
            continue

        actions = (data or {}).get("actions") or []
        if not actions:
            row = {
                "run": i + 1,
                "trigger_kind": t.get("kind"),
                "trigger_base_id": tr_base.get("id"),
                "synthetic_trigger_id": tid,
                "merchant_id": merchant.get("merchant_id"),
                "customer_id": t.get("customer_id"),
                "sparse": args.sparse_prob > 0,
                "flags": ["no_action"],
                "body": None,
                "template_name": None,
                "latency_ms": round(lat, 1),
            }
            for f in row["flags"]:
                summary_flags[f] = summary_flags.get(f, 0) + 1
            rows_out.append(row)
            continue

        act = actions[0]
        body = act.get("body")
        taboos = taboos_for_merchant(categories, merchant)
        flags = analyze_body(body, taboos, seen_bodies)
        if act.get("template_name") == "vera_generic_v1":
            flags.append("default_template")
        row = {
            "run": i + 1,
            "trigger_kind": t.get("kind"),
            "trigger_base_id": tr_base.get("id"),
            "synthetic_trigger_id": tid,
            "merchant_id": merchant.get("merchant_id"),
            "merchant_locality": (merchant.get("identity") or {}).get("locality"),
            "category_slug": merchant.get("category_slug"),
            "customer_id": t.get("customer_id"),
            "sparse": args.sparse_prob > 0,
            "template_name": act.get("template_name"),
            "flags": flags,
            "specificity_score": specificity_hints(body or "")[0],
            "body": body,
            "latency_ms": round(lat, 1),
            "error": None,
        }
        for f in flags:
            summary_flags[f] = summary_flags.get(f, 0) + 1
        rows_out.append(row)

        if (i + 1) % 10 == 0:
            print(f"[bulk_probe] completed {i + 1}/{args.runs}", flush=True)

    def row_has_issue(r: dict) -> bool:
        fl = r.get("flags")
        if fl:
            return True
        if r.get("error"):
            return True
        return False

    bad = sum(1 for r in rows_out if row_has_issue(r))
    print("\n=== SUMMARY ===", flush=True)
    print(f"runs: {args.runs}  rows_with_issues: {bad}", flush=True)
    for k, v in sorted(summary_flags.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}", flush=True)

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for r in rows_out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nWrote {len(rows_out)} lines to {p}", flush=True)


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    main()
