import re


class ResponseValidator:
    PREAMBLE_RE = re.compile(
        r"^\s*(hi there|hope you are well|I hope you are doing well|I hope this message finds you well)[,\.]?\s*",
        re.IGNORECASE,
    )

    def enforce_rubric(self, llm_response: dict, category: dict) -> dict:
        response = dict(llm_response or {})
        body = response.get("body")
        if not isinstance(body, str):
            body = "" if body is None else str(body)

        response["body"] = self.PREAMBLE_RE.sub("", body).strip()
        return response
