"""BaseAgent against a fake Anthropic client: message conversion, tool loop, structured output."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.base import BaseAgent, StructuredOutputError, Tool
from app.config import Settings

from tests.conftest import PNG_1PX


def text(value: str):
    return SimpleNamespace(type="text", text=value)


def tool_use(id_: str, name: str, input_: dict):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def response(*blocks, tokens=(10, 5)):
    return SimpleNamespace(
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


class FakeAnthropic:
    """Records every messages.create call and replays queued responses."""

    def __init__(self, *responses):
        self.calls: list[dict] = []
        self._queue = list(responses)
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        # Snapshot the conversation: the agent keeps appending to the same list.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._queue.pop(0)


@pytest.fixture
def fake(monkeypatch):
    def install(*responses) -> FakeAnthropic:
        client = FakeAnthropic(*responses)
        monkeypatch.setattr(BaseAgent, "_anthropic_client", client)
        return client

    return install


async def test_run_sends_system_separately_and_returns_text(fake):
    client = fake(response(text("ready")))
    agent = BaseAgent(model="claude-test", system_prompt="Be brief.")

    out = await agent.run([{"role": "user", "content": "hello"}], context={"farm": "A"})

    assert out == "ready"
    call = client.calls[0]
    assert call["model"] == "claude-test"
    assert call["system"].startswith("Be brief.") and '"farm": "A"' in call["system"]
    assert call["messages"] == [{"role": "user", "content": "hello"}]
    assert call["max_tokens"] > 0


async def test_tool_loop_runs_tool_and_feeds_result_back(fake):
    seen: list[str] = []

    async def get_price(crop: str) -> dict:
        seen.append(crop)
        return {"price": 150}

    tool = Tool("get_price", "Price lookup", {"type": "object", "properties": {}}, get_price)
    client = fake(
        response(text("Checking."), tool_use("tu_1", "get_price", {"crop": "cassava"})),
        response(text("Cassava is 150 per kg.")),
    )
    agent = BaseAgent(model="claude-test", system_prompt="s", tools=[tool])

    result = await agent.run_with_tools([{"role": "user", "content": "price?"}])

    assert seen == ["cassava"]
    assert result.content == "Cassava is 150 per kg."
    assert result.tool_calls_made == [
        {"name": "get_price", "args": {"crop": "cassava"}, "result": {"price": 150}}
    ]
    assert result.usage == {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
    assert client.calls[0]["tools"][0] == {
        "name": "get_price",
        "description": "Price lookup",
        "input_schema": {"type": "object", "properties": {}},
    }
    # Second call carries the assistant's tool_use and our tool_result, correctly paired.
    assistant, tool_result = client.calls[1]["messages"][1:]
    assert assistant["role"] == "assistant"
    assert assistant["content"][1] == {
        "type": "tool_use", "id": "tu_1", "name": "get_price", "input": {"crop": "cassava"},
    }
    assert tool_result == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": '{"price": 150}'}],
    }


async def test_tool_error_is_reported_to_the_model_not_raised(fake):
    async def boom() -> dict:
        raise RuntimeError("storage offline")

    tool = Tool("boom", "fails", {"type": "object", "properties": {}}, boom)
    client = fake(response(tool_use("tu_1", "boom", {})), response(text("Sorry.")))
    agent = BaseAgent(model="claude-test", system_prompt="s", tools=[tool])

    result = await agent.run_with_tools([{"role": "user", "content": "go"}])

    assert result.content == "Sorry."
    assert "storage offline" in client.calls[1]["messages"][2]["content"][0]["content"]


async def test_images_are_sent_as_base64_blocks(fake):
    client = fake(response(text("a leaf")))
    agent = BaseAgent(model="claude-test", system_prompt="s")

    await agent.run(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": PNG_1PX}},
                ],
            }
        ]
    )

    blocks = client.calls[0]["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "what is this?"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["type"] == "base64"
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert blocks[1]["source"]["data"] == PNG_1PX.split(",", 1)[1]


async def test_run_structured_forces_the_tool_and_returns_its_input(fake):
    client = fake(response(tool_use("tu_1", "record", {"ok": True})))
    agent = BaseAgent(model="claude-test", system_prompt="s")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    out = await agent.run_structured(
        [{"role": "user", "content": "q"}], name="record", description="d", schema=schema
    )

    assert out == {"ok": True}
    assert client.calls[0]["tool_choice"] == {"type": "tool", "name": "record"}
    assert client.calls[0]["tools"] == [
        {"name": "record", "description": "d", "input_schema": schema}
    ]


async def test_run_structured_raises_when_model_answers_in_prose(fake):
    fake(response(text("I think yes")))
    agent = BaseAgent(model="claude-test", system_prompt="s")

    with pytest.raises(StructuredOutputError):
        await agent.run_structured(
            [{"role": "user", "content": "q"}], name="record", description="d", schema={}
        )


def test_gateway_base_url_sends_key_in_both_auth_headers(live_setting):
    live_setting(llm_api_key="gateway-key", llm_base_url="https://gateway.example")
    BaseAgent.reset_clients()

    client = BaseAgent.anthropic_client()

    assert str(client.base_url).rstrip("/") == "https://gateway.example"
    assert client.auth_headers == {
        "X-Api-Key": "gateway-key",
        "Authorization": "Bearer gateway-key",
    }


def test_model_defaults_follow_provider_and_can_be_overridden():
    claude = Settings(_env_file=None, llm_provider="anthropic", model_report="")
    assert claude.model_vision.startswith("claude-")

    qwen = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_api_key="xxx",
        llm_base_url="",
        dashscope_api_key="sk-real",
        model_vision="",
        model_report="my-model",
    )
    assert qwen.model_vision.startswith("qwen")
    assert qwen.model_report == "my-model"
    # Legacy Qwen variables still work when the generic ones are not set.
    assert qwen.resolved_llm_api_key == "sk-real"
    assert "dashscope" in qwen.resolved_llm_base_url

    with pytest.raises(ValueError):
        Settings(_env_file=None, llm_provider="nope")


async def test_real_anthropic_sdk_round_trip_over_fake_http(monkeypatch):
    """Drive the real SDK (request building + response parsing) against a fake server."""
    import json

    import anthropic._base_client as sdk_base
    from anthropic import AsyncAnthropic

    # Use whichever HTTP library this SDK version is built on (httpx or httpx2).
    httpx = getattr(sdk_base, "httpx2", None) or sdk_base.httpx

    bodies: list[dict] = []
    replies = [
        {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
            "stop_reason": "tool_use", "stop_sequence": None,
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_1", "name": "get_price", "input": {"crop": "maize"}},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 7},
        },
        {
            "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-test",
            "stop_reason": "end_turn", "stop_sequence": None,
            "content": [{"type": "text", "text": "Maize is 250 per kg."}],
            "usage": {"input_tokens": 30, "output_tokens": 9},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(bodies) - 1])

    client = AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(BaseAgent, "_anthropic_client", client)

    async def get_price(crop: str) -> dict:
        return {"crop": crop, "price": 250}

    tool = Tool(
        "get_price", "Price lookup",
        {"type": "object", "properties": {"crop": {"type": "string"}}, "required": ["crop"]},
        get_price,
    )
    agent = BaseAgent(model="claude-test", system_prompt="Be brief.", tools=[tool])

    result = await agent.run_with_tools(
        [{"role": "user", "content": [
            {"type": "text", "text": "price of this crop?"},
            {"type": "image_url", "image_url": {"url": PNG_1PX}},
        ]}]
    )

    assert result.content == "Maize is 250 per kg."
    assert result.tool_calls_made[0]["result"] == {"crop": "maize", "price": 250}
    assert result.usage == {"prompt_tokens": 42, "completion_tokens": 16, "total_tokens": 58}
    first, second = bodies
    assert first["system"] == "Be brief." and first["tools"][0]["name"] == "get_price"
    assert first["messages"][0]["content"][1]["source"]["media_type"] == "image/png"
    assert second["messages"][1]["content"][1]["type"] == "tool_use"
    assert second["messages"][2]["content"][0] == {
        "type": "tool_result", "tool_use_id": "toolu_1",
        "content": '{"crop": "maize", "price": 250}',
    }
