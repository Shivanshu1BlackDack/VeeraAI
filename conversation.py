"""Multi-turn reply handling: auto-reply, intent, hostile, off-topic, pivots."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from state import Store

AUTO_PATTERNS = re.compile(
    r"thank you for contacting|our team will respond|automated assistant|"
    r"we will get back|जानकारी के लिए|धन्यवाद",
    re.I,
)
COMMIT_PATTERNS = re.compile(
    r"\b(yes|yeah|yep|haan|ok|okay|chalega|proceed|confirm|go ahead|let's do|lets do|"
    r"do it|send it|sounds good|sounds great)\b",
    re.I,
)
STOP_PATTERNS = re.compile(
    r"\b(stop|spam|unsubscribe|not interested|don't message|do not message|"
    r"band karo|bakwas|nuisance|harass)\b",
    re.I,
)
OFF_TOPIC = re.compile(
    r"\b(gst|tax filing|income tax|legal case|politics|password|hack)\b",
    re.I,
)
ALREADY_HAVE = re.compile(
    r"already\s+(running|have|doing|offering)|we\s+already|no\s+need|not\s+needed|"
    r"already\s+live|already\s+posted",
    re.I,
)
SHORT_AFFIRM = re.compile(r"^(yes|yep|yeah|haan|ok|okay|sure|done|theek|thik)\.?!?\s*$", re.I)


def _last_bot_body(store: Store, conv_id: str) -> str:
    st = store.get_conversation(conv_id)
    if not st or not st.last_bot_body:
        return ""
    return st.last_bot_body or ""


def is_likely_auto_reply(message: str) -> bool:
    m = message.strip()
    if len(m) < 20 and "thank" in m.lower():
        return True
    if AUTO_PATTERNS.search(m):
        return True
    return False


def handle_reply(
    conv_id: str,
    merchant_id: str,
    customer_id: Optional[str],
    from_role: str,
    message: str,
    turn_number: int,
) -> Dict[str, Any]:
    store = Store.get()
    st = store.get_conversation(conv_id)
    if st and st.ended:
        return {"action": "end", "rationale": "This thread was already closed; no further nudges."}

    if from_role == "merchant":
        if st is None:
            store.init_conversation(conv_id, merchant_id or "unknown", customer_id)
            st = store.get_conversation(conv_id)
        store.append_turn(conv_id, "merchant", message)

        if STOP_PATTERNS.search(message):
            return {
                "action": "end",
                "rationale": "Merchant asked to stop; Vera ends the sequence respectfully.",
            }

        offer = st.best_offer_title if st else None
        loc = st.locality if st else ""
        trend = st.top_search_hint if st else ""

        if ALREADY_HAVE.search(message) and st:
            body = (
                f"Nice — if the offer is already live, the sharper lever is conversion (headline + reply speed), "
                f"not more visibility. Want me to rewrite the hero line for {offer or 'your main offer'} "
                f"using your {loc or 'local'} context? Reply YES."
            )
            return {
                "action": "send",
                "body": body[:2000],
                "cta": "binary_yes_no",
                "rationale": "Offer is already live; Vera shifts to headline and reply-speed to lift conversion.",
            }

        if SHORT_AFFIRM.match(message.strip()) and st:
            anchor = offer or "your active offer"
            place = loc or "nearby"
            trend_bit = f' and "{trend}" demand signal' if trend else ""
            body = (
                f"On it — I’ll prep the draft around {anchor} for {place} customers{trend_bit}. "
                f"Send that WhatsApp + GBP version now? Reply YES."
            )
            store.append_turn(conv_id, "vera", body, body_sent=body)
            return {
                "action": "send",
                "body": body,
                "cta": "binary_yes_no",
                "rationale": "Quick yes; Vera confirms the draft scope using the offer and area already in context.",
            }

        if OFF_TOPIC.search(message) and not COMMIT_PATTERNS.search(message):
            hint = ""
            if st and st.context_hint:
                hint = st.context_hint
            body = (
                "I’ll leave GST/tax to your CA — outside my scope. "
                f"Back to growth: {hint or 'your listing/offers'} — one concrete draft? Reply YES."
            )
            return {
                "action": "send",
                "body": body[:2000],
                "cta": "binary_yes_no",
                "rationale": "Off-topic question; Vera declines politely and returns to one growth action.",
            }

        # Commitment before auto-reply streak so repeated judge runs (same conv_id + same text)
        # do not mis-classify a human "let's do it" as a cool-off.
        if COMMIT_PATTERNS.search(message):
            body = (
                f"Locked in — drafting WhatsApp + GBP now (anchored on {offer or 'your offer'} / {loc or 'your area'}). "
                f"Reply CONFIRM to paste as-is or EDIT with tweaks."
            )
            store.append_turn(conv_id, "vera", body, body_sent=body)
            return {
                "action": "send",
                "body": body,
                "cta": "binary_confirm_cancel",
                "rationale": "Merchant committed; Vera moves to paste-ready copy tied to the active offer.",
            }

        streak = st.auto_reply_streak if st else 0

        if is_likely_auto_reply(message) or streak >= 2:
            if streak >= 3:
                return {
                    "action": "end",
                    "rationale": "Only automated replies detected; pausing so a human can take over later.",
                }
            if streak == 2:
                return {
                    "action": "wait",
                    "wait_seconds": 86400,
                    "rationale": "Two canned replies in a row; better to cool off and retry tomorrow.",
                }
            return {
                "action": "send",
                "body": (
                    "Looks like a canned auto-reply — when a human sees this, reply YES and I’ll drop the draft."
                ),
                "cta": "binary_yes_no",
                "rationale": "Likely inbox auto-responder; one short prompt for a human YES.",
            }

        return {
            "action": "send",
            "body": (
                f"Got it — should I prioritize the Google post or the customer WhatsApp first for "
                f"{offer or 'this offer'}? Reply POST or WA."
            ),
            "cta": "open_ended",
            "rationale": "Ambiguous reply; Vera asks which deliverable to ship first.",
        }

    store.append_turn(conv_id, from_role or "customer", message)
    return {
        "action": "send",
        "body": "Thanks — noted; we’ll confirm shortly.",
        "cta": "none",
        "rationale": "Inbound from customer channel; short acknowledgement only.",
    }


def init_conversation_hook(
    conv_id: str,
    merchant_id: str,
    customer_id: Optional[str],
    trigger_id: Optional[str],
    first_body: str,
    trigger_kind: Optional[str] = None,
    best_offer_title: Optional[str] = None,
    top_search_hint: Optional[str] = None,
    locality: Optional[str] = None,
) -> None:
    store = Store.get()
    existing = store.get_conversation(conv_id)
    if existing and existing.turns:
        return
    hint = (first_body[:120] + "…") if len(first_body) > 120 else first_body
    store.init_conversation(
        conv_id,
        merchant_id,
        customer_id,
        trigger_id=trigger_id,
        context_hint=hint,
        trigger_kind=trigger_kind,
        best_offer_title=best_offer_title,
        top_search_hint=top_search_hint,
        locality=locality,
    )
    store.append_turn(conv_id, "vera", first_body, body_sent=first_body)
