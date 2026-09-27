"""Deterministic message templates — facts only, no LLM."""

from __future__ import annotations

from typing import Any, Dict, Optional

from hydration import (
    category_bucket,
    chronic_refill_sparse_body,
    human_short_date,
    humanize_metric,
    humanize_verification_path,
    is_blank,
    lapsed_soft_offer_phrase,
    present,
    present_text,
    recall_offer_line,
    recall_reminder_phrase,
)
from semantic_compat import (
    planning_topic_plausible_for_bucket,
    safe_archetype_for_kind,
    safe_variant_index,
)


def _d(facts: dict) -> dict:
    return facts.get("derived") or {}


def safe_offer_label(merchant: dict, bucket: str, facts: Optional[dict] = None) -> str:
    """Concrete offer phrase for templates; never returns empty ‘main live offer’ filler."""
    hero = present_text((merchant or {}).get("best_live_offer"))
    if hero and "main live offer" not in hero.lower():
        label = hero
    else:
        d = ((facts or {}).get("derived")) or {}
        mer = (facts or {}).get("merchant") or {}
        label = present_text(d.get("best_live_offer_title"))
        if not label:
            for o in mer.get("offers_active") or []:
                label = present_text(o)
                if label:
                    break
        if label and "main live offer" in label.lower():
            label = None
    if not label:
        fallback = {
            "dentist": "your top cleaning service",
            "pharmacy": "your repeat medicine delivery offer",
            "fitness": "your best trial class",
            "salon": "your salon starter package",
            "restaurant": "your top repeat order combo",
        }
        label = fallback.get(bucket, "your strongest customer offer")
    if len(label) > 72:
        return label[:69] + "…"
    return label


def _trial_service_word(bucket: str) -> str:
    return {
        "fitness": "comeback class",
        "salon": "grooming",
        "dentist": "follow-up",
        "restaurant": "order",
        "pharmacy": "refill",
    }.get(bucket, "visit")


def _name_merchant(merchant: dict) -> str:
    i = merchant.get("identity") or {}
    o = i.get("owner_first_name") or ""
    b = i.get("name") or "there"
    if o:
        return f"{o}, {b}"
    return b


def _pct(x: Any) -> str:
    if x is None:
        return ""
    try:
        return f"{float(x)*100:.0f}%"
    except (TypeError, ValueError):
        return str(x)


def _pretty_seasonal_trends(trends: list) -> str:
    """Turn ORS_demand_+40 style tokens into merchant-facing ORS +40%, …"""
    parts: list[str] = []
    for t in trends[:4]:
        s = str(t)
        if "_demand_+" in s:
            left, _, right = s.partition("_demand_+")
            label = left.replace("_", " ").strip() or "item"
            parts.append(f"{label} +{right}%")
        elif "_demand_-" in s:
            left, _, right = s.partition("_demand_-")
            label = left.replace("_", " ").strip() or "item"
            parts.append(f"{label} -{right}%")
        else:
            parts.append(s.replace("_", " "))
    return ", ".join(parts) if parts else "key SKUs in your category"


def _gbp_path_safe_for_bucket(bucket: str, path_raw: Any) -> str:
    p = humanize_verification_path(path_raw)
    if bucket == "pharmacy":
        return p
    low = p.lower()
    if any(
        x in low
        for x in ("atorvastatin", "recall", "rx_batch", "molecule_list", "chronic_rx", "metformin")
    ):
        return "postcard or phone verification"
    return p


def fallback_compose(
    kind: str,
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
    facts: dict,
) -> Dict[str, Any]:
    k = (kind or "default").lower()
    fn = TEMPLATE_DISPATCH.get(k, _tpl_default)
    return fn(category, merchant, trigger, customer, facts)


def _tpl_default(cat, m, tr, c, facts) -> Dict[str, Any]:
    d = _d(facts)
    nm = (m.get("identity") or {}).get("owner_first_name") or _name_merchant(m).split(",")[0]
    loc = present(d.get("locality"), None) or (m.get("identity") or {}).get("locality", "") or "your area"
    views = present(d.get("views_30d"), None)
    calls = present(d.get("calls_30d"), None)
    ctr_gap = present(d.get("conversion_gap_ctr_minus_peer"), None)
    best = d.get("best_live_offer_title") or (
        (d.get("active_offer_titles") or [None])[0]
    )
    trend = present_text(d.get("top_trend_query_near_me")) or ""
    sk = tr.get("suppression_key") or f"generic:{tr.get('id','')}"
    trend_bit = f' “{trend}” is trending in search — ' if trend else " "
    if views is not None and calls is not None and ctr_gap is not None:
        perf = f"{views} views / 30d and {calls} calls (CTR vs peer gap {ctr_gap})"
    else:
        perf = "your listing still has workable visibility"
    body = (
        f"{nm}, your {loc} listing shows {perf};{trend_bit}"
        f"should I relaunch {best or 'your hero offer'} with one sharper WhatsApp line? Want me to draft that?"
    )
    return {
        "body": body[:2000],
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf" if c and tr.get("scope") == "customer" else "vera",
        "suppression_key": sk,
        "rationale": "Catch-all nudge when no specialised template fits: relist hero offer with sharper copy.",
        "template_name": "vera_generic_v1",
        "template_params": [nm[:40], tr.get("kind", ""), loc],
    }


