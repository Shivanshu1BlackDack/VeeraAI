"""Unit tests: store, composition, conversation handlers."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["LLM_API_KEY"] = ""  # force template path in tests

from state import Store  # noqa: E402
from composer import compose_message  # noqa: E402
from conversation import handle_reply, is_likely_auto_reply  # noqa: E402
from hydration import human_short_date, humanize_metric, humanize_verification_path, present, present_text  # noqa: E402
from semantic_compat import trigger_category_compatible  # noqa: E402
from signals import body_banned_internal, body_has_url, build_facts_pack  # noqa: E402


@pytest.fixture(autouse=True)
def reset_store():
    Store.reset_for_tests()
    yield
    Store.reset_for_tests()


def test_context_idempotent():
    store = Store.get()
    a, ok, _ = store.push_context("merchant", "m1", 1, {"x": 1})
    assert a is True
    a_dup, _, _ = store.push_context("merchant", "m1", 1, {"x": 1})
    assert a_dup is True
    a2, r, cv = store.push_context("merchant", "m1", 1, {"x": 2})
    assert a2 is False and r == "stale_version" and cv == 1
    a3, _, _ = store.push_context("merchant", "m1", 2, {"x": 3})
    assert a3 is True


def test_compose_deterministic_no_llm():
    cat = json.loads((ROOT / "dataset" / "categories" / "dentists.json").read_text(encoding="utf-8"))
    m = json.loads((ROOT / "dataset" / "merchants_seed.json").read_text(encoding="utf-8"))["merchants"][0]
    t = json.loads((ROOT / "dataset" / "triggers_seed.json").read_text(encoding="utf-8"))["triggers"][0]
    a = compose_message(cat, m, t, None, use_llm=False)
    b = compose_message(cat, m, t, None, use_llm=False)
    assert a["body"] == b["body"]
    assert "http" not in a["body"].lower()


def test_no_url_guard():
    assert body_has_url("see https://x.com")
    assert not body_has_url("Reply YES")


def test_auto_reply():
    out = handle_reply("c1", "m1", None, "merchant", "Thank you for contacting us! Our team will respond shortly.", 2)
    assert out["action"] == "send"
    assert "auto" in out.get("rationale", "").lower() or "canned" in out["body"].lower() or "YES" in out["body"]


def test_hostile_end():
    out = handle_reply("c2", "m1", None, "merchant", "Stop messaging me. This is spam.", 2)
    assert out["action"] == "end"


def test_intent_commitment():
    conv = "c3"
    st = Store.get()
    st.init_conversation(conv, "m1", None)
    st.append_turn(conv, "vera", "Want a draft?", body_sent="Want a draft?")
    out = handle_reply(conv, "m1", None, "merchant", "Ok lets do it. Whats next?", 2)
    assert out["action"] == "send"
    assert "draft" in out["body"].lower() or "confirm" in out["body"].lower()


def test_human_short_date_iso():
    assert human_short_date("2026-05-02T19:00:00+05:30") == "02 May"
    assert human_short_date("2026-05-02T19:00:00Z") == "02 May"
    assert human_short_date(None) is None


def test_hydration_present_and_labels():
    assert present(None, "x") == "x"
    assert present("", "x") == "x"
    assert present("None", "x") == "x"
    assert present(0, "x") == 0
    assert present_text(None) is None
    assert present_text("  ") is None
    assert present_text("none") is None
    assert present_text("...") is None
    assert present_text("  Hello  ") == "Hello"


def test_compose_cross_domain_supply_on_restaurant():
    cat = json.loads((ROOT / "dataset" / "categories" / "restaurants.json").read_text(encoding="utf-8"))
    merchants = json.loads((ROOT / "dataset" / "merchants_seed.json").read_text(encoding="utf-8"))["merchants"]
    m = next(x for x in merchants if x.get("category_slug") == "restaurants")
    t = next(
        x
        for x in json.loads((ROOT / "dataset" / "triggers_seed.json").read_text(encoding="utf-8"))["triggers"]
        if x.get("kind") == "supply_alert"
    )
    out = compose_message(cat, m, t, None, use_llm=False)
    assert out["template_name"] == "vera_safe_checklist_v1"
    low = out["body"].lower()
    assert "chronic" not in low
    assert "atorvastatin" not in low


def test_firewall_category_seasonal_pharma_trends_not_fitness():
    tr = {"payload": {"trends": ["ORS_demand_+40", "sunscreen_demand_+38"]}}
    assert not trigger_category_compatible("category_seasonal", "gyms", tr)
    assert trigger_category_compatible("category_seasonal", "pharmacies", tr)


def test_firewall_perf_spike_yoga_driver_not_dentist():
    tr = {"payload": {"likely_driver": "kids_yoga_post"}}
    assert not trigger_category_compatible("perf_spike", "dentists", tr)
    assert trigger_category_compatible("perf_spike", "gyms", tr)


def test_compose_compatible_research_digest_dentist():
    cat = json.loads((ROOT / "dataset" / "categories" / "dentists.json").read_text(encoding="utf-8"))
    m = json.loads((ROOT / "dataset" / "merchants_seed.json").read_text(encoding="utf-8"))["merchants"][0]
    t = json.loads((ROOT / "dataset" / "triggers_seed.json").read_text(encoding="utf-8"))["triggers"][0]
    assert t.get("kind") == "research_digest"
    out = compose_message(cat, m, t, None, use_llm=False)
    assert out["template_name"] == "vera_research_digest_v1"
    assert humanize_metric("review_count") == "reviews"
    assert "postcard" in humanize_verification_path("postcard_or_phone_call").lower()


def test_merchant_naturalness_banned_phrases():
    assert body_banned_internal("here’s the spine for bulk lunch priced off ₹149")
    assert body_banned_internal("paste WhatsApp + GBP in one block")
    assert body_banned_internal("runs dry ~the due date in your dispense log")
    assert not body_banned_internal("Want the exact WhatsApp pitch + Google listing copy?")


def test_facts_pack_sorted():
    cat = json.loads((ROOT / "dataset" / "categories" / "dentists.json").read_text(encoding="utf-8"))
    m = json.loads((ROOT / "dataset" / "merchants_seed.json").read_text(encoding="utf-8"))["merchants"][0]
    t = json.loads((ROOT / "dataset" / "triggers_seed.json").read_text(encoding="utf-8"))["triggers"][0]
    f = build_facts_pack(cat, m, t, None)
    # keys sorted at top level
    keys = list(f.keys())
    assert keys == sorted(keys)

