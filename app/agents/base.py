"""BaseAgent — shared LLM client with a tool-calling loop.

All Yieldra agents subclass or instantiate ``BaseAgent``. It handles:
  * connecting to the configured provider (``settings.llm_provider``):
      - ``anthropic``: Claude through the Anthropic Messages API (or any
        Anthropic-compatible gateway set with ``LLM_BASE_URL``)
      - ``openai``: any OpenAI-compatible chat-completions endpoint (Qwen, etc.)
  * a multi-turn tool-calling loop (model -> tool -> result -> model -> ...)
  * forced structured output (``run_structured``)
  * retry with exponential backoff
  * structured logging of model, token usage, latency, and tool calls

Agents always pass messages in the OpenAI chat shape
(``{"role": "user", "content": "..." | [{"type": "text"...}, {"type": "image_url"...}]}``);
this module converts them for the provider in use.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.utils.logger import get_logger

log = get_logger("yieldra.agent")

# Type of a tool handler: async function taking keyword args, returning anything JSON-serialisable.
ToolHandler = Callable[..., Awaitable[Any]]

MAX_RETRIES = 3
MAX_TOOL_ITERATIONS = 8
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


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

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass
class AgentResult:
    """Outcome of an agent run."""

    content: str
    tool_calls_made: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""


class StructuredOutputError(RuntimeError):
    """The model did not return the structured answer it was asked for."""


async def fetch_image(url: str) -> tuple[bytes, str] | None:
    """Download an image. Returns ``(bytes, media_type)`` or ``None`` if unavailable.

    Used so photo URLs that embed a secret (Telegram file links contain the bot token)
    are never forwarded to the model provider, and so photos can be fingerprinted.
    """
    if url.startswith("data:"):
        try:
            header, payload = url.split(",", 1)
            media_type = header[5:].split(";")[0] or "image/jpeg"
            return base64.b64decode(payload), media_type
        except (ValueError, TypeError):
            return None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        # Never log the URL itself: it may contain a bot token.
        log.warning("image download failed: %s", type(exc).__name__)
        return None
    data = resp.content
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in _IMAGE_TYPES:
        media_type = _sniff_image_type(data)
    return data, media_type


def _sniff_image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


class BaseAgent:
    """Shared LLM client with tool-calling support."""

    _openai_client: Any = None
    _anthropic_client: Any = None

    def __init__(
        self,
        model: str,
        system_prompt: str,
        tools: list[Tool] | None = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self.tools: list[Tool] = tools or []

    # -- clients -----------------------------------------------------------
    @classmethod
    def openai_client(cls) -> Any:
        """Lazily create one shared async OpenAI-compatible client."""
        if cls._openai_client is None:
            from openai import AsyncOpenAI

            cls._openai_client = AsyncOpenAI(
                api_key=settings.resolved_llm_api_key,
                base_url=settings.resolved_llm_base_url,
            )
        return cls._openai_client

    @classmethod
    def anthropic_client(cls) -> Any:
        """Lazily create one shared async Anthropic client."""
        if cls._anthropic_client is None:
            from anthropic import AsyncAnthropic

            key = settings.resolved_llm_api_key
            base_url = settings.resolved_llm_base_url
            kwargs: dict[str, Any] = {"api_key": key, "max_retries": 0}
            if base_url:
                # Anthropic-compatible gateways differ on which auth header they read,
                # so send the key both as x-api-key and as a bearer token.
                kwargs.update(base_url=base_url, auth_token=key)
            cls._anthropic_client = AsyncAnthropic(**kwargs)
        return cls._anthropic_client

    @classmethod
    def reset_clients(cls) -> None:
        """Drop cached clients (used by tests and after a settings change)."""
        BaseAgent._openai_client = None
        BaseAgent._anthropic_client = None

    @property
    def _provider(self) -> str:
        return settings.llm_provider

    # -- low level ---------------------------------------------------------
    async def _create(self, **kwargs: Any) -> Any:
        """Call the provider's create endpoint with retry + exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                if self._provider == "anthropic":
                    resp = await self.anthropic_client().messages.create(**kwargs)
                else:
                    resp = await self.openai_client().chat.completions.create(**kwargs)
                latency_ms = (time.perf_counter() - start) * 1000
                log.info(
                    "llm call provider=%s model=%s latency=%.0fms tokens=%s",
                    self._provider,
                    kwargs.get("model"),
                    latency_ms,
                    self._usage_dict(resp).get("total_tokens", "?"),
                )
                return resp
            except Exception as exc:  # noqa: BLE001 — retry on any API error
                last_exc = exc
                if attempt < MAX_RETRIES:
                    wait = 2 ** (attempt - 1)
                    log.warning(
                        "llm call failed (attempt %d/%d): %s — retrying in %ds",
                        attempt,
                        MAX_RETRIES,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
        assert last_exc is not None
        log.error("llm call exhausted retries: %s", last_exc)
        raise last_exc

    def _system_text(self, context: dict | None) -> str:
        system = self.system_prompt
        if context:
            system = f"{system}\n\nContext:\n{json.dumps(context, default=str)}"
        return system

    def _full_messages(self, messages: list[dict], context: dict | None) -> list[dict]:
        """OpenAI shape: the system prompt is the first message."""
        return [{"role": "system", "content": self._system_text(context)}, *messages]

    # -- public API --------------------------------------------------------
    async def run(self, messages: list[dict], context: dict | None = None) -> str:
        """Single-shot completion with no tools. Returns the assistant text."""
        if self._provider == "anthropic":
            resp = await self._create(
                model=self.model,
                max_tokens=settings.llm_max_tokens,
                system=self._system_text(context),
                messages=await self._to_anthropic_messages(messages),
            )
            return self._anthropic_text(resp)
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
        if self._provider == "anthropic":
            return await self._run_with_tools_anthropic(messages, tools, context)
        return await self._run_with_tools_openai(messages, tools, context)

    async def run_structured(
        self,
        messages: list[dict],
        name: str,
        description: str,
        schema: dict[str, Any],
        context: dict | None = None,
    ) -> dict[str, Any]:
        """Force the model to answer by filling ``schema``; returns the parsed object.

        Implemented as a forced tool call, which both providers support. The schema is
        a hint to the model, not a guarantee, so callers must validate the result.
        """
        if self._provider == "anthropic":
            resp = await self._create(
                model=self.model,
                max_tokens=settings.llm_max_tokens,
                system=self._system_text(context),
                messages=await self._to_anthropic_messages(messages),
                tools=[{"name": name, "description": description, "input_schema": schema}],
                tool_choice={"type": "tool", "name": name},
            )
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == name:
                    return dict(block.input or {})
            raise StructuredOutputError(f"model did not call {name}")

        resp = await self._create(
            model=self.model,
            messages=self._full_messages(messages, context),
            tools=[
                {
                    "type": "function",
                    "function": {"name": name, "description": description, "parameters": schema},
                }
            ],
            tool_choice={"type": "function", "function": {"name": name}},
        )
        calls = resp.choices[0].message.tool_calls or []
        for call in calls:
            if call.function.name == name:
                try:
                    return json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    raise StructuredOutputError(f"{name} arguments were not valid JSON") from exc
        raise StructuredOutputError(f"model did not call {name}")

    # -- OpenAI-compatible loop -------------------------------------------
    async def _run_with_tools_openai(
        self, messages: list[dict], tools: list[Tool], context: dict | None
    ) -> AgentResult:
        by_name = {t.name: t for t in tools}
        convo = self._full_messages(messages, context)
        tool_calls_made: list[dict] = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for _ in range(MAX_TOOL_ITERATIONS):
            kwargs: dict[str, Any] = {"model": self.model, "messages": convo}
            if tools:
                kwargs["tools"] = [t.to_openai_schema() for t in tools]
                kwargs["tool_choice"] = "auto"
            resp = await self._create(**kwargs)
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

        return self._iteration_limit(tool_calls_made, total_usage)

    # -- Anthropic loop ----------------------------------------------------
    async def _run_with_tools_anthropic(
        self, messages: list[dict], tools: list[Tool], context: dict | None
    ) -> AgentResult:
        by_name = {t.name: t for t in tools}
        convo = await self._to_anthropic_messages(messages)
        tool_calls_made: list[dict] = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for _ in range(MAX_TOOL_ITERATIONS):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": settings.llm_max_tokens,
                "system": self._system_text(context),
                "messages": convo,
            }
            if tools:
                kwargs["tools"] = [t.to_anthropic_schema() for t in tools]
            resp = await self._create(**kwargs)
            self._accumulate_usage(total_usage, resp)

            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                return AgentResult(
                    content=self._anthropic_text(resp),
                    tool_calls_made=tool_calls_made,
                    usage=total_usage,
                    model=self.model,
                )

            # Echo the assistant turn (text + tool_use blocks), then answer every
            # tool_use in a single user turn, as the Messages API requires.
            assistant_blocks: list[dict[str, Any]] = []
            for block in resp.content:
                kind = getattr(block, "type", None)
                if kind == "text" and block.text:
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif kind == "tool_use":
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": dict(block.input or {}),
                        }
                    )
            convo.append({"role": "assistant", "content": assistant_blocks})

            results: list[dict[str, Any]] = []
            for block in tool_uses:
                args = dict(block.input or {})
                result = await self._invoke_tool(by_name, block.name, args)
                tool_calls_made.append({"name": block.name, "args": args, "result": result})
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            convo.append({"role": "user", "content": results})

        return self._iteration_limit(tool_calls_made, total_usage)

    @staticmethod
    def _anthropic_text(resp: Any) -> str:
        return "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text" and b.text
        )

    async def _to_anthropic_messages(self, messages: list[dict]) -> list[dict]:
        """Convert OpenAI-shaped chat messages to Anthropic Messages API params."""
        out: list[dict] = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                # System text travels in the ``system`` parameter, not in messages.
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            blocks: list[dict[str, Any]] = []
            for part in content:
                kind = part.get("type")
                if kind == "text":
                    blocks.append({"type": "text", "text": part.get("text", "")})
                elif kind == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    blocks.append(await self._anthropic_image_block(url))
                else:
                    # Already an Anthropic block (image, tool_result, ...): pass through.
                    blocks.append(part)
            out.append({"role": role, "content": blocks})
        return out

    @staticmethod
    async def _anthropic_image_block(url: str) -> dict[str, Any]:
        fetched = await fetch_image(url)
        if fetched is None:
            # Could not download it ourselves; let the provider fetch the URL.
            return {"type": "image", "source": {"type": "url", "url": url}}
        data, media_type = fetched
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(data).decode("ascii"),
            },
        }

    # -- shared helpers ----------------------------------------------------
    def _iteration_limit(self, tool_calls_made: list[dict], usage: dict[str, int]) -> AgentResult:
        log.warning("tool loop hit MAX_TOOL_ITERATIONS for model=%s", self.model)
        return AgentResult(
            content="(stopped: tool iteration limit reached)",
            tool_calls_made=tool_calls_made,
            usage=usage,
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
    def _usage_dict(resp: Any) -> dict[str, int]:
        """Normalise provider usage to prompt/completion/total token counts."""
        usage = getattr(resp, "usage", None)
        if not usage:
            return {}
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if prompt is None and completion is None:
            # Anthropic naming.
            prompt = getattr(usage, "input_tokens", 0) or 0
            completion = getattr(usage, "output_tokens", 0) or 0
        prompt = prompt or 0
        completion = completion or 0
        total = getattr(usage, "total_tokens", None) or (prompt + completion)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    @classmethod
    def _accumulate_usage(cls, total: dict[str, int], resp: Any) -> None:
        for key, value in cls._usage_dict(resp).items():
            total[key] = total.get(key, 0) + value
