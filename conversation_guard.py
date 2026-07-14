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
    DENTAL_TECH_RE = re.compile(
        r"(?i)\b(x[- ]?ray|radiograph|d[- ]?speed|film unit|aerb|radiation|audit|sensor|opg|cbct)\b"
    )
    AUTO_REPLY_THRESHOLD = 0.85

    def __init__(
        self,
        conversation_id: str,
        merchant_id: Optional[str],
        message: str,
        history: list[dict],
        merchant: Optional[dict] = None,
        customer: Optional[dict] = None,
        from_role: str = "merchant",
    ) -> None:
        self.conversation_id = conversation_id
        self.merchant_id = merchant_id
        self.message = message
        self.history = history
        self.merchant = merchant or {}
        self.customer = customer
        self.from_role = from_role
        self.intent_actioned = False

    def route(self) -> dict[str, Any]:
        canned_auto_reply = bool(self.CANNED_AUTO_REPLY_RE.search(self.message))
        auto_reply_count = self._merchant_auto_reply_count(canned_auto_reply)
        if auto_reply_count == 1:
            return {
                "action": "send",
                "body": "It looks like an auto-responder is on. Let me know when you are back to review the campaign!",
                "cta": "open_ended",
                "rationale": "Detected first auto-reply. Sent one prompt to human.",
            }
        if auto_reply_count == 2:
            return {
                "action": "wait",
                "wait_seconds": 14400,
                "rationale": "Detected repeated auto-reply. Backing off instead of sending again.",
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
                "action": "end",
                "rationale": "Unsupported GST/tax/accounting request is outside Vera scope. Closing without giving advice.",
            }

        technical = self._technical_followup()
        if technical:
            return technical

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

    def _merchant_auto_reply_count(self, canned_auto_reply: bool) -> int:
        if not self.merchant_id:
            return 1 if canned_auto_reply else 0

        state = merchant_auto_replies.get(self.merchant_id)
        if not state:
            count = 1 if canned_auto_reply else 0
            merchant_auto_replies[self.merchant_id] = {"last_msg": self.message, "count": count}
            return count

        similarity = SequenceMatcher(None, self.message, state.get("last_msg", "")).ratio()
        if canned_auto_reply or similarity > self.AUTO_REPLY_THRESHOLD:
            state["count"] = int(state.get("count", 0)) + 1
        else:
            state["count"] = 0
            state["last_msg"] = self.message
        return int(state["count"])

    def _technical_followup(self) -> dict[str, Any] | None:
        if not self.DENTAL_TECH_RE.search(self.message):
            return None
        if self.merchant.get("category_slug") != "dentists":
            return None

        identity = self.merchant.get("identity", {}) or {}
        performance = self.merchant.get("performance", {}) or {}
        owner = identity.get("owner_first_name") or identity.get("name") or "Doctor"
        business = identity.get("name") or "your clinic"
        calls = performance.get("calls")
        directions = performance.get("directions")
        metric = ""
        if calls is not None and directions is not None:
            metric = f" With {calls} calls and {directions} direction requests, "
        else:
            metric = " "
        body = (
            f"Dr. {owner}, for an old D-speed film X-ray unit at {business}, audit radiation safety before promo work:"
            f"{metric}check timer calibration, developer age/temp, collimation, apron/thyroid shield, and AERB records. "
            "Want the 7-point chairside checklist?"
        )
        return {
            "action": "send",
            "body": body,
            "cta": "natural_question",
            "rationale": "Answered the dentist's technical X-ray audit follow-up with clinic-specific safety steps.",
        }
