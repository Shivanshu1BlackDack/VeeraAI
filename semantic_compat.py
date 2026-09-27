"""Trigger kind × merchant category semantic firewall (cross-wire safe compose)."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Set, Tuple

from hydration import category_bucket, present_text
from playbooks import KIND_ALIASES

# category_bucket() → dentist | pharmacy | fitness | salon | restaurant | generic

_KIND_ALLOWED_BUCKETS: Dict[str, Set[str]] = {
    "research_digest": {"dentist", "pharmacy"},
    "regulation_change": {"dentist", "pharmacy"},
    "chronic_refill_due": {"pharmacy"},
    "supply_alert": {"pharmacy"},
    "cde_opportunity": {"dentist"},
    "wedding_package_followup": {"salon"},
    "ipl_match_today": {"restaurant"},
    "seasonal_perf_dip": {"fitness"},
    "trial_followup": {"fitness", "salon"},
}

_PHARMA_TREND_FRAGMENTS = (
    "ors_",
    "sunscreen",
    "antifungal",
    "cold_cough",
    "metformin",
    "atorvastatin",
    "tablet",
    "vaccine",
    "rx",
    "recall",
    "batch",
    "molecule",
)
_FOOD_TREND_FRAGMENTS = ("thali", "biryani", "dosa", "combo", "menu_roll", "lunch_", "dine")

_DENTAL_HINTS = ("dental", "tooth", "aligner", "ortho", "whitening", "cleaning @", "smile studio")
_FOOD_OFFER_HINTS = ("pizza", "thali", "restaurant", "cafe", "combo", "biryani", "@ ₹", "delivery")
_FITNESS_HINTS = ("yoga", "gym", "fitness", "membership", "class", "trainer")

_MEDICAL_LEAK_TOKENS = ("atorvastatin", "molecule_list", "recall:", "rx_batch", "chronic_rx", "metformin")


def canonical_kind(kind: str) -> str:
    k = (kind or "default").lower().strip()
    return KIND_ALIASES.get(k, k)


def safe_variant_index(suppression_key: str, modulus: int) -> int:
    """Deterministic variant pick from suppression key (stable per merchant/trigger)."""
    if modulus <= 0:
        return 0
    h = int(hashlib.sha256((suppression_key or "x").encode("utf-8")).hexdigest()[:8], 16)
    return h % modulus


def safe_archetype_for_kind(kind: str) -> str:
    """Maps original trigger kind → safe copy family (Patch J)."""
    k = canonical_kind(kind)
    if k in (
        "active_planning_intent",
        "trial_followup",
        "wedding_package_followup",
        "seasonal_perf_dip",
        "ipl_match_today",
    ):
        return "offer_draft"
    if k in ("regulation_change", "cde_opportunity", "supply_alert"):
        return "checklist"
    if k in ("chronic_refill_due", "recall_due", "customer_lapsed_hard", "customer_lapsed_soft"):
        return "customer_reactivation"
    if k in ("research_digest", "review_theme_emerged", "curious_ask_due"):
        return "merchant_insight"
    if k in (
        "category_seasonal",
        "perf_spike",
        "perf_dip",
        "milestone_reached",
        "winback_eligible",
        "festival_upcoming",
        "competitor_opened",
    ):
        return "quick_campaign"
    return "generic"


# Native templates already vertical-agnostic; never force bland safe archetype (Patch N).
_NATIVE_ALWAYS_OK = frozenset(
    {
        "dormant_with_vera",
        "dormant",
        "festival_upcoming",
        "renewal_due",
        "milestone_reached",
        "perf_dip",
        "appointment_tomorrow",
    }
)


def trigger_category_compatible(kind: str, category_slug: str, trigger: Dict) -> bool:
    k = canonical_kind(kind)
    bucket = category_bucket(category_slug)

    if k in _NATIVE_ALWAYS_OK:
        return True

    if k == "active_planning_intent":
        return _planning_intent_compatible(bucket, trigger)
    if k == "category_seasonal":
        return _category_seasonal_compatible(bucket, trigger)
    if k == "competitor_opened":
        return _competitor_opened_compatible(bucket, trigger)
    if k == "perf_spike":
        return _perf_spike_compatible(bucket, trigger)
    if k == "review_theme_emerged":
        return _review_theme_compatible(bucket, trigger)
    if k == "gbp_unverified":
        return _gbp_unverified_compatible(bucket, trigger)
    if k == "customer_lapsed_hard":
        return _customer_lapsed_hard_compatible(bucket, trigger)

    allowed = _KIND_ALLOWED_BUCKETS.get(k)
    if allowed is None:
        return True
    return bucket in allowed


def _planning_intent_compatible(bucket: str, trigger: Dict) -> bool:
    topic = str((trigger.get("payload") or {}).get("intent_topic") or "").lower()
    gymish = any(x in topic for x in ("yoga", "kids", "camp", "gym", "membership", "fitness", "class"))
    foodish = any(
        x in topic
        for x in ("thali", "corporate", "bulk", "lunch", "catering", "menu", "dine", "delivery")
    )
    if gymish and bucket != "fitness":
        return False
    if foodish and bucket != "restaurant":
        return False
    return True


def _category_seasonal_compatible(bucket: str, trigger: Dict) -> bool:
    trends = (trigger.get("payload") or {}).get("trends") or []
    blob = " ".join(str(t).lower() for t in trends)
    if any(p in blob for p in _PHARMA_TREND_FRAGMENTS):
        return bucket == "pharmacy"
    if any(p in blob for p in _FOOD_TREND_FRAGMENTS):
        return bucket == "restaurant"
    return True


def _competitor_opened_compatible(bucket: str, trigger: Dict) -> bool:
    pk = trigger.get("payload") or {}
    blob = f"{pk.get('competitor_name', '')} {pk.get('their_offer', '')}".lower()
    if any(h in blob for h in _DENTAL_HINTS):
        return bucket == "dentist"
    if any(h in blob for h in _FOOD_OFFER_HINTS):
        return bucket == "restaurant"
    if any(h in blob for h in _FITNESS_HINTS):
        return bucket == "fitness"
    return True


def _perf_spike_compatible(bucket: str, trigger: Dict) -> bool:
    driver = str((trigger.get("payload") or {}).get("likely_driver") or "").lower()
    if any(x in driver for x in ("yoga", "kids", "gym", "fitness", "class", "membership")):
        return bucket == "fitness"
    if any(x in driver for x in ("dental", "whitening", "cleaning", "aligner")):
        return bucket == "dentist"
    if any(x in driver for x in ("thali", "lunch", "delivery", "dine", "menu")):
        return bucket == "restaurant"
    return True


def _review_theme_compatible(bucket: str, trigger: Dict) -> bool:
    theme = str((trigger.get("payload") or {}).get("theme") or "").lower()
    if "delivery" in theme or theme == "delivery_late" or "packaging" in theme or "portion" in theme:
        return bucket == "restaurant"
    return True


def _gbp_unverified_compatible(bucket: str, trigger: Dict) -> bool:
    try:
        blob = json.dumps(trigger, ensure_ascii=False).lower()
    except Exception:
        blob = str(trigger).lower()
    if any(t in blob for t in _MEDICAL_LEAK_TOKENS):
        return bucket == "pharmacy"
    return True


def _customer_lapsed_hard_compatible(bucket: str, trigger: Dict) -> bool:
    focus = str((trigger.get("payload") or {}).get("previous_focus") or "").lower()
    if any(x in focus for x in ("weight", "yoga", "gym", "fitness", "membership", "class")):
        return bucket == "fitness"
    if any(x in focus for x in ("dental", "root", "cleaning", "ortho", "tooth")):
        return bucket == "dentist"
    return True


def minimal_safe_payload(original: Dict) -> Dict[str, Any]:
    """Strip specialist fields so build_facts_pack / LLM cannot resurrect wrong-domain semantics."""
    pk = (original.get("payload") or {}) if isinstance(original, dict) else {}
    out: Dict[str, Any] = {}
    for key in ("deadline_iso", "expires_at", "due_date"):
        v = present_text(pk.get(key))
        if v:
            out[key] = v
    return out


def prepare_trigger_for_compose(trigger: Dict, category_slug: str) -> Tuple[Dict, str, bool]:
    kind_raw = trigger.get("kind") or "default"
    k = canonical_kind(kind_raw)
    if trigger_category_compatible(kind_raw, category_slug, trigger):
        return trigger, k, False
    t2 = copy.deepcopy(trigger)
    t2["_semantic_original_kind"] = k
    t2["kind"] = "cross_domain_safe"
    t2["payload"] = minimal_safe_payload(t2)
    return t2, "cross_domain_safe", True


def planning_topic_plausible_for_bucket(bucket: str, topic_lower: str) -> bool:
    if not topic_lower.strip():
        return False
    gymish = any(x in topic_lower for x in ("yoga", "kids", "camp", "gym", "membership", "fitness", "class"))
    foodish = any(
        x in topic_lower
        for x in ("thali", "corporate", "bulk", "lunch", "catering", "menu", "dine", "delivery")
    )
    if gymish:
        return bucket == "fitness"
    if foodish:
        return bucket == "restaurant"
    return True
