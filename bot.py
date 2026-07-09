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
        return asyncio.run(
            composer.generate_action(
                category or {},
                merchant or {},
                trigger or {},
                customer,
                conversation_history=[],
            )
        )
    raise RuntimeError("compose() cannot run inside an active event loop; call LLMComposer directly.")
