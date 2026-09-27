"""Null-safe and merchant-facing hydration helpers for templates and facts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

# Raw API / seed keys → words a merchant would say
METRIC_LABELS = {
    "review_count": "reviews",
    "reviews": "reviews",
    "calls": "customer calls",
    "views": "listing views",
    "ctr": "click-through rate",
    "orders": "orders",
    "bookings": "bookings",
}

VERIFICATION_PATH_LABELS = {
    "postcard_or_phone_call": "postcard or phone verification",
    "postcard": "postcard verification",
    "phone": "phone verification",
}


def present(value: Any, fallback: Optional[Any] = None) -> Any:
    """Treat None, '', and string 'none' as missing; return fallback."""
    if value is None:
        return fallback
    if value == "":
        return fallback
    if str(value).strip().lower() == "none":
        return fallback
    return value


def present_text(value: Any) -> Optional[str]:
    """
    Merchant-facing string guard: None / blank / 'none' / punctuation-only → None.
    Use so sparse payloads never produce half-sentences like 'today's match at .'.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    low = s.lower()
    if low in ("none", "null", "n/a", "na", "undefined", "nil"):
        return None
    if re.fullmatch(r"[\s\W_]+", s):
        return None
    if s in ('""', "''", "“”", "‘’", "``"):
        return None
    return s


def is_blank(value: Any) -> bool:
    return present(value, None) is None and value is not False


def humanize_metric(metric: Any) -> str:
    """Turn payload metric keys into merchant language."""
    if is_blank(metric):
        return "activity"
    s = str(metric).strip()
    low = s.lower()
    if low in METRIC_LABELS:
        return METRIC_LABELS[low]
    if "_" in s:
        return s.replace("_", " ")
    return s


def humanize_verification_path(path: Any) -> str:
    if is_blank(path):
        return "postcard or phone verification"
    s = str(path).strip()
    low = s.lower()
    if low in VERIFICATION_PATH_LABELS:
        return VERIFICATION_PATH_LABELS[low]
    if "_" in s:
        return s.replace("_", " ")
    return s


def human_short_date(v: Any) -> Optional[str]:
    """Turn ISO datetimes into short merchant-facing dates like '02 May'."""
    s = present_text(v)
    if not s:
        return None
    try:
        norm = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(norm)
        return dt.strftime("%d %b")
    except Exception:
        return s[:10] if len(s) >= 10 else s


def category_bucket(category_slug: Optional[str]) -> str:
    s = (category_slug or "").lower()
    if "dent" in s or s == "dentists":
        return "dentist"
    if "pharm" in s:
        return "pharmacy"
    if "gym" in s or "yoga" in s or "studio" in s:
        return "fitness"
    if "salon" in s:
        return "salon"
    if "restaurant" in s or "cafe" in s:
        return "restaurant"
    return "generic"


def recall_reminder_phrase(category_slug: Optional[str]) -> str:
    """Opening line for scheduled follow-up / recall (customer message)."""
    b = category_bucket(category_slug)
    if b == "dentist":
        return "your cleaning and check-up are due for a visit"
    if b == "pharmacy":
        return "your medicine refill cycle is due"
    if b == "fitness":
        return "it’s a good time for a comeback class or quick assessment"
    if b == "salon":
        return "your grooming revisit is due"
    if b == "restaurant":
        return "we’d love to welcome you back with a loyalty treat"
    return "it’s time for your next visit with us"


def recall_offer_line(category_slug: Optional[str], offers: list) -> str:
    """Price/offer line for recall — category-appropriate default."""
    if offers:
        o0 = present_text(offers[0])
        if o0:
            return o0
        s = str(offers[0]).strip()
        if s:
            return s
    b = category_bucket(category_slug)
    if b == "dentist":
        return "your follow-up cleaning slot"
    if b == "pharmacy":
        return "Free Home Delivery > ₹499"
    if b == "fitness":
        return "First Month @ ₹499"
    if b == "salon":
        return "Haircut @ ₹99"
    if b == "restaurant":
        return "your combo or loyalty plate"
    return "your active offer"


def lapsed_soft_offer_phrase(category_slug: Optional[str], off: str) -> str:
    """Avoid ‘standing offer’ for pharmacy; keep natural per vertical."""
    b = category_bucket(category_slug)
    low = (off or "").lower()
    if b == "pharmacy":
        return off if off and "standing" not in low else "your repeat-order deal"
    if b == "dentist":
        return off if off and "standing" not in low else "your check-up package"
    if b == "restaurant":
        return off if off else "your house favourite combo"
    if b == "fitness":
        return off if off else "your comeback class pack"
    if b == "salon":
        return off if off and "standing" not in low else "your grooming package"
    return off if off and "standing" not in low else "your current promo"


def chronic_refill_sparse_body(biz: str, cust_name: str, category_slug: Optional[str]) -> str:
    """Sparse chronic follow-up: pharmacy vs dental wording."""
    b = category_bucket(category_slug)
    if b == "dentist":
        return (
            f"Namaste — {biz}: {cust_name}, a routine follow-up is due from the last visit. "
            f"Reply CONFIRM to hold a slot or CALL if anything changed."
        )
    return (
        f"Namaste — {biz}: {cust_name} has a routine refill due from the last visit. "
        f"Reply CONFIRM for dispatch or CALL if anything changed."
    )
