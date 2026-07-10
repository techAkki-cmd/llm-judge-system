import re
from difflib import SequenceMatcher
from typing import Any, Optional

from state_store import merchant_auto_replies


class ConversationGuard:
    CANNED_AUTO_REPLY_RE = re.compile(
        r"(?i)(thank you for contacting|will respond|business hours|automated reply|out of office|get back to you shortly|we have received your message)"
    )
    HOSTILE_OPT_OUT_RE = re.compile(
        r"(?i)\b(stop|spam|useless|don'?t message|unsubscribe|not interested)\b"
    )
    OFF_TOPIC_RE = re.compile(r"(?i)\b(gst|tax|loan|filing|ca|accountant)\b")
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
        if self.CANNED_AUTO_REPLY_RE.search(self.message):
            return {
                "action": "send",
                "body": "It looks like an auto-responder is on. Let me know when you are back to review the campaign!",
                "cta": "open_ended",
                "rationale": "Proactively detected standard auto-responder phrasing on first turn. Sent prompt to human.",
            }

        auto_reply_count = self._merchant_auto_reply_count()
        if auto_reply_count == 1:
            return {
                "action": "send",
                "body": "It looks like an auto-responder is on. Let me know when you are back to review the campaign!",
                "cta": "open_ended",
                "rationale": "Detected first auto-reply. Sent prompt to human.",
            }
        if auto_reply_count == 2:
            return {
                "action": "wait",
                "wait_seconds": 14400,
                "rationale": "Detected second auto-reply. Backing off.",
            }
        if auto_reply_count >= 3:
            return {
                "action": "end",
                "rationale": "Detected persistent auto-reply loop. Terminating.",
            }

        if self.HOSTILE_OPT_OUT_RE.search(self.message):
            return {
                "action": "end",
                "rationale": "Merchant explicitly opted out or showed hostility. Closing conversation.",
            }

        if self.OFF_TOPIC_RE.search(self.message):
            return {
                "action": "send",
                "body": "I'll leave the GST and tax filing to your CA! 😅 Coming back to the campaign — want me to draft that customer message for you?",
                "cta": "open_ended",
                "rationale": "Out-of-scope ask politely declined; redirected to original Vera task.",
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
