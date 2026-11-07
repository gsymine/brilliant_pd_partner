"""ZooWork plumbing: provision the three agents, and drive one agent turn to completion.

Provisioning
    Each role (communication / extraction / analysis) is one long-lived ZooWork Agent. Its
    standing instructions are the role's skill.md (loaded as persona AGENTS.md) and its tools are
    the custom tools from tool_specs.py. Agents are created once, found again by labels, and
    updated in place when skill.md or the tool list changes.

Driving a turn
    Post a message (or create the session with it), stream events from the last saved cursor,
    execute every requested custom tool in this backend, resolve it, and stop on run.finished.
    One asyncio lock per session keeps turns on the same vendor thread strictly sequential.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, Callable

from zoowork import ZooworkError, custom_tool_use, is_run_finished, run_outcome

from .config import AGENTS_DIR, Settings
from .db import Database
from .tool_specs import TOOLS_BY_ROLE
from .tools import ToolContext, dispatch

log = logging.getLogger(__name__)

ROLES = ("communication", "extraction", "analysis")
FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.S)


def load_skill(role: str) -> str:
    """Return the body of agents/<role>/skill.md (YAML frontmatter stripped)."""
    text = (AGENTS_DIR / role / "skill.md").read_text(encoding="utf-8")
    return FRONTMATTER_RE.sub("", text, count=1).strip()


def agent_resource(role: str, settings: Settings, model: str) -> dict[str, Any]:
    return {
        "name": f"pd-partner-{role}",
        "model": {"primary": model},
        "persona": {"docs": [{"name": "AGENTS.md", "content": load_skill(role)}]},
        "custom_tools": [{**t, "timeoutMs": 600_000} for t in TOOLS_BY_ROLE[role]],
        "labels": {"app": settings.agent_label_app, "env": settings.agent_label_env, "role": role},
        "sandbox": {"scope": "agent"},
    }


def config_hash(resource: dict[str, Any]) -> str:
    stable = {k: resource[k] for k in ("model", "persona", "custom_tools")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


class ZooworkRuntime:
    def __init__(self, client: Any, db: Database, settings: Settings,
                 ctx_factory: Callable[[str, str, str | None], ToolContext]):
        self.client = client
        self.db = db
        self.s = settings
        self.ctx_factory = ctx_factory           # (role, run_id, thread_id) -> ToolContext
        self.agent_ids: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---- provisioning ----------------------------------------------------------------------
    async def _pick_model(self) -> str:
        if self.s.zoowork_model:
            return self.s.zoowork_model
        for row in await self.client.list_models():
            if row.get("selectable") is not False and "model" in (row.get("default_for") or []):
                return row["model"]
        raise RuntimeError("No selectable default ZooWork model; set ZOOWORK_MODEL")

    async def ensure_agents(self) -> dict[str, str]:
        """Create or update the three agents (serially: concurrent creates can 503) and start them."""
        model = await self._pick_model()
        for role in ROLES:
            resource = agent_resource(role, self.s, model)
            digest = config_hash(resource)
            saved = self.db.get_agent(role)
            agent_id = saved["agent_id"] if saved else None
            if agent_id is None:
                page = await self.client.list_agents(labels=resource["labels"])
                agent_id = page.data[0]["agent_id"] if page.data else None
                if agent_id:
                    saved = {"config_hash": None}
            if agent_id is None:
                created = await self.client.create_agent(
                    resource, idempotency_key=f"{self.s.agent_label_app}-{self.s.agent_label_env}-{role}-v1")
                agent_id = created["agent_id"]
                log.info("Created %s agent %s", role, agent_id)
            elif saved and saved.get("config_hash") != digest:
                await self.client.update_agent(agent_id, {
                    "model": resource["model"], "persona": resource["persona"], "custom_tools": resource["custom_tools"],
                })
                log.info("Updated %s agent %s (skill.md or tools changed)", role, agent_id)
            self.db.save_agent(role, agent_id, digest)
            await self.client.start_agent(agent_id)
            await self.client.wait_until_running(agent_id, timeout=90)
            self.agent_ids[role] = agent_id
        return dict(self.agent_ids)

    # ---- sessions --------------------------------------------------------------------------
    async def start_session(self, role: str, owner_id: str, run_id: str, message: str,
                            thread_id: str | None = None, timeout: float | None = None) -> str:
        """Create the role's session for owner_id with an opening message and drive the first turn."""
        agent_id = self.agent_ids[role]
        async with self._locks[f"{role}:{owner_id}"]:
            existing = self.db.get_session(role, owner_id)
            if existing:
                session_id = existing["session_id"]
            else:
                session = await self.client.create_session(
                    agent_id,
                    {"initial_events": [{"type": "user.message", "content": message}],
                     "metadata": {"run_id": run_id, "owner_id": owner_id, "role": role}},
                    idempotency_key=f"pd-{role}-{owner_id}",
                )
                session_id = session["session_id"]
                self.db.save_session(role, owner_id, run_id, agent_id, session_id)
            await self._drive(role, run_id, thread_id, agent_id, session_id, timeout)
            return session_id

    async def send(self, role: str, owner_id: str, message: str, idempotency_key: str,
                   timeout: float | None = None) -> None:
        """Post one more user.message into an existing session and drive that turn."""
        async with self._locks[f"{role}:{owner_id}"]:
            row = self.db.get_session(role, owner_id)
            if not row:
                raise RuntimeError(f"No {role} session for {owner_id}")
            receipts = await self.client.post_events(row["agent_id"], row["session_id"], [
                {"type": "user.message", "content": message, "idempotency_key": idempotency_key}])
            if len(receipts) != 1 or receipts[0].get("accepted") is not True:
                raise RuntimeError(f"Message not accepted by session {row['session_id']}: {receipts}")
            thread_id = owner_id if role == "communication" else None
            await self._drive(role, row["run_id"], thread_id, row["agent_id"], row["session_id"], timeout)

    async def _drive(self, role: str, run_id: str, thread_id: str | None, agent_id: str, session_id: str,
                     timeout: float | None = None) -> str:
        """Drive one turn to run.finished. On timeout the ZooWork run is interrupted and the stream is
        drained to its (aborted) run.finished, so the next turn starts from a clean cursor; then raises."""
        async def loop() -> str:
            while True:  # the server may close an idle stream before run.finished: reconnect
                cursor = (self.db.session_by_id(session_id) or {}).get("cursor")
                async for event in self.client.stream_events(agent_id, session_id, cursor=cursor):
                    call = custom_tool_use(event)
                    if call is not None and call.phase == "requested":
                        await self._handle_tool(role, run_id, thread_id, agent_id, session_id, call)
                    if event.cursor:
                        self.db.save_cursor(session_id, event.cursor)
                    if is_run_finished(event):
                        outcome = run_outcome(event) or "unknown"
                        if outcome != "succeeded":
                            self.db.log(run_id, "agent_run_failed", f"{role} turn ended: {outcome}", thread_id)
                        return outcome
        try:
            return await asyncio.wait_for(loop(), timeout=timeout or self.s.run_timeout_s)
        except asyncio.TimeoutError:
            self.db.log(run_id, "agent_run_failed", f"{role} turn timed out; interrupting it", thread_id)
            try:
                await self.client.post_events(agent_id, session_id, [{"type": "user.interrupt"}])
                await asyncio.wait_for(loop(), timeout=60)  # consume the aborted run.finished
            except Exception:
                log.exception("Could not interrupt/drain timed-out %s turn in session %s", role, session_id)
            raise

    async def _handle_tool(self, role: str, run_id: str, thread_id: str | None, agent_id: str,
                           session_id: str, call: Any) -> None:
        done = self.db.get_tool_call(call.call_id)
        if done:  # replayed event: re-send the stored result in case the first resolve was lost
            content, is_error = done["result"], bool(done["is_error"])
        else:
            ctx = self.ctx_factory(role, run_id, thread_id)
            content, is_error = await asyncio.to_thread(dispatch, ctx, call.name or "", call.input)
            self.db.save_tool_call(call.call_id, role, session_id, call.name or "", call.input, content, is_error)
        try:
            await self.client.resolve_custom_tool_call(
                agent_id, call.call_id, content=content, is_error=is_error, resolved_by="pd-backend")
        except ZooworkError as exc:
            if exc.status == 409:  # already resolved / no longer pending
                return
            raise

    async def recover_pending_tool_calls(self) -> None:
        """After a restart, finish custom tool calls that were requested while we were down."""
        for role, agent_id in self.agent_ids.items():
            for rec in await self.client.list_custom_tool_calls(agent_id, status="pending"):
                srow = self.db.session_by_id(rec["session_id"])
                if not srow:
                    continue
                thread_id = srow["owner_id"] if role == "communication" else None
                call = SimpleNamespace(call_id=rec["call_id"], name=rec["name"], input=rec.get("input"))
                await self._handle_tool(role, srow["run_id"], thread_id, agent_id, rec["session_id"], call)
