#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request


BOT_URL = "http://localhost:8080"
NOW = "2026-04-26T10:00:00Z"
PROJECT_ROOT = Path(__file__).resolve().parent
CHALLENGE_ROOT = Path("/Users/arijitajaykumar/Downloads/magicpin-ai-challenge")
EXPANDED_DIR = CHALLENGE_ROOT / "expanded"
DATASET_DIR = CHALLENGE_ROOT / "dataset"
SUBMISSION_PATH = PROJECT_ROOT / "submission.jsonl"
PUSH_VERSION = int(time.time())
FALLBACK_MARKERS = ("LLM timeout fallback", "Stub for", "[LLM COMPOSE STUB]")


def http_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        f"{BOT_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"error": raw}
        return exc.code, parsed


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_expanded_dataset() -> Path:
    test_pairs = EXPANDED_DIR / "test_pairs.json"
    if test_pairs.exists():
        return EXPANDED_DIR

    generator = DATASET_DIR / "generate_dataset.py"
    if not generator.exists():
        raise SystemExit(f"Expanded dataset missing and generator not found: {generator}")

    subprocess.run(
        [sys.executable, str(generator), "--seed-dir", str(DATASET_DIR), "--out", str(EXPANDED_DIR)],
        cwd=str(DATASET_DIR),
        check=True,
    )
    if not test_pairs.exists():
        raise SystemExit(f"Expanded dataset generation did not create {test_pairs}")
    return EXPANDED_DIR


def load_contexts(
    dataset_root: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    categories = {
        item["slug"]: item
        for item in (load_json(path) for path in sorted((dataset_root / "categories").glob("*.json")))
    }
    merchants = {
        item["merchant_id"]: item
        for item in (load_json(path) for path in sorted((dataset_root / "merchants").glob("*.json")))
    }
    customers = {
        item["customer_id"]: item
        for item in (load_json(path) for path in sorted((dataset_root / "customers").glob("*.json")))
    }
    triggers = {
        item["id"]: item
        for item in (load_json(path) for path in sorted((dataset_root / "triggers").glob("*.json")))
    }
    pairs = load_json(dataset_root / "test_pairs.json")["pairs"]
    return categories, merchants, customers, triggers, pairs


def require_server() -> None:
    try:
        status, data = http_json("GET", "/v1/healthz", timeout=5)
    except Exception as exc:
        raise SystemExit(
            "FastAPI server is not reachable at http://localhost:8080. "
            "Start it with: .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080"
        ) from exc
    if status != 200:
        raise SystemExit(f"FastAPI server health check failed: HTTP {status} {data}")


def push_context(scope: str, context_id: str, payload: dict[str, Any]) -> None:
    status, data = http_json(
        "POST",
        "/v1/context",
        {
            "scope": scope,
            "context_id": context_id,
            "version": PUSH_VERSION,
            "payload": payload,
            "delivered_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    )
    if status == 200 and data.get("accepted") is True:
        return
    if status == 409 and data.get("reason") == "stale_version":
        return
    raise SystemExit(f"Context push failed for {scope}/{context_id}: HTTP {status} {data}")


def bootstrap_contexts(
    categories: dict[str, dict[str, Any]],
    merchants: dict[str, dict[str, Any]],
    customers: dict[str, dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
) -> None:
    for slug, category in categories.items():
        push_context("category", slug, category)
    for merchant_id, merchant in merchants.items():
        push_context("merchant", merchant_id, merchant)
    for customer_id, customer in customers.items():
        push_context("customer", customer_id, customer)
    for trigger_id, trigger in triggers.items():
        push_context("trigger", trigger_id, trigger)


def validate_action(test_id: str, action: dict[str, Any]) -> None:
    body = str(action.get("body") or "")
    if not body.strip():
        raise RuntimeError(f"{test_id}: empty action body")
    if len(body) > 320:
        raise RuntimeError(f"{test_id}: action body exceeds 320 characters: {len(body)}")
    for marker in FALLBACK_MARKERS:
        if marker in body or marker in str(action.get("rationale") or ""):
            raise RuntimeError(f"{test_id}: fallback marker detected: {marker}")


def generate_record(test_id: str, pair: dict[str, Any], triggers: dict[str, dict[str, Any]]) -> dict[str, str]:
    trigger_id = pair["trigger_id"]
    status, data = http_json(
        "POST",
        "/v1/tick",
        {"now": NOW, "available_triggers": [trigger_id]},
        timeout=20,
    )
    if status >= 500:
        raise RuntimeError(f"{test_id}: server error HTTP {status} {data}")
    if status != 200:
        raise RuntimeError(f"{test_id}: unexpected HTTP {status} {data}")

    actions = data.get("actions") if isinstance(data, dict) else None
    if not actions:
        raise RuntimeError(f"{test_id}: empty actions list for trigger {trigger_id}")

    action = actions[0]
    validate_action(test_id, action)
    trigger = triggers.get(trigger_id, {})
    return {
        "test_id": test_id,
        "body": str(action.get("body") or ""),
        "cta": str(action.get("cta") or ""),
        "send_as": str(action.get("send_as") or ""),
        "suppression_key": str(action.get("suppression_key") or trigger.get("suppression_key") or ""),
        "rationale": str(action.get("rationale") or ""),
    }


def write_submission(records: list[dict[str, str]]) -> None:
    with SUBMISSION_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    dataset_root = ensure_expanded_dataset()
    categories, merchants, customers, triggers, pairs = load_contexts(dataset_root)
    if len(pairs) != 30:
        raise SystemExit(f"Expected 30 test pairs, found {len(pairs)}")

    require_server()
    bootstrap_contexts(categories, merchants, customers, triggers)

    records: list[dict[str, str]] = []
    for index, pair in enumerate(pairs, start=1):
        test_id = f"T{index:02d}"
        records.append(generate_record(test_id, pair, triggers))

    write_submission(records)
    print(f"Wrote {len(records)} records to {SUBMISSION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
