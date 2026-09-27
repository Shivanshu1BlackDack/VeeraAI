"""In-memory stores for contexts, conversations, suppression, and compose cache."""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ContextRecord:
    version: int
    payload: dict


@dataclass
class Turn:
    from_role: str
    message: str
    body_sent: Optional[str] = None


@dataclass
class ConversationState:
    merchant_id: str
    customer_id: Optional[str]
    trigger_id: Optional[str] = None
    trigger_kind: Optional[str] = None
    turns: List[Turn] = field(default_factory=list)
    last_bot_body: Optional[str] = None
    auto_reply_streak: int = 0
    last_merchant_norm: Optional[str] = None
    ended: bool = False
    context_hint: Optional[str] = None
    best_offer_title: Optional[str] = None
    top_search_hint: Optional[str] = None
    locality: Optional[str] = None


class Store:
    _instance: Optional["Store"] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._contexts: Dict[Tuple[str, str], ContextRecord] = {}
        self._conversations: Dict[str, ConversationState] = {}
        self._suppression: set[str] = set()
        self._compose_cache: Dict[str, dict] = {}
        self._started: float = time.time()

    @classmethod
    def get(cls) -> "Store":
        with cls._lock:
            if cls._instance is None:
                cls._instance = Store()
            return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._instance = Store()

    def push_context(
        self, scope: str, context_id: str, version: int, payload: dict
    ) -> Tuple[bool, Optional[str], Optional[int]]:
        key = (scope, context_id)
        cur = self._contexts.get(key)
        if cur is not None:
            if version < cur.version:
                return False, "stale_version", cur.version
            if version == cur.version:
                if cur.payload == payload:
                    return True, None, cur.version
                return False, "stale_version", cur.version
        self._contexts[key] = ContextRecord(version=version, payload=copy.deepcopy(payload))
        return True, None, None

    def get_context(self, scope: str, context_id: str) -> Optional[ContextRecord]:
        return self._contexts.get((scope, context_id))

    def count_by_scope(self) -> Dict[str, int]:
        counts: Dict[str, int] = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        for (s, _) in self._contexts:
            if s in counts:
                counts[s] += 1
        return counts

    def uptime_seconds(self) -> int:
        return int(time.time() - self._started)

    def is_suppressed(self, key: str) -> bool:
        return key in self._suppression

    def suppress(self, key: str) -> None:
        self._suppression.add(key)

    def get_compose_cache(self, key: str) -> Optional[dict]:
        return self._compose_cache.get(key)

    def set_compose_cache(self, key: str, value: dict) -> None:
        self._compose_cache[key] = value

    def get_conversation(self, conv_id: str) -> Optional[ConversationState]:
        return self._conversations.get(conv_id)

    def init_conversation(
        self,
        conv_id: str,
        merchant_id: str,
        customer_id: Optional[str],
        trigger_id: Optional[str] = None,
        context_hint: Optional[str] = None,
        trigger_kind: Optional[str] = None,
        best_offer_title: Optional[str] = None,
        top_search_hint: Optional[str] = None,
        locality: Optional[str] = None,
    ) -> ConversationState:
        st = self._conversations.get(conv_id)
        if st is None:
            st = ConversationState(
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
                context_hint=context_hint,
                trigger_kind=trigger_kind,
                best_offer_title=best_offer_title,
                top_search_hint=top_search_hint,
                locality=locality,
            )
            self._conversations[conv_id] = st
        return st

    def append_turn(
        self,
        conv_id: str,
        from_role: str,
        message: str,
        body_sent: Optional[str] = None,
    ) -> None:
        st = self._conversations.get(conv_id)
        if st is None:
            return
        st.turns.append(Turn(from_role=from_role, message=message, body_sent=body_sent))
        if from_role in ("vera", "bot", "system") and body_sent:
            st.last_bot_body = body_sent
        if from_role == "merchant" and message:
            norm = " ".join(message.lower().split())
            if st.last_merchant_norm == norm:
                st.auto_reply_streak += 1
            else:
                st.auto_reply_streak = 1
            st.last_merchant_norm = norm
