"""Derive grounded facts from category, merchant, trigger, customer — no invention."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _sorted_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sorted_jsonable(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_sorted_jsonable(x) for x in obj]
    return obj


def _peer_ctr_delta(merchant: dict, category: dict) -> Optional[float]:
    try:
        peer = float(category.get("peer_stats", {}).get("avg_ctr", 0) or 0)
        m = float(merchant.get("performance", {}).get("ctr", 0) or 0)
        if peer == 0:
            return None
        return round(m - peer, 4)
    except (TypeError, ValueError):
        return None


def _active_offers(merchant: dict) -> List[str]:
    out: List[str] = []
    for o in merchant.get("offers") or []:
        if (o or {}).get("status") == "active":
            t = (o or {}).get("title")
            if t:
                out.append(str(t))
    return out


def _offer_rows(merchant: dict) -> List[dict]:
    return [x for x in (merchant.get("offers") or []) if isinstance(x, dict)]


def _inactive_offer_count(merchant: dict) -> int:
    n = 0
    for o in _offer_rows(merchant):
        st = (o or {}).get("status", "")
        if st and st != "active":
            n += 1
    return n


def _parse_rupees_from_title(title: str) -> Optional[int]:
    if not title:
        return None
    m = re.search(r"₹\s*([\d,]+)", title)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _best_live_offer(merchant: dict) -> Tuple[Optional[str], Optional[int]]:
    """First active offer title and parsed ₹ price if any."""
    for o in _offer_rows(merchant):
        if o.get("status") != "active":
            continue
        t = o.get("title") or ""
        if not t:
            continue
        return t, _parse_rupees_from_title(t)
    return None, None


def _top_service_name_from_offer(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    part = title.split("@")[0].strip()
    return part or None


def _underused_offer_title(merchant: dict) -> Optional[str]:
    """Active offer with oldest `started` if dates exist."""
    best: Optional[Tuple[str, str]] = None
    for o in _offer_rows(merchant):
        if o.get("status") != "active":
            continue
        st = o.get("started") or o.get("started_at")
        tit = o.get("title")
        if st and tit:
            if best is None or st < best[0]:
                best = (st, tit)
    return best[1] if best else None


def _digest_item_by_id(category: dict, item_id: str) -> Optional[dict]:
    for d in category.get("digest") or []:
        if (d or {}).get("id") == item_id:
            return d
    return None


def _language_hint(identity: dict) -> str:
    langs = identity.get("languages") or ["en"]
    if "hi" in langs and "en" in langs:
        return "hi_en_mix"
    if "hi" in langs:
        return "hindi_leaning"
    return "english"


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _utc_for_delta(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize for subtraction: naive timestamps are treated as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_expires(expires_at: Optional[str]) -> Optional[datetime]:
    return _parse_iso(expires_at)


def is_trigger_expired(expires_at: Optional[str], now_iso: str) -> bool:
    ex = _parse_expires(expires_at)
    if ex is None:
        return False
    try:
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return ex < now
    except Exception:
        return False


def _top_trend_query(category: dict) -> Optional[str]:
    ts = category.get("trend_signals") or []
    if not ts:
        return None
    q = (ts[0] or {}).get("query")
    return str(q) if q else None


def _conversation_derived(merchant: dict, now_iso: Optional[str]) -> Dict[str, Any]:
    hist = merchant.get("conversation_history") or []
    out: Dict[str, Any] = {
        "days_since_last_vera_message": None,
        "days_since_last_merchant_reply": None,
        "last_vera_engagement": None,
        "recent_unanswered_vera_cta": False,
        "reply_gap_days": None,
    }
    if not hist:
        return out

    last_vera_ts: Optional[str] = None
    last_merchant_ts: Optional[str] = None
    last_vera_eng: Optional[str] = None
    for row in reversed(hist):
        fr = (row or {}).get("from")
        if fr == "vera" and last_vera_ts is None:
            last_vera_ts = (row or {}).get("ts")
            last_vera_eng = (row or {}).get("engagement")
        if fr == "merchant" and last_merchant_ts is None:
            last_merchant_ts = (row or {}).get("ts")
    out["last_vera_engagement"] = last_vera_eng
    if last_vera_eng and "no_reply" in str(last_vera_eng).lower():
        out["recent_unanswered_vera_cta"] = True

    now = _parse_iso(now_iso) if now_iso else None
    if now and last_vera_ts:
        lv = _parse_iso(last_vera_ts)
        nu, lu = _utc_for_delta(now), _utc_for_delta(lv)
        if nu and lu:
            out["days_since_last_vera_message"] = max(0, (nu - lu).days)
    if now and last_merchant_ts:
        lm = _parse_iso(last_merchant_ts)
        nu, mu = _utc_for_delta(now), _utc_for_delta(lm)
        if nu and mu:
            out["days_since_last_merchant_reply"] = max(0, (nu - mu).days)
            if last_vera_ts:
                lv2 = _parse_iso(last_vera_ts)
                lu2 = _utc_for_delta(lv2)
                if lu2 and mu < lu2:
                    out["reply_gap_days"] = max(0, (nu - mu).days)
    return out


def _performance_tension(merchant: dict) -> Dict[str, Any]:
    perf = merchant.get("performance") or {}
    d7 = perf.get("delta_7d") or {}
    agg = merchant.get("customer_aggregate") or {}
    views_pct = d7.get("views_pct")
    calls_pct = d7.get("calls_pct")
    ctr_pct = d7.get("ctr_pct")
    tension: Optional[str] = None
    try:
        vp = float(views_pct) if views_pct is not None else None
        cp = float(calls_pct) if calls_pct is not None else None
        if vp is not None and cp is not None and vp > 0.02 and cp < -0.02:
            tension = "views_up_calls_down"
    except (TypeError, ValueError):
        pass

    del30 = agg.get("delivery_orders_30d")
    dine30 = agg.get("dine_in_orders_30d")
    high_search_low_orders = False
    try:
        if del30 is not None and views_pct is not None:
            if float(views_pct) > 0.05 and del30 == 0:
                high_search_low_orders = True
    except (TypeError, ValueError):
        pass

    return {
        "calls_delta_7d_pct": calls_pct,
        "ctr_delta_7d_pct": ctr_pct,
        "delivery_orders_30d": del30,
        "dine_in_orders_30d": dine30,
        "high_search_low_order_flag": high_search_low_orders,
        "orders_drop_vs_views": tension,
        "views_delta_7d_pct": views_pct,
    }


def _repeat_customer_loss_signals(merchant: dict) -> Dict[str, Any]:
    agg = merchant.get("customer_aggregate") or {}
    lapsed = agg.get("lapsed_180d_plus") or agg.get("lapsed_90d_plus")
    ret = agg.get("retention_6mo_pct") or agg.get("retention_3mo_pct")
    return {
        "lapsed_customers_band": lapsed,
        "repeat_customer_retention_pct": ret,
    }


def _rating_gap_vs_peer(merchant: dict, category: dict) -> Optional[float]:
    """Merchant rating not always in JSON; return None if absent."""
    peer_r = category.get("peer_stats", {}).get("avg_rating")
    # Some seeds might add merchant.reviews_rating — not standard; skip if missing
    mr = merchant.get("gbp_rating") or merchant.get("average_rating")
    try:
        if mr is None or peer_r is None:
            return None
        return round(float(mr) - float(peer_r), 2)
    except (TypeError, ValueError):
        return None


def _customer_derived(customer: dict, now_iso: Optional[str]) -> Dict[str, Any]:
    rel = customer.get("relationship") or {}
    last_visit = rel.get("last_visit")
    visits = rel.get("visits_total")
    st = customer.get("state") or "unknown"
    consent = customer.get("consent") or {}
    scopes = list(consent.get("scope") or [])
    if not scopes:
        consent_mode = "no_marketing_consent_recorded"
    elif any("promo" in str(s).lower() for s in scopes):
        consent_mode = "promotional_ok"
    elif any("recall" in str(s).lower() or "refill" in str(s).lower() for s in scopes):
        consent_mode = "service_reminders_only"
    else:
        consent_mode = "mixed"

    band = st
    if visits is not None:
        try:
            v = int(visits)
            if v >= 12:
                band = f"{st}_heavy_user"
            elif v <= 2:
                band = f"{st}_light_user"
        except (TypeError, ValueError):
            pass

    days_since_visit: Optional[int] = None
    now = _parse_iso(now_iso) if now_iso else None
    lv = _parse_iso(last_visit) if last_visit else None
    if now and lv:
        nu, lu = _utc_for_delta(now), _utc_for_delta(lv)
        if nu and lu:
            days_since_visit = max(0, (nu - lu).days)

    prefs = customer.get("preferences") or {}
    focus = (
        prefs.get("training_focus")
        or prefs.get("health_focus")
        or prefs.get("favourite_dish")
    )

    return {
        "consent_mode": consent_mode,
        "consent_scopes": scopes,
        "customer_last_visit_iso": last_visit,
        "customer_loyalty_band": band,
        "days_since_last_visit": days_since_visit,
        "preference_anchor": focus,
        "visits_total": visits,
    }


def derive_opportunity_signals(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Rich, judge-friendly anchors — all from JSON fields or explicit date math from now_iso."""
    peer = category.get("peer_stats") or {}
    ident = merchant.get("identity") or {}
    perf = merchant.get("performance") or {}
    agg = merchant.get("customer_aggregate") or {}
    conv_d = _conversation_derived(merchant, now_iso)
    tens = _performance_tension(merchant)
    ctr_delta = _peer_ctr_delta(merchant, category)
    best_tit, best_price = _best_live_offer(merchant)
    under = _underused_offer_title(merchant)
    top_svc = _top_service_name_from_offer(best_tit)
    inactive_n = _inactive_offer_count(merchant)
    active_titles = _active_offers(merchant)

    urgency_anchors: List[str] = []
    if conv_d.get("recent_unanswered_vera_cta"):
        urgency_anchors.append("last_vera_nudge_unanswered")
    if tens.get("orders_drop_vs_views"):
        urgency_anchors.append("visibility_up_conversion_down")
    if tens.get("high_search_low_order_flag"):
        urgency_anchors.append("high_search_low_orders")
    try:
        if trigger.get("urgency") and int(trigger.get("urgency") or 0) >= 4:
            urgency_anchors.append("trigger_high_urgency")
    except (TypeError, ValueError):
        pass

    derived: Dict[str, Any] = {
        "active_offer_titles": active_titles[:5],
        "best_live_offer_price_inr": best_price,
        "best_live_offer_title": best_tit,
        "conversion_gap_ctr_minus_peer": ctr_delta,
        "customer_aggregate_snapshot": {
            "chronic_rx_count": agg.get("chronic_rx_count"),
            "high_risk_adult_count": agg.get("high_risk_adult_count"),
            "total_active_members": agg.get("total_active_members"),
            "total_unique_ytd": agg.get("total_unique_ytd"),
        },
        "high_search_low_order_flag": tens.get("high_search_low_order_flag"),
        "inactive_offer_count": inactive_n,
        "locality": ident.get("locality"),
        "orders_drop_vs_views": tens.get("orders_drop_vs_views"),
        "performance_tension": tens,
        "peer_avg_ctr": peer.get("avg_ctr"),
        "rating_gap_vs_peer": _rating_gap_vs_peer(merchant, category),
        "recent_unanswered_vera_cta": conv_d.get("recent_unanswered_vera_cta"),
        "repeat_customer_signals": _repeat_customer_loss_signals(merchant),
        "reply_gap_days": conv_d.get("reply_gap_days"),
        "top_service_name_from_catalog": top_svc,
        "top_trend_query_near_me": _top_trend_query(category),
        "underused_active_offer_title": under,
        "urgency_language_anchors": urgency_anchors,
        "vera_conversation": conv_d,
        "views_30d": perf.get("views"),
        "calls_30d": perf.get("calls"),
    }
    if customer:
        derived["customer_derived"] = _customer_derived(customer, now_iso)
    return _sorted_jsonable(derived)


