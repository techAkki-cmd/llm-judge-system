from __future__ import annotations

from typing import Any

from conversation_guard import ConversationGuard
from validator import ResponseValidator


validator = ResponseValidator()


def respond(state: Any, merchant_message: str) -> dict[str, Any]:
    """Challenge-brief compatible wrapper for multi-turn reply handling.

    The static brief describes a `respond(state, merchant_message)` entrypoint.
    This adapter accepts either a dict-like state or an object with matching
    attributes, then delegates to the same deterministic guard used by FastAPI.
    """
    merchant = _state_get(state, "merchant", {}) or {}
    customer = _state_get(state, "customer", None)
    history = _state_get(state, "history", []) or _state_get(state, "conversation_history", []) or []
    return handle_reply(merchant, customer, merchant_message, history)


def handle_reply(
    merchant: dict[str, Any],
    customer: dict[str, Any] | None,
    message: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Route an inbound turn through Vera's deterministic replay guard.

    Hostile opt-outs such as "stop", "spam", or "not interested" immediately end
    the conversation. Repeated merchant auto-replies are tracked by merchant_id:
    the second matching auto-reply waits, and the third closes the conversation.
    Explicit commitments such as "yes" or "go ahead" switch to action mode.
    """
    merchant_id = str(merchant.get("merchant_id") or merchant.get("id") or "")
    customer_id = customer.get("customer_id") if customer else None
    conversation_id = _conversation_id(merchant_id, customer_id, history)
    turn_number = _next_turn_number(history)
    guard_history = list(history or [])
    guard_history.append({"from": "merchant", "msg": message, "turn": turn_number})
    guard = ConversationGuard(
        conversation_id,
        merchant_id,
        message or "",
        guard_history,
        merchant=merchant,
        customer=customer,
        from_role="merchant",
    )
    return validator.normalize_action(guard.route())


def _conversation_id(
    merchant_id: str,
    customer_id: str | None,
    history: list[dict[str, Any]],
) -> str:
    for turn in reversed(history or []):
        value = turn.get("conversation_id")
        if value:
            return str(value)
    if customer_id:
        return f"static_{merchant_id}_{customer_id}"
    return f"static_{merchant_id or 'unknown'}"


def _next_turn_number(history: list[dict[str, Any]]) -> int:
    turns = [
        int(turn.get("turn", 0))
        for turn in history or []
        if isinstance(turn, dict) and str(turn.get("turn", "")).isdigit()
    ]
    return (max(turns) + 1) if turns else 1


def _state_get(state: Any, key: str, default: Any) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)
