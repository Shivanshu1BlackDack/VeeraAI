"""LLM composition with validation and deterministic template fallback."""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, Dict, Optional

from llm_provider import LLMError, complete_composer_json
from playbooks import get_playbook, resolve_send_as
from semantic_compat import prepare_trigger_for_compose
from signals import (
    body_banned_internal,
    body_banned_vague,
    body_has_taboo,
    body_has_url,
    build_facts_pack,
    cache_key,
    scrub_merchant_body,
)
import templates

ALLOWED_CTA = {
    "open_ended",
    "binary_yes_no",
    "binary_confirm_cancel",
    "multi_choice_slot",
    "none",
}

SYSTEM_PROMPT = """You are Vera (magicpin) — WhatsApp assistant for merchant growth.

Return ONLY one JSON object (no markdown) with keys:
body, cta, send_as, suppression_key, rationale, template_name, template_params (array of strings).

HARD RUBRIC (judge scores this):
1) BODY STRUCTURE (up to 3 short sentences): Sentence A = one concrete merchant fact from facts JSON (number, ₹ price, locality, metric delta). Sentence B = social proof or implication (how competitors are doing, trending stats). Sentence C = ONE low-friction CTA (question or YES/NO).
2) FORBIDDEN in body: “increase sales”, “boost engagement”, “run a campaign today”, “grow your business”, “skyrocket”, “amazing deal”, generic “discount campaign”, hollow “let us help you succeed”.
3) NO greetings that waste the open line (avoid “Hope you are well”, “Dear merchant”). Start with the anchor fact or name + fact.
4) NO URLs, http, www. NO claims not supported by facts JSON (no invented competitors, stats, or citations).
5) If facts.derived has numbers (views, calls, CTR gap, best_live_offer_title, trend query), you MUST use at least one to build strong social proof.
6) Match voice.tone; never use voice.taboos.
7) send_as: use expected_send_as from user JSON exactly.
8) cta must be one of: open_ended, binary_yes_no, binary_confirm_cancel, multi_choice_slot, none
9) suppression_key: reuse trigger.suppression_key if present.
10) Do not plagiarize famous case-study wording; paraphrase structure only.
11) hi-en code-mix MUST be used if language_hint is hi_en_mix (Use natural Hinglish like "Bhaiya", "Sir", "dikkat", "fayda" to sound like a local).
12) MERCHANT NATURALNESS: write like WhatsApp to a busy owner — never internal or “spec doc” wording.
    Forbidden anywhere in body (and close variants): spine, priced off, one block, per chart, on file,
    dispense log, payload, placeholder, merchant_last_message, naked JSON field names, “paste … in one block”.
    If a fact is missing, speak generally (“routine refill from last visit”) — never echo missing-field placeholders.
"""


def _sentence_count(body: str) -> int:
    s = body.strip()
    if not s:
        return 0
    parts = [p for p in re.split(r"(?<=[.!?।])\s+", s) if p.strip()]
    return len(parts) if parts else 1


def _validate(
    out: Dict[str, Any],
    expected_send_as: str,
    taboos: list,
) -> Optional[str]:
    body = (out.get("body") or "").strip()
    if not body:
        return "empty body"
    if body_has_url(body):
        return "url"
    if body_has_taboo(body, taboos):
        return "taboo"
    if body_banned_vague(body):
        return "vague_crm"
    if body_banned_internal(body):
        return "internal_tooling"
    if _sentence_count(body) > 4:
        return "too_many_sentences"
    cta = out.get("cta")
    if cta not in ALLOWED_CTA:
        return "bad cta"
    if out.get("send_as") != expected_send_as:
        return "send_as mismatch"
    if not out.get("suppression_key"):
        return "suppression_key"
    if not out.get("rationale"):
        return "rationale"
    return None


