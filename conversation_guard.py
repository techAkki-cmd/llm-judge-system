import re
from difflib import SequenceMatcher
from typing import Any, Optional

from state_store import merchant_auto_replies


class ConversationGuard:
    HOSTILE_OPT_OUT_RE = re.compile(
        r"(?i)\b(stop|spam|useless|don'?t message|unsubscribe|not interested)\b"
    )
    COMMITMENT_RE = re.compile(r"(?i)\b(yes|let'?s do it|go ahead|send it|sure|confirm)\b")
    AUTO_REPLY_THRESHOLD = 0.85

    def __init__(
        self,
        conversation_id: str,
        merchant_id: Optional[str],
        message: str,
        history: list[dict],
    ) -> None:
        self.conversation_id = conversation_id
        self.merchant_id = merchant_id
        self.message = message
        self.history = history
        self.intent_actioned = False

    def route(self) -> dict[str, Any]:
        auto_reply_count = self._merchant_auto_reply_count()
        if auto_reply_count == 1:
            return {
                "action": "wait",
                "wait_seconds": 14400,
                "rationale": "Detected merchant auto-reply. Backing off 4 hours to wait for owner.",
            }
        if auto_reply_count >= 2:
            return {
                "action": "end",
                "rationale": "Auto-reply 3x in a row, no real reply. Conversation has zero engagement signal; closing.",
            }

        if self.HOSTILE_OPT_OUT_RE.search(self.message):
            return {
                "action": "end",
                "rationale": "Merchant explicitly opted out or showed hostility. Closing conversation.",
            }

        if self.COMMITMENT_RE.search(self.message):
            self.intent_actioned = True
            return {
                "action": "send",
                "body": "I am drafting the message now. Please confirm to proceed with sending.",
                "cta": "open_ended",
                "rationale": "Merchant committed; switching to action.",
            }

        return {
            "action": "send",
            "body": "[LLM COMPOSE STUB]",
            "cta": "open_ended",
            "rationale": "Valid reply, passing to composer.",
        }

    def _merchant_auto_reply_count(self) -> int:
        if not self.merchant_id:
            return 0

        state = merchant_auto_replies.get(self.merchant_id)
        if not state:
            merchant_auto_replies[self.merchant_id] = {"last_msg": self.message, "count": 0}
            return 0

        similarity = SequenceMatcher(None, self.message, state.get("last_msg", "")).ratio()
        if similarity > self.AUTO_REPLY_THRESHOLD:
            state["count"] = int(state.get("count", 0)) + 1
        else:
            state["count"] = 0
            state["last_msg"] = self.message
        return int(state["count"])
