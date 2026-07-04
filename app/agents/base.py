"""BaseAgent — shared Qwen (OpenAI-compatible) client with a tool-calling loop.

All six Yieldra agents subclass or instantiate ``BaseAgent``. It handles:
  * connecting to Alibaba Cloud Model Studio via the OpenAI SDK
  * a multi-turn tool-calling loop (model -> tool -> result -> model -> ...)
  * retry with exponential backoff
  * structured logging of model, token usage, latency, and tool calls
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app.config import settings
from app.utils.logger import get_logger

log = get_logger("yieldra.agent")

# Type of a tool handler: async function taking keyword args, returning anything JSON-serialisable.
ToolHandler = Callable[..., Awaitable[Any]]

MAX_RETRIES = 3
MAX_TOOL_ITERATIONS = 8


@dataclass
class Tool:
    """A function the model can call. ``parameters`` is a JSON-Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class AgentResult:
    """Outcome of an agent run."""

    content: str
    tool_calls_made: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""


class BaseAgent:
    """Shared Qwen client with tool-calling support."""

    _client: AsyncOpenAI | None = None

    def __init__(
        self,
        model: str,
        system_prompt: str,
        tools: list[Tool] | None = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self.tools: list[Tool] = tools or []

    # -- client ------------------------------------------------------------
    @classmethod
    def client(cls) -> AsyncOpenAI:
        """Lazily create one shared async OpenAI-compatible client for Qwen."""
        if cls._client is None:
            cls._client = AsyncOpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.qwen_base_url,
            )
        return cls._client

    # -- low level ---------------------------------------------------------
    async def _create(self, **kwargs: Any):
        """Call chat.completions.create with retry + exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                resp = await self.client().chat.completions.create(**kwargs)
                latency_ms = (time.perf_counter() - start) * 1000
                usage = getattr(resp, "usage", None)
                log.info(
                    "qwen call model=%s latency=%.0fms tokens=%s",
                    kwargs.get("model"),
                    latency_ms,
                    getattr(usage, "total_tokens", "?"),
                )
                return resp
            except Exception as exc:  # noqa: BLE001 — retry on any API error
                last_exc = exc
                wait = 2 ** (attempt - 1)
                log.warning(
                    "qwen call failed (attempt %d/%d): %s — retrying in %ds",
                    attempt,
                    MAX_RETRIES,
                    exc,
                    wait,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(wait)
        assert last_exc is not None
        log.error("qwen call exhausted retries: %s", last_exc)
        raise last_exc

    # -- public API --------------------------------------------------------
    def _full_messages(self, messages: list[dict], context: dict | None) -> list[dict]:
        system = self.system_prompt
        if context:
            system = f"{system}\n\nContext:\n{json.dumps(context, default=str)}"
        return [{"role": "system", "content": system}, *messages]

    async def run(self, messages: list[dict], context: dict | None = None) -> str:
        """Single-shot completion with no tools. Returns the assistant text."""
        resp = await self._create(
            model=self.model,
            messages=self._full_messages(messages, context),
        )
        return resp.choices[0].message.content or ""

    async def run_with_tools(
        self,
        messages: list[dict],
        available_tools: list[Tool] | None = None,
        context: dict | None = None,
    ) -> AgentResult:
        """Run the model with tool calling until it returns a final text answer."""
        tools = available_tools if available_tools is not None else self.tools
        by_name = {t.name: t for t in tools}
        convo = self._full_messages(messages, context)
        tool_calls_made: list[dict] = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await self._create(
                model=self.model,
                messages=convo,
                tools=[t.to_openai_schema() for t in tools] or None,
                tool_choice="auto" if tools else None,
            )
            self._accumulate_usage(total_usage, resp)
            msg = resp.choices[0].message

            if not msg.tool_calls:
                return AgentResult(
                    content=msg.content or "",
                    tool_calls_made=tool_calls_made,
                    usage=total_usage,
                    model=self.model,
                )

            # Append the assistant turn that requested tools.
            convo.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                }
            )

            # Execute each requested tool and feed results back.
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await self._invoke_tool(by_name, name, args)
                tool_calls_made.append({"name": name, "args": args, "result": result})
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        log.warning("tool loop hit MAX_TOOL_ITERATIONS for model=%s", self.model)
        return AgentResult(
            content="(stopped: tool iteration limit reached)",
            tool_calls_made=tool_calls_made,
            usage=total_usage,
            model=self.model,
        )

    async def _invoke_tool(
        self, by_name: dict[str, Tool], name: str, args: dict
    ) -> Any:
        tool = by_name.get(name)
        if tool is None:
            log.warning("model requested unknown tool: %s", name)
            return {"error": f"unknown tool: {name}"}
        try:
            log.info("tool call %s args=%s", name, args)
            return await tool.handler(**args)
        except Exception as exc:  # noqa: BLE001 — surface tool errors back to model
            log.exception("tool %s raised", name)
            return {"error": str(exc)}

    @staticmethod
    def _accumulate_usage(total: dict[str, int], resp: Any) -> None:
        usage = getattr(resp, "usage", None)
        if not usage:
            return
        for key in total:
            total[key] += getattr(usage, key, 0) or 0