def compose_message(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
    category_version: int = 1,
    merchant_version: int = 1,
    customer_version: int = 1,
    use_llm: bool = True,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    slug = (category or {}).get("slug") or (merchant or {}).get("category_slug") or ""
    tw, kind, semantic_safe = prepare_trigger_for_compose(copy.deepcopy(trigger), slug)
    facts = build_facts_pack(category, merchant, tw, customer, now_iso=now_iso)
    pb = get_playbook(kind)
    sc = tw.get("scope") or "merchant"
    expected_send = resolve_send_as(pb, customer, sc)
    taboos = (facts.get("voice") or {}).get("taboos") or []

    tid = tw.get("id") or ""
    cid = (customer or {}).get("customer_id") if customer else None
    ck = cache_key(
        category_version,
        merchant_version,
        tid,
        cid,
        customer_version,
        facts,
    )

    from state import Store

    store = Store.get()
    cached = store.get_compose_cache(ck)
    if cached:
        out = dict(cached)
        out["body"] = scrub_merchant_body((out.get("body") or "").strip())[:4000]
        return out

    def _finalize(t: Dict[str, Any]) -> Dict[str, Any]:
        o = dict(t)
        o["body"] = scrub_merchant_body((o.get("body") or "").strip())[:4000]
        return o

    def _fallback() -> Dict[str, Any]:
        t = templates.fallback_compose(kind, category, merchant, tw, customer, facts)
        if body_banned_internal(t.get("body") or ""):
            t = templates.fallback_compose("default", category, merchant, tw, customer, facts)
        t["template_params"] = list(t.get("template_params") or [])
        return _finalize(t)

    # F2: semantic mismatch must never hit the LLM or specialist template_name from model output.
    if semantic_safe:
        result = _fallback()
        result["template_name"] = result.get("template_name") or pb.template_name
        result["cta"] = result.get("cta") or pb.cta_default
        result["send_as"] = result.get("send_as") or expected_send
        result["suppression_key"] = result.get("suppression_key") or (tw.get("suppression_key") or "")
        store.set_compose_cache(ck, result)
        return result

    if not use_llm or os.environ.get("LLM_API_KEY", "") == "":
        result = _fallback()
        store.set_compose_cache(ck, result)
        return result

    import json as _json

    user = _json.dumps(
        {
            "expected_send_as": expected_send,
            "facts": facts,
            "playbook": {
                "cta_default": pb.cta_default,
                "cta_style": pb.cta_style,
                "emotional_frame": pb.emotional_frame,
                "forbidden": list(pb.forbidden),
                "mandatory_fact_keys": pb.mandatory_fact_keys,
                "message_objective": pb.message_objective,
                "must_include": pb.must_include,
                "template_name": pb.template_name,
                "voice_emphasis": pb.voice_emphasis,
            },
            "trigger_kind": kind,
            "semantic_safe_mode": kind == "cross_domain_safe",
        },
        ensure_ascii=False,
    )

    out: Optional[Dict[str, Any]] = None
    for _ in range(2):
        try:
            out = complete_composer_json(SYSTEM_PROMPT, user)
            break
        except (LLMError, json.JSONDecodeError, KeyError):
            out = None
    if out is None:
        result = _fallback()
        store.set_compose_cache(ck, result)
        return result

    out["body"] = scrub_merchant_body((out.get("body") or "").strip())

    v = _validate(out, expected_send, taboos)
    if v:
        try:
            out2 = complete_composer_json(
                SYSTEM_PROMPT
                + f"\nPrevious JSON failed: {v}. Fix: max 2 sentences, one numeric anchor, no vague CRM, "
                "no internal tooling words (spine, priced off, one block, per chart, on file, dispense log, payload). "
                "JSON only.",
                user,
            )
            v2 = _validate(out2, expected_send, taboos)
            if not v2:
                out2["body"] = scrub_merchant_body((out2.get("body") or "").strip())
                out = out2
            else:
                result = _fallback()
                store.set_compose_cache(ck, result)
                return result
        except Exception:
            result = _fallback()
            store.set_compose_cache(ck, result)
            return result

    out["template_name"] = pb.template_name
    if not isinstance(out.get("template_params"), list):
        out["template_params"] = []
    result = {
        "body": out.get("body", "")[:4000],
        "cta": out.get("cta", pb.cta_default),
        "rationale": out.get("rationale", "")[:2000],
        "send_as": out.get("send_as", expected_send),
        "suppression_key": out.get("suppression_key") or (tw.get("suppression_key") or ""),
        "template_name": pb.template_name,
        "template_params": [str(x) for x in (out.get("template_params") or [])],
    }
    if _validate(result, expected_send, taboos):
        result = _fallback()
    else:
        result = _finalize(result)
    store.set_compose_cache(ck, result)
    return result


def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
) -> Dict[str, Any]:
    """Public API matching challenge-brief §7.1."""
    return compose_message(category, merchant, trigger, customer)
