import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.logger import logger
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from conversation_guard import ConversationGuard
from llm_composer import LLMComposer
from schemas import ContextPush, PAYLOAD_MODELS, ReplyRequest, Scope, TickRequest
from state_store import conversation_store, store


START_TIME = time.monotonic()
STATIC_DIR = Path(__file__).resolve().parent / "static"


app = FastAPI(title="magicpin AI Challenge Bot", version=os.getenv("APP_VERSION", "1.0.0"))
composer = LLMComposer()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_team_members(value: str) -> list[str]:
    members = [member.strip() for member in value.split(",") if member.strip()]
    return members or ["Arijit Ajay Kumar"]


@app.get("/")
async def serve_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.head("/")
async def serve_ui_head() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("Validation failed on %s: %s", request.url.path, exc.errors())
    body: dict[str, Any]
    if request.url.path == "/v1/context":
        body = {"accepted": False, "reason": "validation_error", "details": exc.errors()}
    else:
        body = {"error": "validation_error", "details": exc.errors()}
    return JSONResponse(status_code=400, content=body)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "details": "unexpected server error"},
    )


@app.get("/v1/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": int(time.monotonic() - START_TIME),
        "contexts_loaded": await store.counts(),
    }


@app.get("/v1/metadata")
async def metadata() -> dict[str, Any]:
    runtime_model = os.getenv("MODEL_NAME") or f"{composer.model} via {composer.provider}"
    return {
        "team_name": os.getenv("TEAM_NAME", "Team AI-agent"),
        "team_members": parse_team_members(os.getenv("TEAM_MEMBERS", "Arijit Ajay Kumar")),
        "model": runtime_model,
        "approach": "Deterministic context router with isolated state store",
        "contact_email": os.getenv("CONTACT_EMAIL", "team@example.com"),
        "version": os.getenv("APP_VERSION", "1.0.0"),
        "submitted_at": os.getenv("SUBMITTED_AT", "2026-04-26T08:00:00Z"),
    }


@app.post("/v1/context")
async def push_context(body: ContextPush) -> JSONResponse:
    try:
        scope = Scope(body.scope)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={
                "accepted": False,
                "reason": "invalid_scope",
                "details": f"scope must be one of: {', '.join(scope.value for scope in Scope)}",
            },
        )

    payload_model = PAYLOAD_MODELS[scope]
    try:
        validated_payload = payload_model.model_validate(body.payload).model_dump(
            mode="json", by_alias=True
        )
    except ValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "accepted": False,
                "reason": "invalid_payload",
                "details": exc.errors(),
            },
        )
    accepted, current_version = await store.put_if_newer(
        scope, body.context_id, body.version, validated_payload
    )

    if not accepted:
        return JSONResponse(
            status_code=409,
            content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": current_version,
            },
        )

    logger.info("Stored %s %s v%s", scope.value, body.context_id, body.version)
    return JSONResponse(
        status_code=200,
        content={
            "accepted": True,
            "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": utc_now_iso(),
        },
    )


@app.post("/v1/tick")
async def tick(body: TickRequest) -> dict[str, list[Any]]:
    actions: list[dict[str, Any]] = []
    for trigger_id in body.available_triggers[:20]:
        trigger = await store.get_payload(Scope.trigger, trigger_id)
        if not trigger:
            continue

        merchant_id = trigger.get("merchant_id")
        if not merchant_id:
            continue

        merchant = await store.get_payload(Scope.merchant, merchant_id)
        if not merchant:
            continue

        category_slug = merchant.get("category_slug")
        if not category_slug:
            continue

        category = await store.get_payload(Scope.category, category_slug)
        if not category:
            continue

        customer_id = trigger.get("customer_id")
        customer = None
        if customer_id:
            customer = await store.get_payload(Scope.customer, customer_id)
            if not customer:
                continue

        try:
            action = await composer.generate_action(
                category, merchant, trigger, customer, conversation_history=[]
            )
        except Exception as exc:
            logger.warning("LLM composer failed for trigger %s: %s", trigger_id, exc)
            continue

        action.update(
            {
                "conversation_id": f"conv_{merchant_id}_{trigger_id}_{int(time.time() * 1000)}",
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": "merchant_on_behalf" if customer_id else "vera",
                "trigger_id": trigger_id,
                "suppression_key": trigger.get("suppression_key", ""),
            }
        )
        if not await store.reserve_suppression_key(str(action.get("suppression_key") or "")):
            continue
        actions.append(action)
    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyRequest) -> dict[str, Any]:
    history = await conversation_store.append_turn(
        body.conversation_id, body.from_role, body.message, body.turn_number
    )
    guard = ConversationGuard(body.conversation_id, body.merchant_id, body.message, history)
    response = guard.route()
    if guard.intent_actioned:
        await conversation_store.append_flag(body.conversation_id, {"intent_actioned": True})
    if response.get("body") != "[LLM COMPOSE STUB]":
        return response

    if not body.merchant_id:
        return {"action": "wait", "wait_seconds": 300, "rationale": "LLM timeout fallback."}

    history = await conversation_store.history(body.conversation_id)
    merchant = await store.get_payload(Scope.merchant, body.merchant_id)
    if not merchant:
        return {"action": "wait", "wait_seconds": 300, "rationale": "LLM timeout fallback."}

    category_slug = merchant.get("category_slug")
    category = await store.get_payload(Scope.category, category_slug) if category_slug else None
    if not category:
        return {"action": "wait", "wait_seconds": 300, "rationale": "LLM timeout fallback."}

    try:
        return await composer.generate_action(category, merchant, {}, None, history)
    except Exception as exc:
        logger.warning("LLM composer failed for reply %s: %s", body.conversation_id, exc)
        return {"action": "wait", "wait_seconds": 300, "rationale": "LLM timeout fallback."}


@app.post("/v1/teardown")
async def teardown_state() -> dict[str, str]:
    await store.teardown()
    return {"status": "success", "message": "State cleared"}
