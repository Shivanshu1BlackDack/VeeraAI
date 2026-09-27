"""Vera bot — FastAPI service with /v1/context, /tick, /reply, /healthz, /metadata."""

from __future__ import annotations

import copy
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, Field

load_dotenv()

from composer import compose_message
from conversation import handle_reply, init_conversation_hook
from signals import build_facts_pack, is_trigger_expired
from state import Store

app = FastAPI(title="Vera Bot", version=os.environ.get("BOT_VERSION", "1.0.0"))


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int = Field(ge=1)
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


class TickBody(BaseModel):
    now: str
    available_triggers: List[str] = Field(default_factory=list)


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str
    received_at: Optional[str] = None
    turn_number: int = 1


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@app.get("/v1/healthz")
async def healthz():
    store = Store.get()
    return {
        "status": "ok",
        "uptime_seconds": store.uptime_seconds(),
        "contexts_loaded": store.count_by_scope(),
    }


def _team_members_from_env(raw: Optional[str]) -> List[str]:
    """JSON array, comma-separated names, or single name."""
    import json

    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            out = json.loads(s)
            return [str(x).strip() for x in out if str(x).strip()]
        except Exception:
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


@app.get("/v1/metadata")
async def metadata():
    members_raw = os.environ.get("BOT_TEAM_MEMBERS", "Shivanshu Sharma")
    ml = _team_members_from_env(members_raw)
    if not ml:
        ml = ["Shivanshu Sharma"]
    return {
        "team_name": os.environ.get("BOT_TEAM_NAME", "Shivanshu Sharma"),
        "team_members": ml,
        "model": os.environ.get("BOT_MODEL", os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")),
        "approach": os.environ.get(
            "BOT_APPROACH",
            "Hybrid Groq composer with deterministic templates, playbooks, and hydration fallbacks",
        ),
        "contact_email": os.environ.get("BOT_CONTACT_EMAIL", ""),
        "version": os.environ.get("BOT_VERSION", "1.0.0"),
        "submitted_at": os.environ.get("BOT_SUBMITTED_AT") or _iso_now(),
    }


@app.post("/v1/context")
async def push_context(body: ContextBody):
    store = Store.get()
    ok, reason, cur_v = store.push_context(
        body.scope, body.context_id, body.version, body.payload
    )
    if not ok:
        return {
            "accepted": False,
            "reason": reason or "stale_version",
            "current_version": cur_v,
        }
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": _iso_now(),
    }


def _get_payload(scope: str, cid: str) -> Optional[dict]:
    store = Store.get()
    rec = store.get_context(scope, cid)
    return copy.deepcopy(rec.payload) if rec else None


@app.post("/v1/tick")
async def tick(body: TickBody):
    deadline = time.monotonic() + 22.0
    store = Store.get()
    actions: List[Dict[str, Any]] = []

    ranked: List[tuple] = []
    for tid in body.available_triggers or []:
        tr = _get_payload("trigger", tid)
        if not tr:
            continue
        if is_trigger_expired(tr.get("expires_at"), body.now):
            continue
        u = int(tr.get("urgency") or 0)
        ranked.append((u, tid, tr))
    ranked.sort(key=lambda x: -x[0])

    for u, tid, tr in ranked:
        if time.monotonic() > deadline:
            break
        if len(actions) >= 20:
            break

        sk = tr.get("suppression_key") or ""
        if sk and store.is_suppressed(sk):
            continue

        mid = tr.get("merchant_id")
        if not mid:
            continue
        merchant = _get_payload("merchant", mid)
        if not merchant:
            continue
        slug = merchant.get("category_slug") or ""
        cat = _get_payload("category", slug)
        if not cat:
            continue

        cid = tr.get("customer_id")
        customer = _get_payload("customer", cid) if cid else None

        cat_rec = store.get_context("category", slug)
        mer_rec = store.get_context("merchant", mid)
        cus_rec = store.get_context("customer", cid) if cid else None

        cv = cat_rec.version if cat_rec else 1
        mv = mer_rec.version if mer_rec else 1
        cuv = cus_rec.version if cus_rec else 1

        try:
            composed = compose_message(
                cat,
                merchant,
                tr,
                customer,
                category_version=cv,
                merchant_version=mv,
                customer_version=cuv,
                now_iso=body.now,
            )
        except Exception:
            continue

        if time.monotonic() > deadline:
            break

        conv_id = f"conv_{mid}_{tid}"
        fp = build_facts_pack(cat, merchant, tr, customer, now_iso=body.now)
        der = fp.get("derived") or {}
        init_conversation_hook(
            conv_id,
            mid,
            cid,
            tid,
            composed.get("body", ""),
            trigger_kind=tr.get("kind"),
            best_offer_title=der.get("best_live_offer_title"),
            top_search_hint=der.get("top_trend_query_near_me"),
            locality=der.get("locality")
            or (merchant.get("identity") or {}).get("locality"),
        )

        if sk:
            store.suppress(sk)

        actions.append(
            {
                "conversation_id": conv_id,
                "merchant_id": mid,
                "customer_id": cid,
                "send_as": composed.get("send_as", "vera"),
                "trigger_id": tid,
                "template_name": composed.get("template_name", "vera_generic_v1"),
                "template_params": composed.get("template_params") or [],
                "body": composed.get("body", ""),
                "cta": composed.get("cta", "open_ended"),
                "suppression_key": composed.get("suppression_key") or sk,
                "rationale": composed.get("rationale", ""),
            }
        )

    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    out = handle_reply(
        body.conversation_id,
        body.merchant_id or "",
        body.customer_id,
        body.from_role,
        body.message,
        body.turn_number,
    )
    return out


def create_app():
    return app
