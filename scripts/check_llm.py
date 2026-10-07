"""Check that the configured LLM key and endpoint work for Yieldra's agents.

    python scripts/check_llm.py

Runs three small calls against whatever LLM_PROVIDER / LLM_API_KEY / LLM_BASE_URL
point at: a plain reply, a tool call, and a forced structured answer. Use it to
confirm a new key (Anthropic, an Anthropic-compatible gateway, or Qwen) before
starting the app. Exits non-zero if any check fails.
"""

from __future__ import annotations

import asyncio
import sys

from app.agents import base
from app.agents.base import BaseAgent, Tool
from app.config import settings

# One attempt per check is enough here; fail fast instead of backing off.
base.MAX_RETRIES = 1


async def _plain(agent: BaseAgent) -> str:
    text = await agent.run([{"role": "user", "content": "Reply with the single word: ready"}])
    if not text.strip():
        raise RuntimeError("empty reply")
    return text.strip()[:60]


async def _tool_call(agent: BaseAgent) -> str:
    async def get_price(crop: str) -> dict:
        return {"crop": crop, "price_per_kg_ngn": 150}

    tool = Tool(
        name="get_price",
        description="Look up the farm-gate price per kg for a crop.",
        parameters={
            "type": "object",
            "properties": {"crop": {"type": "string"}},
            "required": ["crop"],
        },
        handler=get_price,
    )
    result = await agent.run_with_tools(
        [{"role": "user", "content": "Use the tool to get the cassava price, then state it."}],
        available_tools=[tool],
    )
    if not result.tool_calls_made:
        raise RuntimeError("the model answered without calling the tool")
    return f"{len(result.tool_calls_made)} tool call(s), {result.usage.get('total_tokens', '?')} tokens"


async def _structured(agent: BaseAgent) -> str:
    out = await agent.run_structured(
        [{"role": "user", "content": "Is cassava a root crop?"}],
        name="record_answer",
        description="Record the answer.",
        schema={
            "type": "object",
            "properties": {"answer": {"type": "boolean"}},
            "required": ["answer"],
        },
    )
    if "answer" not in out:
        raise RuntimeError(f"unexpected structured answer: {out}")
    return str(out)


async def main() -> int:
    endpoint = settings.resolved_llm_base_url or "provider default"
    print(f"provider={settings.llm_provider}  endpoint={endpoint}  model={settings.model_advisory}")
    if not settings.llm_live:
        print("FAIL  no key set: put LLM_API_KEY in .env")
        return 1

    agent = BaseAgent(model=settings.model_advisory, system_prompt="You are a concise assistant.")
    failed = 0
    for label, check in (
        ("plain reply", _plain),
        ("tool call", _tool_call),
        ("structured answer", _structured),
    ):
        try:
            print(f"ok    {label}: {await check(agent)}")
        except Exception as exc:  # noqa: BLE001 — report every failure, then exit non-zero
            failed += 1
            print(f"FAIL  {label}: {type(exc).__name__}: {str(exc)[:300]}")
    if failed:
        print(f"\n{failed} check(s) failed. The agents will not work with this key/endpoint as set.")
        return 1
    print("\nAll checks passed. The agents can use this key and endpoint.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
