from __future__ import annotations

import asyncio
from typing import Any

from llm_composer import LLMComposer


def compose(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Static challenge artifact entrypoint for one outbound composition."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        composer = LLMComposer()
        action = asyncio.run(
            composer.generate_action(
                category or {},
                merchant or {},
                trigger or {},
                customer,
                conversation_history=[],
            )
        )
        return _submission_shape(action, trigger or {}, customer)
    raise RuntimeError("compose() cannot run inside an active event loop; call LLMComposer directly.")


def _submission_shape(
    action: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "body": str(action.get("body") or ""),
        "cta": str(action.get("cta") or "open_ended"),
        "send_as": str(
            action.get("send_as")
            or ("merchant_on_behalf" if customer else "vera")
        ),
        "suppression_key": str(
            action.get("suppression_key")
            or trigger.get("suppression_key")
            or ""
        ),
        "rationale": str(action.get("rationale") or "Deterministic context-aware composition."),
    }
