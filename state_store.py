import asyncio
from typing import Any, Optional

from schemas import Scope


contexts: dict[tuple[str, str], dict] = {}
conversations: dict[str, list[dict]] = {}
merchant_auto_replies: dict[str, dict] = {}


class AsyncContextStore:
    def __init__(self, backing_store: dict[tuple[str, str], dict]) -> None:
        self._store = backing_store
        self._lock = asyncio.Lock()

    async def put_if_newer(
        self, scope: Scope, context_id: str, version: int, payload: dict[str, Any]
    ) -> tuple[bool, Optional[int]]:
        key = (scope.value, context_id)
        async with self._lock:
            current = self._store.get(key)
            if current is not None and version <= int(current["version"]):
                return False, int(current["version"])
            self._store[key] = {"version": version, "payload": payload}
            return True, None

    async def counts(self) -> dict[str, int]:
        counts = {scope.value: 0 for scope in Scope}
        async with self._lock:
            for scope, _ in self._store:
                if scope in counts:
                    counts[scope] += 1
        return counts

    async def get_payload(self, scope: Scope | str, context_id: str) -> dict | None:
        scope_value = scope.value if isinstance(scope, Scope) else scope
        async with self._lock:
            current = self._store.get((scope_value, context_id))
            if current is None:
                return None
            return dict(current["payload"])

    async def latest_payload_for_scope(self, scope: Scope | str) -> dict | None:
        scope_value = scope.value if isinstance(scope, Scope) else scope
        async with self._lock:
            latest: dict | None = None
            latest_version = -1
            for (stored_scope, _), value in self._store.items():
                if stored_scope != scope_value:
                    continue
                version = int(value.get("version", -1))
                if version > latest_version:
                    latest = dict(value["payload"])
                    latest_version = version
            return latest


class ConversationStore:
    def __init__(self, backing_store: dict[str, list[dict]]) -> None:
        self._store = backing_store
        self._lock = asyncio.Lock()

    async def append_turn(
        self, conversation_id: str, from_role: str, message: str, turn_number: int
    ) -> list[dict]:
        turn = {"from": from_role, "msg": message, "turn": turn_number}
        async with self._lock:
            history = self._store.setdefault(conversation_id, [])
            history.append(turn)
            return list(history)

    async def append_flag(self, conversation_id: str, flag: dict[str, Any]) -> None:
        async with self._lock:
            self._store.setdefault(conversation_id, []).append(flag)

    async def history(self, conversation_id: str) -> list[dict]:
        async with self._lock:
            return list(self._store.get(conversation_id, []))


store = AsyncContextStore(contexts)
conversation_store = ConversationStore(conversations)
