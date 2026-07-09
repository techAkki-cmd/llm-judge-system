#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request


BOT_URL = "http://localhost:8080"
NOW = "2026-04-26T10:00:00Z"
PUSH_VERSION = 100
PROJECT_ROOT = Path(__file__).resolve().parent
CHALLENGE_ROOT = Path("/Users/arijitajaykumar/Downloads/magicpin-ai-challenge")
EXPANDED_DIR = CHALLENGE_ROOT / "expanded"
DATASET_DIR = CHALLENGE_ROOT / "dataset"
FALLBACK_MARKERS = ("LLM timeout fallback", "Stub for", "[LLM COMPOSE STUB]")


def http_json(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 15) -> tuple[int, dict[str, Any]]:
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
    if generator.exists():
        print(f"[INFO] expanded dataset missing; generating {EXPANDED_DIR}")
        subprocess.run(
            [sys.executable, str(generator), "--seed-dir", str(DATASET_DIR), "--out", str(EXPANDED_DIR)],
            cwd=str(DATASET_DIR),
            check=True,
        )
        if test_pairs.exists():
            return EXPANDED_DIR

    print("[WARN] expanded dataset unavailable; falling back to seed dataset")
    return DATASET_DIR


def load_contexts(dataset_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if (dataset_root / "test_pairs.json").exists():
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

    categories = {
        item["slug"]: item
        for item in (load_json(path) for path in sorted((dataset_root / "categories").glob("*.json")))
    }
    merchants = {item["merchant_id"]: item for item in load_json(dataset_root / "merchants_seed.json")["merchants"]}
    customers = {item["customer_id"]: item for item in load_json(dataset_root / "customers_seed.json")["customers"]}
    triggers = {item["id"]: item for item in load_json(dataset_root / "triggers_seed.json")["triggers"]}
    pairs = [
        {
            "test_id": f"T{index:02d}",
            "trigger_id": trigger["id"],
            "merchant_id": trigger["merchant_id"],
            "customer_id": trigger.get("customer_id"),
        }
        for index, trigger in enumerate(list(triggers.values())[:30], start=1)
    ]
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


def push_context(scope: str, context_id: str, payload: dict[str, Any]) -> bool:
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
        return True
    if status == 409 and data.get("reason") == "stale_version":
        return True
    print(f"[FAIL] context push {scope}/{context_id}: HTTP {status} {data}")
    return False


def bootstrap_contexts(
    categories: dict[str, dict[str, Any]],
    merchants: dict[str, dict[str, Any]],
    customers: dict[str, dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
) -> None:
    checks = []
    for slug, category in categories.items():
        checks.append(push_context("category", slug, category))
    for merchant_id, merchant in merchants.items():
        checks.append(push_context("merchant", merchant_id, merchant))
    for customer_id, customer in customers.items():
        checks.append(push_context("customer", customer_id, customer))
    for trigger_id, trigger in triggers.items():
        checks.append(push_context("trigger", trigger_id, trigger))
    if not all(checks):
        raise SystemExit("Context bootstrap failed; aborting evaluation.")


def evaluate_pair(index: int, total: int, pair: dict[str, Any], triggers: dict[str, dict[str, Any]]) -> bool:
    trigger_id = pair["trigger_id"]
    trigger = triggers.get(trigger_id, {})
    kind = trigger.get("kind", "unknown")
    print(f"[Pair {index}/{total}] Trigger: {kind}")

    try:
        status, data = http_json(
            "POST",
            "/v1/tick",
            {"now": NOW, "available_triggers": [trigger_id]},
            timeout=20,
        )
    except Exception as exc:
        print(f"[FAIL] tick request failed: {exc}")
        print("---")
        return False

    if status >= 500:
        print(f"[FAIL] server error: HTTP {status} {data}")
        print("---")
        return False

    actions = data.get("actions") if isinstance(data, dict) else None
    if not actions:
        print(f"[FAIL] empty action list: HTTP {status} {data}")
        print("---")
        return False

    action = actions[0]
    body = str(action.get("body") or "")
    send_as = action.get("send_as")
    print(f"Send As: {send_as}")
    print(f"Message: {body}")

    failed = False
    if not body.strip():
        print("[FAIL] empty message body")
        failed = True
    for marker in FALLBACK_MARKERS:
        if marker in body:
            print(f"[FAIL] fallback marker detected: {marker}")
            failed = True
    print("---")
    return not failed


def main() -> int:
    dataset_root = ensure_expanded_dataset()
    categories, merchants, customers, triggers, pairs = load_contexts(dataset_root)

    print(f"[INFO] Dataset: {dataset_root}")
    print(f"[INFO] Loaded {len(categories)} categories, {len(merchants)} merchants, {len(customers)} customers, {len(triggers)} triggers")
    print(f"[INFO] Test pairs: {len(pairs)}")

    require_server()
    bootstrap_contexts(categories, merchants, customers, triggers)

    passed = 0
    failed = 0
    for index, pair in enumerate(pairs, start=1):
        if evaluate_pair(index, len(pairs), pair, triggers):
            passed += 1
        else:
            failed += 1

    print(f"PASS: {passed}/{len(pairs)}")
    print(f"FAIL: {failed}/{len(pairs)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
