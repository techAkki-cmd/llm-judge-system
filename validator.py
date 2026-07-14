import re


class ResponseValidator:
    MAX_BODY_CHARS = 320
    PREAMBLE_RE = re.compile(
        r"^\s*(hi there|hope you are well|I hope you are doing well|I hope this message finds you well)[,\.]?\s*",
        re.IGNORECASE,
    )
    CTA_RE = re.compile(
        r"(Reply (?:YES|1|CONFIRM|with|1 for)[^.?!]*(?:STOP|skip|cancel|better time)[^.?!]*[.?!]?)",
        re.IGNORECASE,
    )

    def enforce_rubric(self, llm_response: dict, category: dict) -> dict:
        response = dict(llm_response or {})
        body = response.get("body")
        if not isinstance(body, str):
            body = "" if body is None else str(body)

        response["body"] = self.clamp_body(self.PREAMBLE_RE.sub("", body).strip())
        return response

    def normalize_action(self, action: dict) -> dict:
        response = dict(action or {})
        if response.get("action") == "send" or response.get("body") is not None:
            response["action"] = "send"
            response["body"] = self.clamp_body(str(response.get("body") or "").strip())
            response["cta"] = str(response.get("cta") or "open_ended")
            response["rationale"] = str(response.get("rationale") or "Normalized outbound action.")
        return response

    def clamp_body(self, body: str, limit: int | None = None) -> str:
        max_chars = limit or self.MAX_BODY_CHARS
        text = " ".join(str(body or "").split())
        if len(text) <= max_chars:
            return text

        cta_match = self.CTA_RE.search(text)
        cta = cta_match.group(1).strip() if cta_match else "Reply YES to proceed, or STOP."
        if len(cta) > max_chars:
            return cta[: max_chars - 1].rstrip() + "."

        prefix_limit = max_chars - len(cta) - 1
        prefix = text[:prefix_limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
        if not prefix:
            return cta
        return f"{prefix}. {cta}"[:max_chars]