def build_facts_pack(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
    now_iso: Optional[str] = None,
) -> dict:
    """Compact sorted dict for prompts and cache keys."""
    ident = merchant.get("identity") or {}
    perf = merchant.get("performance") or {}
    sub = merchant.get("subscription") or {}
    cat_voice = category.get("voice") or {}
    peer = category.get("peer_stats") or {}

    trigger_payload = trigger.get("payload") or {}
    tid_inner = (
        trigger_payload.get("payload")
        if isinstance(trigger_payload.get("payload"), dict)
        else {}
    )

    top_item_id = trigger_payload.get("top_item_id") or tid_inner.get("top_item_id")
    digest_item = None
    if top_item_id:
        digest_item = _digest_item_by_id(category, top_item_id)

    cust_facts: Optional[dict] = None
    if customer:
        cd = _customer_derived(customer, now_iso)
        cust_facts = {
            "consent_scopes": list((customer.get("consent") or {}).get("scope") or []),
            "customer_derived": cd,
            "customer_id": customer.get("customer_id"),
            "identity": {
                "language_pref": (customer.get("identity") or {}).get("language_pref"),
                "name": (customer.get("identity") or {}).get("name"),
            },
            "preferences": customer.get("preferences") or {},
            "relationship": customer.get("relationship") or {},
            "state": customer.get("state"),
        }

    derived_block = derive_opportunity_signals(
        category, merchant, trigger, customer, now_iso=now_iso
    )

    facts = {
        "category_slug": category.get("slug"),
        "derived": derived_block,
        "digest_item": digest_item,
        "merchant": {
            "category_slug": merchant.get("category_slug"),
            "conversation_history_tail": (merchant.get("conversation_history") or [])[-5:],
            "customer_aggregate": merchant.get("customer_aggregate") or {},
            "identity": {
                "city": ident.get("city"),
                "languages": ident.get("languages"),
                "locality": ident.get("locality"),
                "name": ident.get("name"),
                "owner_first_name": ident.get("owner_first_name"),
                "verified": ident.get("verified"),
            },
            "merchant_id": merchant.get("merchant_id"),
            "offers_active": _active_offers(merchant),
            "performance": {
                "calls": perf.get("calls"),
                "ctr": perf.get("ctr"),
                "delta_7d": perf.get("delta_7d"),
                "directions": perf.get("directions"),
                "views": perf.get("views"),
                "window_days": perf.get("window_days"),
            },
            "review_themes": merchant.get("review_themes") or [],
            "signals": merchant.get("signals") or [],
            "subscription": {
                "days_remaining": sub.get("days_remaining"),
                "plan": sub.get("plan"),
                "status": sub.get("status"),
            },
        },
        "now_iso_for_relative_dates": now_iso,
        "peer_stats_summary": {
            "avg_ctr": peer.get("avg_ctr"),
            "avg_rating": peer.get("avg_rating") or peer.get("avg_review_count"),
            "scope": peer.get("scope"),
        },
        "peer_vs_merchant_ctr_delta": _peer_ctr_delta(merchant, category),
        "language_hint": _language_hint(ident),
        "trigger": {
            "expires_at": trigger.get("expires_at"),
            "id": trigger.get("id"),
            "kind": trigger.get("kind"),
            "merchant_id": trigger.get("merchant_id"),
            "payload": trigger_payload,
            "scope": trigger.get("scope"),
            "source": trigger.get("source"),
            "suppression_key": trigger.get("suppression_key"),
            "urgency": trigger.get("urgency"),
        },
        "voice": {
            "taboos": cat_voice.get("vocab_taboo") or cat_voice.get("taboos") or [],
            "tone": cat_voice.get("tone"),
        },
    }
    if cust_facts:
        facts["customer"] = cust_facts

    return _sorted_jsonable(facts)