def _tpl_research_digest(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or "there"
    b = category_bucket(m.get("category_slug"))
    honorific = "Dr. " if b == "dentist" else ""
    di = facts.get("digest_item") or {}
    title = present_text(di.get("title")) or "a new item in this week’s category digest"
    title = title[:90]
    trial_n = di.get("trial_n")
    d = _d(facts)
    hr = (d.get("customer_aggregate_snapshot") or {}).get("high_risk_adult_count")
    summ = present_text(di.get("summary"))
    summ_bit = f"{summ[:100]}; " if summ else ""
    trial_bit = f"{trial_n}-patient trial; " if trial_n else ""
    panel = (
        f"You’re following {hr} high-risk adults — "
        if hr is not None
        else ""
    )
    src = present_text(di.get("source"))
    src_bit = f" ({src})" if src else ""
    aud = "patient" if b in ("dentist", "pharmacy") else "customer"
    body = (
        f"{honorific}{nm}, new research: {title} — {trial_bit}{summ_bit}{src_bit} "
        f"{panel}Want a short summary + {aud}-ready WhatsApp wording drafted? Say YES."
    )
    return {
        "body": body.strip(),
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "New clinical evidence landed; Vera offers a tight summary plus patient-ready wording.",
        "template_name": "vera_research_digest_v1",
        "template_params": [nm, title[:80], src],
    }


def _tpl_regulation_change(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    item_id = pk.get("top_item_id")
    di = None
    if item_id:
        for d in cat.get("digest") or []:
            if d.get("id") == item_id:
                di = d
                break
    title = present_text((di or {}).get("title")) or "a compliance update for your listing"
    deadline = present_text(pk.get("deadline_iso")) or present_text(tr.get("expires_at"))
    dl_bit = f" Target date: {deadline}." if deadline else ""
    body = (
        f"{nm.split(',')[0] if ',' in nm else nm}, regulatory heads-up: {title}.{dl_bit} "
        f"Want me to outline a short audit checklist? Reply YES."
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Rule change is dated; Vera helps the owner prep a short audit checklist before the deadline.",
        "template_name": "vera_compliance_v1",
        "template_params": [nm[:30], title[:60], (deadline or "")[:20]],
    }


def _tpl_recall_due(cat, m, tr, c, facts) -> Dict[str, Any]:
    pk = tr.get("payload") or {}
    slots = pk.get("available_slots") or []
    labels: list[str] = []
    for s in slots[:2]:
        lab = present((s or {}).get("label"), None)
        if lab is not None and str(lab).strip():
            labels.append(str(lab).strip())
    slot_txt = " / ".join(labels) if labels else ""
    cust_name = ((c or {}).get("identity") or {}).get("name") or "there"
    biz = (m.get("identity") or {}).get("name") or "the clinic"
    slug = m.get("category_slug")
    offers = facts.get("merchant", {}).get("offers_active") or []
    price_line = recall_offer_line(slug, offers)
    lang = ((c or {}).get("identity") or {}).get("language_pref") or ""
    hi = "Apke liye " if "hi" in str(lang).lower() else ""
    opening = recall_reminder_phrase(slug)
    if slot_txt:
        if hi:
            slot_part = f"{hi.strip()} — here are two slots that work: {slot_txt}. "
        else:
            slot_part = f"Here are two slots that work: {slot_txt}. "
    else:
        slot_part = (
            f"{hi.strip()} — reply with a preferred evening; we’ll fit you in. "
            if hi
            else "Reply with a preferred evening — we’ll fit you in. "
        )
    body = (
        f"Hi {cust_name}, {biz} here — {opening}. "
        f"{slot_part}{price_line}. Want me to hold one of these? Reply YES or suggest another time."
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Scheduled follow-up for this customer; slots and offer match the business type.",
        "template_name": "merchant_recall_reminder_v1",
        "template_params": [cust_name, biz, slot_txt or "flexible", price_line],
    }


def _tpl_perf_dip(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or _name_merchant(m).split(",")[0]
    pk = tr.get("payload") or {}
    metric = humanize_metric(present(pk.get("metric"), "calls"))
    d = pk.get("delta_pct")
    dip = _pct(d)
    dip_phrase = f"slipped {dip} w/w" if dip else "softened this week"
    peer = (cat.get("peer_stats") or {}).get("avg_ctr")
    perf = m.get("performance") or {}
    der = _d(facts)
    best = der.get("best_live_offer_title") or (
        (der.get("active_offer_titles") or [None])[0]
    )
    trend = present_text(der.get("top_trend_query_near_me")) or "local demand"
    tension = der.get("orders_drop_vs_views")
    tnote = " (views up but calls soft — tighten conversion) " if tension else " "
    body = (
        f"{nm}, {metric} {dip_phrase} while your CTR sits {perf.get('ctr')} vs peer {peer};{tnote}"
        f"nearby “{trend}” intent is still live — relaunch {best or 'your priced offer'} with one new headline? Want me to draft it?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Traffic or calls dipped; Vera ties it to a live search theme and a concrete relaunch line.",
        "template_name": "vera_perf_dip_v1",
        "template_params": [str(metric), _pct(d)],
    }


def _tpl_renewal_due(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    days = present(pk.get("days_remaining"), None)
    plan = present(pk.get("plan"), None) or "Pro"
    amt = pk.get("renewal_amount")
    amt_line = f"₹{amt} due." if amt is not None else "I’ll quote the exact amount once you say yes."
    first = nm.split(",")[0] if "," in nm else nm
    if days is not None:
        body = (
            f"{first}, renewal: {plan} — {days} days left. "
            f"{amt_line} Want the renewal link on WhatsApp now?"
        )
    else:
        body = (
            f"{first}, your {plan} renewal window is open. "
            f"{amt_line} Want the renewal link on WhatsApp now?"
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Subscription is nearing renewal; Vera makes the next step one tap on WhatsApp.",
        "template_name": "vera_renewal_v1",
        "template_params": [str(days if days is not None else ""), str(plan)],
    }


def _tpl_festival(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    fest = present(pk.get("festival"), None) or "the festival season"
    days_raw = pk.get("days_until")
    days = present(days_raw, None)
    first = nm.split(",")[0] if "," in nm else nm
    body = ""
    if days is not None:
        try:
            di = int(days)
            body = (
                f"{first}, {fest} is in about {di} days — "
                f"want a ready-made Google + WhatsApp bundle for your active offers? I can line that up now."
            )
        except (TypeError, ValueError):
            pass
    if not body:
        body = (
            f"{first}, {fest} is coming up — "
            f"want a ready-made Google + WhatsApp bundle for your active offers? I can line that up now."
        )
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Festival demand spike ahead; Vera packages posts and WA copy around what’s already live.",
        "template_name": "vera_festival_v1",
        "template_params": [fest, str(days_raw if days_raw is not None else "")],
    }


def _tpl_bridal(cat, m, tr, c, facts) -> Dict[str, Any]:
    pk = tr.get("payload") or {}
    nm_c = ((c or {}).get("identity") or {}).get("name") or "there"
    biz = (m.get("identity") or {}).get("name") or "salon"
    owner = (m.get("identity") or {}).get("owner_first_name") or "Team"
    days = present(pk.get("days_to_wedding"), None)
    offers = facts.get("merchant", {}).get("offers_active") or []
    der = _d(facts)
    off = None
    for raw in offers:
        off = present_text(raw)
        if off:
            break
    if not off:
        off = present_text(der.get("best_live_offer_title"))
    if not off:
        off = "bridal prep — we’ll tailor the bundle once you pick services"
    if days is not None:
        body = (
            f"Hi {nm_c}, {owner} from {biz} — {days} days to your wedding. "
            f"Skin-prep window is open; we’re leading with {off}. "
            f"Want me to hold your preferred Saturday slot?"
        )
    else:
        body = (
            f"Hi {nm_c}, {owner} from {biz} — wedding prep season is here. "
            f"We’re pitching {off} with clear inclusions. "
            f"Want me to hold your preferred Saturday slot?"
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Bridal guest is in countdown; owner nudges prep package and a concrete slot ask.",
        "template_name": "merchant_bridal_followup_v1",
        "template_params": [nm_c, biz, str(days if days is not None else "")],
    }


def _tpl_curious(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or "there"
    biz = (m.get("identity") or {}).get("name") or "your salon"
    body = (
        f"{nm}, name the one service customers asked for most this week at {biz} — "
        f"I’ll turn it into a Google post + WhatsApp reply copy in ~5 minutes. Want me to draft that once you text it?"
    )
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Weekly pulse on what customers are asking for; turns one answer into post + reply copy.",
        "template_name": "vera_curious_ask_v1",
        "template_params": [nm, biz],
    }


def _tpl_winback_eligible(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    dse = present(pk.get("days_since_expiry"), None)
    first = nm.split(",")[0] if "," in nm else nm
    owner = present_text((m.get("identity") or {}).get("owner_first_name")) or first
    loc = present_text((m.get("identity") or {}).get("locality")) or "your area"
    plan = present_text((m.get("subscription") or {}).get("plan")) or "Pro"
    if dse is not None:
        body = (
            f"{owner}, it has been {dse} days since the {plan} plan lapsed and you're still getting customer interest "
            f"from {loc}. Want a short renewal WhatsApp + payment link ready now?"
        )
    else:
        body = (
            f"{owner}, your {plan} tools are idle but customer traffic is still coming in — "
            f"want the renewal link + one reminder text on WhatsApp?"
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Plan lapsed recently; Vera offers a tight renewal message the owner can send as-is.",
        "template_name": "vera_winback_merchant_v1",
        "template_params": [str(dse if dse is not None else "")],
    }


def _tpl_ipl(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or "there"
    pk = tr.get("payload") or {}
    match = present_text(pk.get("match")) or "a high-traffic match night"
    venue = present_text(pk.get("venue"))
    venue_bit = f" at {venue}" if venue else ""
    weeknight = pk.get("is_weeknight", True)
    offers = facts.get("merchant", {}).get("offers_active") or []
    bo = offers[0] if offers else "your delivery special"
    hint = (
        "Saturday IPL often pulls dine-in down ~12% — push delivery-first with "
        if not weeknight
        else "Match night — highlight "
    )
    body = (
        f"{nm}, quick heads-up: {match}{venue_bit}. {hint}{bo}. "
        f"Want banner + story copy in about 10 minutes? I can draft both together."
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Match night shifts footfall; Vera ties delivery-first copy to an offer already on the menu.",
        "template_name": "vera_ipl_match_v1",
        "template_params": [match, venue],
    }


def _tpl_review_theme(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    theme_raw = present_text(pk.get("theme"))
    theme = (theme_raw.replace("_", " ") if theme_raw else "service feedback")
    n = pk.get("occurrences_30d")
    n_bit = f" ({n}x / 30d)" if n not in (None, "",) else ""
    q = present_text(pk.get("common_quote"))
    q_bit = f' Sample: "{q[:80]}".' if q else ""
    body = (
        f"{nm.split(',')[0] if ',' in nm else nm}, reviews mention {theme}{n_bit}.{q_bit} "
        f"Want a fix-it checklist + reply template? Reply YES."
    )
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Reviews cluster on one theme; Vera gives a fix checklist and a calm reply template.",
        "template_name": "vera_review_theme_v1",
        "template_params": [theme, str(n)],
    }


def _tpl_milestone(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    first = nm.split(",")[0] if "," in nm else nm
    mv = present(pk.get("milestone_value"), None)
    mkey = present(pk.get("metric"), None) or "reviews"
    mhuman = humanize_metric(mkey)
    vn = present(pk.get("value_now"), None)
    if mv is not None and vn is not None:
        body = (
            f"{first}, you're one step from {mv} {mhuman} (now {vn}). "
            f"Want a warm ‘thank-you reviewers’ Google post drafted for you?"
        )
    else:
        body = (
            f"{first}, you're close to your next review milestone. "
            f"Want a warm ‘thank-you reviewers’ Google post drafted for you?"
        )
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Ratings are nearing a round number; a thank-you post converts momentum into trust.",
        "template_name": "vera_milestone_v1",
        "template_params": [],
    }


def _tpl_planning(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or "there"
    pk = tr.get("payload") or {}
    bucket = category_bucket(m.get("category_slug"))
    topic_raw = pk.get("intent_topic")
    topic_t = present_text(topic_raw)
    if not topic_t:
        topic_h = "one new local package"
    elif not planning_topic_plausible_for_bucket(bucket, topic_t.lower()):
        topic_h = "one new local package"
    else:
        topic_h = str(topic_raw).replace("_", " ").strip() or "one new local package"
    last = present_text(pk.get("merchant_last_message"))
    opener = f'you said “{last[:80]}”' if last else "you mentioned adding one new local package"
    der = _d(facts)
    loc = der.get("locality") or "your area"
    price_anchor = der.get("best_live_offer_title") or "your weekday hero SKU"
    body = (
        f"{nm}, {opener} — based on {price_anchor}, I can draft the exact WhatsApp + Google pitch for {topic_h} in {loc} today. "
        f"Reply CONFIRM and I’ll paste copy."
    )
    return {
        "body": body,
        "cta": "binary_confirm_cancel",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Owner already showed intent to launch; Vera advances with a concrete draft tied to a live price.",
        "template_name": "vera_planning_intent_v1",
        "template_params": [topic_h],
    }


def _tpl_seasonal_dip(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or "there"
    pk = tr.get("payload") or {}
    sn = present_text(pk.get("season_note")) or ""
    d = pk.get("delta_pct")
    members = present((m.get("customer_aggregate") or {}).get("total_active_members"), None)
    mem_bit = f"your {members} active members" if members is not None else "your active members"
    b = category_bucket(m.get("category_slug"))
    if b == "fitness":
        dip_line = (
            f"normal Apr–Jun gym dip ({sn})" if sn else "normal Apr–Jun gym dip"
        )
        ask = "want a summer attendance challenge draft I can prepare?"
    else:
        dip_line = (
            f"seasonal softness on listings ({sn})" if sn else "seasonal softness on listings"
        )
        ask = "want a tight retention post + WhatsApp line I can draft?"
    body = (
        f"{nm}, views {_pct(d)} this week — {dip_line}. "
        f"Focus on {mem_bit}; {ask}"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Seasonal slump is expected; Vera reframes it as a retention play with a ready challenge.",
        "template_name": "vera_seasonal_dip_v1",
        "template_params": [],
    }


def _tpl_customer_lapsed_hard(cat, m, tr, c, facts) -> Dict[str, Any]:
    pk = tr.get("payload") or {}
    nm = ((c or {}).get("identity") or {}).get("name") or "there"
    owner = (m.get("identity") or {}).get("owner_first_name") or "Coach"
    biz = (m.get("identity") or {}).get("name") or "gym"
    slug = (m.get("category_slug") or "").lower()
    bucket = category_bucket(slug)
    days = present(pk.get("days_since_last_visit"), None)
    focus_h = _lapsed_hard_focus_display(bucket, pk.get("previous_focus"))
    der = _d(facts)
    off_raw = der.get("best_live_offer_title") or (
        (facts.get("merchant", {}).get("offers_active") or [None])[0]
    )
    off = present_text(off_raw) or {
        "dentist": "a routine check-up slot",
        "pharmacy": "your refill window",
        "fitness": "your next class slot",
        "salon": "a grooming appointment",
        "restaurant": "your usual order window",
    }.get(bucket, "your next visit")
    if days is not None:
        body = (
            f"Hi {nm} — {owner} from {biz}: {days} days since your last visit (last focus: {focus_h}); no judgment. "
            f"Want me to hold {off} for a comeback slot? Reply YES — no auto-charge."
        )
    else:
        body = (
            f"Hi {nm} — {owner} from {biz}: it’s been a while — want me to hold {off} for a comeback slot? "
            f"Reply YES — no auto-charge."
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Long-absent customer; gentle winback with a named offer and no-pressure booking ask.",
        "template_name": "merchant_winback_customer_v1",
        "template_params": [nm, str(days if days is not None else "")],
    }


def _tpl_customer_lapsed_soft(cat, m, tr, c, facts) -> Dict[str, Any]:
    pk = tr.get("payload") or {}
    nm = ((c or {}).get("identity") or {}).get("name") or "there"
    owner = (m.get("identity") or {}).get("owner_first_name") or "Team"
    biz = (m.get("identity") or {}).get("name") or "us"
    slug = (m.get("category_slug") or "").lower()
    cd = (facts.get("customer") or {}).get("customer_derived") or {}
    days = cd.get("days_since_last_visit")
    lv = cd.get("customer_last_visit_iso")
    gap = f"{days} days since last visit" if days is not None else f"since {lv}" if lv else "your usual cadence"
    pref = cd.get("preference_anchor") or ""
    der = _d(facts)
    off_raw = der.get("best_live_offer_title") or (
        (facts.get("merchant", {}).get("offers_active") or [None])[0] or ""
    )
    off = lapsed_soft_offer_phrase(slug, str(off_raw) if off_raw else "")
    if "dent" in slug or slug == "dentists":
        hook = f"cleaning/check-up window ({gap})"
    elif "pharm" in slug:
        hook = f"refill rhythm ({gap})"
    elif "gym" in slug or slug == "gyms":
        hook = f"{pref or 'training'} check-in ({gap})"
    elif "salon" in slug:
        hook = f"style refresh ({gap})"
    elif "restaurant" in slug:
        hook = f"favourite order nudge ({gap})"
    else:
        hook = gap
    body = (
        f"Hi {nm} — {owner} from {biz}: {hook}. "
        f"Shall I block you on {off}? Reply YES with a preferred evening."
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Light-touch winback: category-appropriate hook plus one concrete offer to reply to.",
        "template_name": "merchant_lapsed_soft_v1",
        "template_params": [nm, str(days) if days is not None else str(lv or ""), off[:60]],
    }


def _tpl_trial_followup(cat, m, tr, c, facts) -> Dict[str, Any]:
    slug = (m.get("category_slug") or "").lower()
    bucket = category_bucket(slug)
    svc = _trial_service_word(bucket)
    der = _d(facts)
    off_raw = der.get("best_live_offer_title") or (
        (facts.get("merchant", {}).get("offers_active") or [None])[0]
    )
    offer = present_text(off_raw) or safe_offer_label(m, bucket, facts)
    nm = ((c or {}).get("identity") or {}).get("name") or "there"
    biz = (m.get("identity") or {}).get("name") or "studio"
    body = (
        f"Hi there, {biz} here — glad you tried us. We can hold your next {svc} slot this week under {offer}. "
        f"Want me to block one? Reply YES."
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Trial just ended; category-appropriate next slot + live offer gives a clear YES booking path.",
        "template_name": "merchant_trial_followup_v1",
        "template_params": [svc, offer[:60]],
    }


def _tpl_appointment_tomorrow(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = ((c or {}).get("identity") or {}).get("name") or "there"
    biz = (m.get("identity") or {}).get("name") or "clinic"
    pk = tr.get("payload") or {}
    svc = (pk.get("service_name") or "").strip()
    staff = (pk.get("staff_name") or "").strip()
    tw = (pk.get("time_window") or "").strip()
    bits = [x for x in (svc, tw, f"with {staff}" if staff else "") if x]
    detail = f" ({', '.join(bits)})" if bits else ""
    body = (
        f"Hi {nm}, {biz} is holding your appointment tomorrow{detail}. "
        f"Confirm this slot or move it? Reply CONFIRM or RESCHEDULE."
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Tomorrow booking needs a confirm or reschedule; optional service/time come from live calendar.",
        "template_name": "merchant_appt_tomorrow_v1",
        "template_params": [],
    }


def _tpl_supply_alert(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or "there"
    pk = tr.get("payload") or {}
    loc = present_text((m.get("identity") or {}).get("locality")) or "your area"
    mol_pt = present_text(pk.get("molecule"))
    batches = pk.get("affected_batches") or []
    batch_labels = [b for b in (present_text(x) for x in batches[:3]) if b]
    ch = (m.get("customer_aggregate") or {}).get("chronic_rx_count")
    ch_ok = ch is not None and str(ch).strip().lower() not in ("none", "null", "")

    if not mol_pt and not batch_labels and not ch_ok:
        body = (
            f"{nm}, one backend stock or customer-communication alert needs attention today for {loc} — "
            f"want WhatsApp + Google notice wording drafted? Reply YES."
        )
        mol_disp = "alert"
    else:
        mol = mol_pt or "a stocked medicine line"
        batch_bit = f", batches {', '.join(batch_labels)}" if batch_labels else ""
        roster = (
            f"~{ch} repeat-care patients on roster — "
            if ch_ok
            else "several repeat patients on roster — "
        )
        body = (
            f"{nm}, urgent stock alert: {mol}{batch_bit}. "
            f"{roster}want WhatsApp templates + pickup wording? I can draft both."
        )
        mol_disp = mol
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Stock issue affects named batches; Vera helps notify patients and organise pickup wording.",
        "template_name": "vera_supply_alert_v1",
        "template_params": [mol_disp[:80]],
    }


def _tpl_chronic_refill(cat, m, tr, c, facts) -> Dict[str, Any]:
    pk = tr.get("payload") or {}
    mols = [x for x in (present_text(x) for x in (pk.get("molecule_list") or [])) if x]
    due_raw = (pk.get("stock_runs_out_iso") or "").strip()
    due = due_raw[:10] if due_raw else ""
    nm = ((c or {}).get("identity") or {}).get("name") or "Sir/Madam"
    biz = (m.get("identity") or {}).get("name") or "pharmacy"
    slug = m.get("category_slug")
    dent = category_bucket(slug) == "dentist"
    if mols and due:
        mos = ", ".join(mols[:4])
        if dent:
            body = (
                f"Namaste — {biz}: {nm}’s follow-up ({mos}) is scheduled around {due}. "
                f"Reply CONFIRM to hold the visit or CALL if anything changed."
            )
        else:
            body = (
                f"Namaste — {biz}: {nm}’s repeat medicines ({mos}) are due for refill by {due}. "
                f"Reply CONFIRM for dispatch or CALL if anything changed."
            )
    elif mols:
        mos = ", ".join(mols[:4])
        if dent:
            body = (
                f"Namaste — {biz}: {nm}’s follow-up ({mos}) is due soon. "
                f"Reply CONFIRM to hold the visit or CALL if anything changed."
            )
        else:
            body = (
                f"Namaste — {biz}: {nm}’s repeat medicines ({mos}) are due for refill soon. "
                f"Reply CONFIRM for dispatch or CALL if anything changed."
            )
    else:
        body = chronic_refill_sparse_body(biz, nm, slug)
    mos_key = ", ".join(mols[:4]) if mols else ""
    return {
        "body": body,
        "cta": "binary_confirm_cancel",
        "send_as": "merchant_on_behalf",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Repeat care is due; wording matches pharmacy vs dental so it never sounds like the wrong shop.",
        "template_name": "merchant_refill_v1",
        "template_params": [mos_key, due],
    }


def _tpl_category_seasonal(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    trends = pk.get("trends") or []
    tr_s = _pretty_seasonal_trends(trends)
    der = _d(facts)
    loc = present_text(der.get("locality")) or present_text((m.get("identity") or {}).get("locality")) or "nearby"
    hero = present_text(der.get("best_live_offer_title")) or present_text(
        (facts.get("merchant", {}).get("offers_active") or [None])[0]
    )
    if not hero:
        hero = "your hero offer or SKU"
    weak_trends = (not trends) or (tr_s == "key SKUs in your category")
    if weak_trends:
        hook = f"seasonal pull is shifting around {loc} — lead with {hero} on Google + WhatsApp"
    else:
        hook = f"demand mix is moving — {tr_s}; {loc} searches still convert when the hero offer is obvious"
    body = (
        f"{nm.split(',')[0] if ',' in nm else nm}, {hook}. "
        f"Want a ready reorder checklist plus one broadcast to move it this week?"
    )
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Category demand mix is shifting; Vera turns trend deltas into a reorder list and one broadcast.",
        "template_name": "vera_category_seasonal_v1",
        "template_params": [tr_s if not weak_trends else hero[:60]],
    }


def _tpl_gbp_unverified(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    biz_nm = present_text((m.get("identity") or {}).get("name")) or "your business"
    path = _gbp_path_safe_for_bucket(category_bucket(m.get("category_slug")), pk.get("verification_path"))
    up = pk.get("estimated_uplift_pct")
    up_s = _pct(up) if up is not None else "meaningful"
    body = (
        f"{nm.split(',')[0] if ',' in nm else nm}, Google Business Profile still unverified — try {path} "
        f"(typical visibility uplift ~{up_s}). Want step-by-step screenshots I can walk you through?"
    )
    low = body.lower()
    if any(x in low for x in ("recall", "atorvastatin", "affected customers", "molecule")):
        body = (
            f"{biz_nm} is still unverified on Google Business Profile — postcard or phone verification "
            f"usually lifts visibility fast. Want step-by-step screenshots?"
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Listing is live but unverified; finishing verification usually unlocks cleaner maps presence.",
        "template_name": "vera_gbp_verify_v1",
        "template_params": [],
    }


def _tpl_cde(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or "there"
    b = category_bucket(m.get("category_slug"))
    honorific = "Dr. " if b == "dentist" else ""
    did = (tr.get("payload") or {}).get("digest_item_id")
    di = None
    if did:
        for d in cat.get("digest") or []:
            if d.get("id") == did:
                di = d
                break
    title = present_text((di or {}).get("title")) or "a continuing-education session"
    credits = (tr.get("payload") or {}).get("credits", 2)
    body = (
        f"{honorific}{nm}, heads-up — {credits}-credit session is open: {title}. "
        f"I can prep the calendar invite + one staff reminder so nobody misses registration — want me to draft those now?"
    )
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Credits session just opened; Vera reduces no-shows with invite text and a staff ping.",
        "template_name": "vera_cde_v1",
        "template_params": [title],
    }


def _tpl_competitor(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or "there"
    pk = tr.get("payload") or {}
    cn_raw = pk.get("competitor_name")
    off_raw = pk.get("their_offer")
    has_cn = cn_raw is not None and str(cn_raw).strip() != ""
    has_off = off_raw is not None and str(off_raw).strip() != ""
    dist = pk.get("distance_km")
    der = _d(facts)
    loc = der.get("locality") or (m.get("identity") or {}).get("locality") or "your pin"
    mine = der.get("best_live_offer_title") or (
        (der.get("active_offer_titles") or [None])[0] or "your live service+price"
    )
    views = der.get("views_30d")
    calls = der.get("calls_30d")
    dist_s = f"{dist} km" if dist not in (None, "", "?") else f"near {loc}"
    sal = "Dr. " if (m.get("category_slug") == "dentists") else ""
    if has_cn and has_off:
        cn, off = str(cn_raw).strip(), str(off_raw).strip()
        body = (
            f"{sal}{nm}, {cn} ({dist_s}) is fronting {off} while you still clock {views} views / 30d → {calls} calls from {loc}. "
            f"Want one sharper Google counter-line anchored on {mine} (no trash talk)? Shall I draft it now?"
        )
        tparam = cn[:80]
    else:
        body = (
            f"{sal}{nm}, a nearby competitor is pushing an aggressive intro offer around {loc} while you still clock "
            f"{views} views / 30d → {calls} calls. Want one sharper Google counter-line on {mine}? Shall I draft it now?"
        )
        tparam = loc[:80]
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Nearby rival moved on price or offer; owner keeps GBP honest with a counter-line on their own hero SKU.",
        "template_name": "vera_competitor_v1",
        "template_params": [tparam],
    }


def _tpl_perf_spike(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = (m.get("identity") or {}).get("owner_first_name") or _name_merchant(m).split(",")[0]
    pk = tr.get("payload") or {}
    der = _d(facts)
    hero = der.get("best_live_offer_title") or (
        (der.get("active_offer_titles") or [None])[0]
    )
    driver_raw = pk.get("likely_driver")
    driver = present(driver_raw, None) or "your latest post"
    driver_h = str(driver).replace("_", " ")
    metric_raw = pk.get("metric")
    delta_raw = pk.get("delta_pct")
    window_raw = pk.get("window")
    metric_h = humanize_metric(metric_raw)
    if (
        not is_blank(metric_raw)
        and not is_blank(delta_raw)
        and not is_blank(window_raw)
    ):
        mv = metric_h.lower()
        plural = mv in (
            "customer calls",
            "listing views",
            "orders",
            "bookings",
            "reviews",
        ) or mv.endswith("s")
        copula = "are" if plural else "is"
        body = (
            f"{nm}, {metric_h} {copula} up {_pct(delta_raw)} ({window_raw} vs baseline; likely from {driver_h}). "
            f"Want a tight 48-hour Google blurb on {hero or 'your hero SKU'} while momentum is hot?"
        )
    else:
        body = (
            f"{nm}, recent activity is trending up after {driver_h}. "
            f"Want a 48-hour Google blurb on {hero or 'your hero SKU'}?"
        )
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Uptick in calls or views; Vera rides the wave with a short, timely listing blurb.",
        "template_name": "vera_perf_spike_v1",
        "template_params": [],
    }


def _audience_noun(slug: Optional[str]) -> str:
    b = category_bucket(slug or "")
    return {
        "dentist": "patients",
        "pharmacy": "repeat customers",
        "restaurant": "diners",
        "fitness": "members",
        "salon": "clients",
    }.get(b, "customers")


def _default_focus_for_lapsed(bucket: str) -> str:
    return {
        "dentist": "routine dental care",
        "pharmacy": "refills",
        "fitness": "training",
        "salon": "grooming",
        "restaurant": "orders",
    }.get(bucket, "visits")


def _lapsed_hard_focus_display(bucket: str, focus_raw: Any) -> str:
    ft = present_text(focus_raw)
    if not ft:
        return _default_focus_for_lapsed(bucket)
    low = str(ft).lower().replace("_", " ")
    fitness_hits = ("weight", "yoga", "gym", "fitness", "membership", "class", "trial")
    dental_hits = ("dental", "cleaning", "root", "ortho", "tooth", "whiten")
    if bucket != "fitness" and any(x in low for x in fitness_hits):
        return _default_focus_for_lapsed(bucket)
    if bucket != "dentist" and any(x in low for x in dental_hits):
        return _default_focus_for_lapsed(bucket)
    return str(ft).replace("_", " ").strip()


def _safe_merchant_anchors(m: dict, facts: dict) -> dict:
    """Concrete, merchant-only facts for safe templates (Patch L/M) — no trigger payload."""
    d = _d(facts)
    mer = facts.get("merchant") or {}
    ident_m = m.get("identity") or {}
    ident = mer.get("identity") or {
        "locality": ident_m.get("locality"),
        "name": ident_m.get("name"),
        "owner_first_name": ident_m.get("owner_first_name"),
    }
    perf = mer.get("performance") or (m.get("performance") or {})
    sub = mer.get("subscription") or (m.get("subscription") or {})
    slug = m.get("category_slug")
    bucket = category_bucket((slug or "").lower())
    offer = safe_offer_label(m, bucket, facts)
    views = present(perf.get("views"), None)
    if views is None:
        views = d.get("views_30d")
    calls = present(perf.get("calls"), None)
    if calls is None:
        calls = d.get("calls_30d")
    gap = d.get("conversion_gap_ctr_minus_peer")
    peer = (facts.get("peer_stats_summary") or {}).get("avg_ctr")
    ctr_m = present(perf.get("ctr"), None)
    loc = present_text(ident.get("locality")) or present_text(d.get("locality")) or "your area"
    biz = present_text(ident.get("name")) or "your shop"
    plan = present_text(sub.get("plan"))
    days_rem = sub.get("days_remaining")
    bits = []
    if views is not None:
        bits.append(f"{views} listing views/30d")
    if calls is not None:
        bits.append(f"{calls} calls/30d")
    metric_line = " and ".join(bits) if bits else ""
    ctr_line = ""
    if ctr_m is not None and peer is not None:
        ctr_line = f"CTR {ctr_m} vs peer avg {peer}"
        if gap is not None:
            ctr_line += f" (gap {gap})"
    elif gap is not None:
        ctr_line = f"CTR gap vs peer ~{gap}"
    nm = present_text(ident.get("owner_first_name")) or _name_merchant(m).split(",")[0].strip()
    return {
        "nm": nm,
        "loc": loc,
        "biz": biz,
        "offer": offer,
        "views": views,
        "calls": calls,
        "metric_line": metric_line,
        "ctr_line": ctr_line,
        "plan": plan,
        "days_rem": days_rem,
        "audience": _audience_noun(slug),
        "slug": slug,
    }


def _tpl_dormant(cat, m, tr, c, facts) -> Dict[str, Any]:
    nm = _name_merchant(m)
    pk = tr.get("payload") or {}
    days_raw = pk.get("days_since_last_merchant_message")
    days = present(days_raw, None)
    first = nm.split(",")[0] if "," in nm else nm
    bucket = category_bucket((m.get("category_slug") or "").lower())
    offer = safe_offer_label(m, bucket, facts)
    loc = present_text((m.get("identity") or {}).get("locality")) or present_text(_d(facts).get("locality")) or "your area"
    body = ""
    if days is not None:
        try:
            d_int = int(days)
            body = (
                f"{first}, {d_int} quiet days is enough to lose local visibility around {loc}. "
                f"Fastest restart: 3 fresh Google posts around {offer}. Want me to prep all three today?"
            )
        except (TypeError, ValueError):
            pass
    if not body:
        body = (
            f"{first}, local visibility cools fast when the listing sits unchanged. "
            f"I can prep 3 fresh Google posts around {offer} today — want them?"
        )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": tr.get("suppression_key") or "",
        "rationale": "Quiet inbox; fresh Google posts are low effort and visible to nearby searchers.",
        "template_name": "vera_dormant_v1",
        "template_params": [],
    }


def _tpl_cross_domain_safe(cat, m, tr, c, facts) -> Dict[str, Any]:
    """Merchant-grounded safe copy (Patch L/M) + deterministic archetype variants."""
    A = _safe_merchant_anchors(m, facts)
    nm, loc, biz, offer = A["nm"], A["loc"], A["biz"], A["offer"]
    pk = tr.get("payload") or {}
    raw_deadline = pk.get("deadline_iso") or tr.get("expires_at")
    due_txt = human_short_date(raw_deadline) or "this week"
    orig_kind = tr.get("_semantic_original_kind") or "default"
    orig = str(orig_kind).lower()
    sk = tr.get("suppression_key") or ""
    arch = safe_archetype_for_kind(orig_kind)
    vi = lambda salt, n: safe_variant_index(f"{sk}|{salt}", n)

    metric_paren = f" ({A['metric_line']})" if A["metric_line"] else ""
    sig_frag = ""
    if A["ctr_line"]:
        sig_frag = f" {A['ctr_line']}."
    elif A["metric_line"]:
        sig_frag = f" Recent traction{metric_paren}."

    offer_openers = [
        (
            f"{nm}, your {offer} looks under-promoted to {loc} shoppers — "
            f"want a sharper Google + WhatsApp push?"
        ),
        (
            f"{nm}, {biz} has a strong live angle with {offer} — "
            f"want one tight {loc} post + WhatsApp line?"
        ),
        (
            f"{nm}, {offer} could carry more footfall signal in {loc}{metric_paren} — "
            f"want both drafts?"
        ),
    ]
    offer_ctas = [
        " Reply CONFIRM and I’ll paste both.",
        " Text CONFIRM if you want the full wording.",
        " Say CONFIRM to get the draft.",
    ]

    checklist_with_dl = [
        (
            f"{nm}, one dated account item for {biz} needs clearing before {due_txt} so the listing stays clean in {loc} — "
            f"want the 3-step checklist + calm WhatsApp line?"
        ),
        (
            f"{nm}, quick heads-up: a profile checkpoint for {biz} before {due_txt} — "
            f"want the tight checklist + customer wording?"
        ),
        (
            f"{nm}, there’s a dated listing task to close before {due_txt} in {loc} — "
            f"want me to bundle a 3-bullet checklist + WhatsApp text?"
        ),
    ]
    checklist_no_dl = [
        (
            f"{nm}, one listing hygiene checkpoint for {biz} in {loc} is worth closing today{metric_paren} — "
            f"want a 3-point checklist + WhatsApp wording?"
        ),
        (
            f"{nm}, {biz} still has an open profile task{metric_paren} — "
            f"want the tight checklist + what to tell walk-ins?"
        ),
        (
            f"{nm}, a short operator checklist for {loc} would cut inbox churn around {offer} — "
            f"want me to bundle it + WhatsApp text?"
        ),
    ]
    checklist_ctas = [
        " Reply YES to get it.",
        " Text YES and I’ll send both.",
        " Say YES if you want the bundle.",
    ]

    campaign_openers = [
        (
            f"{nm}, demand tied to {offer} in {loc} still looks movable{metric_paren} — "
            f"want one locality-tight promo line + GBP post?"
        ),
        (
            f"{nm}, {loc} intent is active; pushing {offer} on Google + WhatsApp is low effort{metric_paren} — "
            f"want a single ready line?"
        ),
        (
            f"{nm}, one crisp {loc} campaign around {offer} could lift calls this week — "
            f"want post + message drafts?"
        ),
    ]
    campaign_ctas = [
        " Reply YES to draft.",
        " Text YES for the pair.",
        " Say YES if you want both.",
    ]

    reactivate_openers = [
        (
            f"{nm}, a short “come back this week” note around {offer} could wake silent {A['audience']} — "
            f"want the exact wording?"
        ),
        (
            f"{nm}, gentle win-back for {biz} in {loc}: anchor it on {offer} — "
            f"want me to prep the WhatsApp text?"
        ),
        (
            f"{nm}, one low-effort reactivation ping mentioning {offer} usually beats a generic blast — "
            f"want the line?"
        ),
    ]
    reactivate_ctas = [
        " Reply YES to see the draft.",
        " Text YES and I’ll paste it.",
        " Say YES for the text.",
    ]

    if A["metric_line"] or A["ctr_line"]:
        insight_openers = [
            (
                f"{nm}, your listing signals for {biz}{metric_paren}:{sig_frag} "
                f"Want a 2-bullet “so what” + next step?"
            ),
            (
                f"{nm}, quick read: {biz} in {loc}{metric_paren}.{sig_frag} "
                f"Want the recap in two bullets?"
            ),
            (
                f"{nm}, one concrete takeaway from recent traction for {biz}{metric_paren} — "
                f"want me to spell it out + one action?"
            ),
        ]
    else:
        insight_openers = [
            (
                f"{nm}, {offer} plus how {biz} shows in {loc} suggests a small listing tweak — "
                f"want a 2-bullet readout + next step?"
            ),
            (
                f"{nm}, there’s a tight ‘so what’ from local discovery for {biz} — "
                f"want the 2-bullet recap?"
            ),
            (
                f"{nm}, a quick signal pass on {biz} could sharpen this week’s move around {offer} — "
                f"want the summary?"
            ),
        ]
    insight_ctas = [
        " Reply YES for the bullets.",
        " Text YES to get the recap.",
        " Say YES if you want it.",
    ]

    plan_note = ""
    if A["plan"]:
        plan_note = f" ({A['plan']}"
        if A["days_rem"] is not None:
            plan_note += f", {A['days_rem']} days left on plan"
        plan_note += ")"

    generic_openers = [
        (
            f"{nm}, one timely customer-facing note for {biz} in {loc}{plan_note} — "
            f"anchor it on {offer}; want WhatsApp + Google wording?"
        ),
        (
            f"{nm}, I can bundle one short customer notice + listing line for {biz}{metric_paren} — "
            f"centered on {offer} — want both?"
        ),
        (
            f"{nm}, low-friction growth nudge for {loc}: feature {offer} plainly in the next post — "
            f"want draft copy?"
        ),
    ]
    generic_ctas = [
        " Reply YES to draft.",
        " Text YES for the wording.",
        " Say YES if you want it.",
    ]

    if arch == "offer_draft":
        body = offer_openers[vi("o", len(offer_openers))] + offer_ctas[vi("c", len(offer_ctas))]
        tname = "vera_safe_offer_draft_v1"
        cta = "binary_confirm_cancel"
    elif arch == "checklist":
        has_deadline = (pk.get("deadline_iso") not in (None, "")) or (tr.get("expires_at") not in (None, ""))
        pool = checklist_with_dl if has_deadline else checklist_no_dl
        body = pool[vi("o", len(pool))] + checklist_ctas[vi("c", len(checklist_ctas))]
        tname = "vera_safe_checklist_v1"
        cta = "binary_yes_no"
    elif arch == "quick_campaign":
        body = campaign_openers[vi("o", len(campaign_openers))] + campaign_ctas[vi("c", len(campaign_ctas))]
        tname = "vera_safe_quick_campaign_v1"
        cta = "binary_yes_no"
    elif arch == "customer_reactivation":
        body = reactivate_openers[vi("o", len(reactivate_openers))] + reactivate_ctas[vi("c", len(reactivate_ctas))]
        tname = "vera_safe_customer_reactivate_v1"
        cta = "binary_yes_no"
    elif arch == "merchant_insight":
        body = insight_openers[vi("o", len(insight_openers))] + insight_ctas[vi("c", len(insight_ctas))]
        tname = "vera_safe_merchant_insight_v1"
        cta = "binary_yes_no"
    else:
        body = generic_openers[vi("o", len(generic_openers))] + generic_ctas[vi("c", len(generic_ctas))]
        tname = "vera_safe_generic_v1"
        cta = "binary_yes_no"

    return {
        "body": body[:2000],
        "cta": cta,
        "send_as": "vera",
        "suppression_key": sk,
        "rationale": f"Cross-domain safe ({arch}): sanitized trigger (was {orig}); variant keyed to suppression.",
        "template_name": tname,
        "template_params": [nm[:40], loc, arch],
    }


TEMPLATE_DISPATCH = {
    "research_digest": _tpl_research_digest,
    "regulation_change": _tpl_regulation_change,
    "recall_due": _tpl_recall_due,
    "perf_dip": _tpl_perf_dip,
    "renewal_due": _tpl_renewal_due,
    "festival_upcoming": _tpl_festival,
    "wedding_package_followup": _tpl_bridal,
    "curious_ask_due": _tpl_curious,
    "winback_eligible": _tpl_winback_eligible,
    "ipl_match_today": _tpl_ipl,
    "review_theme_emerged": _tpl_review_theme,
    "milestone_reached": _tpl_milestone,
    "active_planning_intent": _tpl_planning,
    "seasonal_perf_dip": _tpl_seasonal_dip,
    "customer_lapsed_hard": _tpl_customer_lapsed_hard,
    "customer_lapsed_soft": _tpl_customer_lapsed_soft,
    "trial_followup": _tpl_trial_followup,
    "appointment_tomorrow": _tpl_appointment_tomorrow,
    "supply_alert": _tpl_supply_alert,
    "chronic_refill_due": _tpl_chronic_refill,
    "category_seasonal": _tpl_category_seasonal,
    "gbp_unverified": _tpl_gbp_unverified,
    "cde_opportunity": _tpl_cde,
    "competitor_opened": _tpl_competitor,
    "perf_spike": _tpl_perf_spike,
    "dormant": _tpl_dormant,
    "dormant_with_vera": _tpl_dormant,
    "cross_domain_safe": _tpl_cross_domain_safe,
}
