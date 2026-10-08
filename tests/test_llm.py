import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.llm import DeepSeek, ModelError


@pytest.mark.parametrize("status,expected", [(401, "API Key"), (402, "余额"), (429, "限流"), (503, "暂时不可用")])
def test_upstream_failures_are_redacted(monkeypatch, status, expected):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "secret-upstream-debug"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(ModelError) as error:
        asyncio.run(DeepSeek(Settings(api_key="secret")).complete("JSON", "test"))
    assert expected in str(error.value)
    assert "secret" not in str(error.value)


def test_real_adapter_payload_and_truncated_output(monkeypatch):
    original = httpx.AsyncClient
    def handler(request):
        assert request.url == "https://api.deepseek.com/chat/completions"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"]["type"] == "disabled"
        assert request.headers["authorization"] == "Bearer local-test"
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": '{"html":"partial"}'}}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(ModelError, match="未完整结束"):
        asyncio.run(DeepSeek(Settings(api_key="local-test")).complete("JSON", "test"))


def test_valid_deepseek_response(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"title":"hello"}'}}]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    assert asyncio.run(DeepSeek(Settings(api_key="local-test")).complete("JSON", "test")) == {"title": "hello"}
