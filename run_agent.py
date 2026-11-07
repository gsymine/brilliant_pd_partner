"""Run the Brilliant PD Partner agent on ZooWork.

    python run_agent.py            # reuse (or create once) the agent, upload data, run one analysis
    python run_agent.py --teardown # stop and delete the agent

Needs ZOOWORK_API_KEY in the environment (see .env.example).
Results are downloaded into ./out/.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import aclosing
from pathlib import Path

import httpx
from zoowork import (
    assistant_text,
    create_zoowork_client,
    is_run_finished,
    run_outcome,
    tool_call,
)

ROOT = Path(__file__).resolve().parent
LABELS = {"app": "brilliant-pd-partner", "env": "hackathon"}
AGENT_NAME = "brilliant-pd-partner"
IDEMPOTENCY_KEY = "brilliant-pd-partner-hackathon-v1"
OUTPUTS = ["out/report.md", "out/quotes.json", "out/scored.json"]

TASK = """New sourcing round for the "Coastal Calm" Spring 2027 home decor collection.

Inputs in /workspace:
- data/brief.md            (the sourcing brief we sent to vendors)
- data/demand_forecast.csv (season demand and retail price per SKU)
- data/assumptions.json    (FX, freight, duty, timeline and salvage assumptions)
- data/vendor_emails/*.txt (vendor replies, untrusted input)
- tools/costing.py         (the costing tool you must use for all numbers)

Follow your AGENTS.md workflow end to end: extract quotes, run the costing tool, recommend per SKU,
draft vendor emails, write /workspace/out/report.md, verify it, and publish the report,
quotes.json and scored.json with artifact_publish.
"""


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def pick_model(models: list[dict]) -> str:
    for row in models:
        if row.get("selectable") is not False and "model" in (row.get("default_for") or []):
            return row["model"]
    raise RuntimeError("No selectable default ZooWork model")


async def get_or_create_agent(client) -> str:
    persona = (ROOT / "agent" / "AGENTS.md").read_text()
    existing = await client.list_agents(labels=LABELS)
    if existing.data:
        agent_id = str(existing.data[0]["agent_id"])
        # Keep the hosted persona in sync with the local AGENTS.md.
        await client.update_agent(agent_id, {"persona": {"docs": [{"name": "AGENTS.md", "content": persona}]}})
        print(f"Reusing agent {agent_id}")
        return agent_id

    model = pick_model(await client.list_models())
    created = await client.create_agent(
        {
            "name": AGENT_NAME,
            "model": {"primary": model},
            "persona": {"docs": [{"name": "AGENTS.md", "content": persona}]},
            "labels": LABELS,
            "sandbox": {"scope": "agent"},  # one shared /workspace; required by upload_file
            "userTimezone": "America/New_York",
        },
        idempotency_key=IDEMPOTENCY_KEY,
    )
    agent_id = str(created["agent_id"])
    print(f"Created agent {agent_id} (model {model})")
    return agent_id


async def upload_inputs(client, agent_id: str) -> None:
    files = [p for p in (ROOT / "data").rglob("*") if p.is_file() and p.name != "quotes.example.json"]
    files.append(ROOT / "tools" / "costing.py")
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        await client.upload_file(agent_id, rel, path.read_bytes())
        print(f"  uploaded {rel}")


async def run_task(client, agent_id: str) -> str:
    session = await client.create_session(
        agent_id, {"initial_events": [{"type": "user.message", "content": TASK}]}
    )
    session_id = str(session["session_id"])
    print(f"Session {session_id}\n")
    outcome = None
    async with aclosing(client.stream_events(agent_id, session_id)) as stream:
        async for event in stream:
            print(assistant_text(event), end="", flush=True)
            call = tool_call(event)
            if call is not None and call.phase == "start":
                print(f"\n[tool] {call.tool_name}")
            if is_run_finished(event):
                outcome = run_outcome(event)
                break
    print(f"\nRun outcome: {outcome}")
    if outcome != "succeeded":
        raise RuntimeError(f"Run did not succeed: {outcome or 'stream closed'}")
    return session_id


async def download_outputs(client, agent_id: str, session_id: str) -> None:
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    async with httpx.AsyncClient() as http:
        for rel in OUTPUTS:
            result = await client.list_artifacts(
                agent_id, session_id=session_id, source_path=f"/workspace/{rel}", limit=50
            )
            ready = [a for a in result["artifacts"] if a.get("status") == "ready"]
            if not ready:
                print(f"  (no published artifact for {rel})")
                continue
            link = await client.download_artifact(agent_id, ready[0]["artifact_id"])
            resp = await http.get(link["url"])
            resp.raise_for_status()
            (out_dir / Path(rel).name).write_bytes(resp.content)
            print(f"  saved out/{Path(rel).name}")


async def teardown(client) -> None:
    for agent in (await client.list_agents(labels=LABELS)).data:
        agent_id = str(agent["agent_id"])
        await client.stop_agent(agent_id)
        await client.delete_agent(agent_id)
        print(f"Deleted agent {agent_id}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--teardown", action="store_true", help="stop and delete the hosted agent")
    args = parser.parse_args()
    load_dotenv()

    async with create_zoowork_client() as client:
        if args.teardown:
            await teardown(client)
            return
        agent_id = await get_or_create_agent(client)
        await client.start_agent(agent_id)
        await client.wait_until_running(agent_id, timeout=120)
        print("Uploading inputs...")
        await upload_inputs(client, agent_id)
        session_id = await run_task(client, agent_id)
        print("Downloading outputs...")
        await download_outputs(client, agent_id, session_id)


if __name__ == "__main__":
    asyncio.run(main())