def cache_key(
    category_ver: int,
    merchant_ver: int,
    trigger_id: str,
    customer_id: Optional[str],
    customer_ver: int,
    facts: dict,
) -> str:
    import hashlib
    import json

    base = json.dumps(facts, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return f"{category_ver}|{merchant_ver}|{trigger_id}|{customer_id or ''}|{customer_ver}|{h}"


def body_has_url(text: str) -> bool:
    return bool(re.search(r"https?://|www\.", text, re.I))


def body_has_taboo(text: str, taboos: List[str]) -> bool:
    low = text.lower()
    for t in taboos or []:
        if not t:
            continue
        if str(t).lower() in low:
            return True
    return False


def body_banned_vague(text: str) -> bool:
    """Heuristic: penalize generic CRM phrases in validation retry path."""
    low = text.lower()
    banned = (
        "increase your sales",
        "boost engagement",
        "run a campaign today",
        "grow your business",
        "skyrocket",
        "amazing deal",
    )
    return any(b in low for b in banned)


def body_banned_internal(text: str) -> bool:
    """True if body leaks template/tooling vocabulary (LLM-judge penalty)."""
    if not text:
        return False
    low = (
        text.lower()
        .replace("’", "'")
        .replace("`", "'")
    )
    banned = (
        "here's the spine",
        "here is the spine",
        "the spine for",
        "priced off",
        "one block",
        "in one block",
        "per chart",
        "on file",
        "in your dispense log",
        "dispense log",
        "payload",
        "placeholder",
        "merchant_last_message",
        "paste whatsapp + gbp",
        "whatsapp + gbp in",
        "runs dry ~the due date",
        "chronic strip on file",
        "a new nearby listing",
        "an aggressive intro price",
        "template_params",
        "suppression_key:",
    )
    return any(b in low for b in banned)


def scrub_merchant_body(text: str) -> str:
    """
    Post-process merchant text: remove enum-ish tokens, empty quotes, broken fragments.
    Complements template present_text guards for LLM output.
    """
    if not text:
        return text
    s = text.replace("`", "'")
    # snake_case / kebab tokens → spoken words
    def _unsnake(m: re.Match) -> str:
        w = m.group(0)
        if "_" in w:
            return w.replace("_", " ")
        return w

    s = re.sub(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", _unsnake, s, flags=re.I)
    s = re.sub(r"\b[a-z]+(?:-[a-z]+)+\b", lambda m: m.group(0).replace("-", " "), s, flags=re.I)
    # hollow quotes
    s = re.sub(r'\s*""\s*', " ", s)
    s = re.sub(r"\s*“\s*”\s*", " ", s)
    s = re.sub(r"\s*'\s*'\s*", " ", s)
    s = re.sub(
        r'you said\s*(?:"{2}|\'{2}|\u201c\s*\u201d)\s*',
        "you mentioned an idea ",
        s,
        flags=re.I,
    )
    # "at ." / " — ." fragments
    s = re.sub(r"\bat\s+\.\s*", "soon ", s, flags=re.I)
    s = re.sub(r"\s+—\s*\.\s*", " — ", s)
    s = re.sub(r"New research item\s*—\s*\.", "New research landed — want a quick summary?", s, flags=re.I)
    s = re.sub(r"\s+\.\s+Want\b", ". Want", s)
    # tighten whitespace
    s = re.sub(r"  +", " ", s).strip()
    return s
