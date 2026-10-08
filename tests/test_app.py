"""End-to-end HTTP checks with deterministic model doubles; never spend API credits."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import check_html, run_agent
from app.config import Settings
from app.db import Store
from app.llm import ModelError
from app.main import create_app

HTML = (Path(__file__).parents[1] / "app/example.html").read_text()


class ScriptedModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def complete(self, system, user, max_tokens=8000):
        self.calls.append((system, user, max_tokens))
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def plan():
    return {"title": "待办清单", "steps": ["实现任务交互", "保存任务"]}


def result(html=HTML):
    return {"html": html, "summary": "支持任务管理"}


@pytest.fixture
def setup(tmp_path):
    settings = Settings(mode="mock", database=str(tmp_path / "db.sqlite"))
    app = create_app(settings)
    return settings, app


def project(client):
    response = client.post("/api/projects", json={"title": "新项目"})
    assert response.status_code == 201
    return response.json()["id"]


def generate(client, pid, prompt="生成待办清单"):
    response = client.post(f"/api/projects/{pid}/generate", json={"prompt": prompt})
    assert response.status_code == 200
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_complete_modify_restore_and_persist(setup):
    settings, app = setup
    with TestClient(app) as client:
        pid = project(client)
        assert generate(client, pid)[-1]["type"] == "done"
        first = client.get(f"/api/projects/{pid}").json()
        first_id = first["current_version"]
        assert 'data-theme="light"' in first["html"]
        assert first["versions"][0]["mode"] == "mock"
        assert "离线示例" in first["messages"][-1]["content"]
        assert generate(client, pid, "改成深色主题")[-1]["type"] == "done"
        second = client.get(f"/api/projects/{pid}").json()
        assert 'data-theme="dark"' in second["html"]
        assert len(second["versions"]) == 2
        assert len(second["messages"]) == 4
        state = {"tasks": [{"id": "one", "text": "验收", "done": False}]}
        assert client.put(f"/api/projects/{pid}/state", json={"value": state}).status_code == 200
        restored = client.post(f"/api/projects/{pid}/restore/{first_id}", json={}).json()
        assert restored["html"] == first["html"]
        assert len(restored["versions"]) == 2
        assert client.get(f"/api/projects/{pid}/state").json() == state
        owner = client.cookies.get("atoms_owner")
    # Simulate a server restart and the same browser returning.
    with TestClient(create_app(settings)) as client:
        client.cookies.set("atoms_owner", owner)
        saved = client.get(f"/api/projects/{pid}").json()
        assert saved["current_version"] == first_id
        assert client.get(f"/api/projects/{pid}/state").json() == state


def test_owner_isolation_for_all_project_endpoints(setup):
    _, app = setup
    with TestClient(app) as owner, TestClient(app) as other:
        pid = project(owner); generate(owner, pid)
        version = owner.get(f"/api/projects/{pid}").json()["current_version"]
        assert other.get("/api/projects").json() == []
        for method, suffix, body in [
            ("GET", "", None), ("GET", "/state", None), ("GET", "/download", None),
            ("PUT", "/state", {"value": {}}), ("POST", "/generate", {"prompt": "修改"}),
            ("POST", f"/restore/{version}", {}),
        ]:
            assert other.request(method, f"/api/projects/{pid}{suffix}", json=body).status_code == 404


def test_failed_generation_preserves_last_version_and_is_retryable(tmp_path):
    model = ScriptedModel([plan(), result(), plan(), ModelError("测试失败"), plan(), result()])
    app = create_app(Settings(mode="mock", database=str(tmp_path / "test.db")), model)
    with TestClient(app) as client:
        pid = project(client); generate(client, pid)
        before = client.get(f"/api/projects/{pid}").json()
        assert generate(client, pid)[-1] == {"type": "error", "message": "测试失败"}
        after = client.get(f"/api/projects/{pid}").json()
        assert after["current_version"] == before["current_version"]
        assert len(after["versions"]) == 1
        assert generate(client, pid)[-1]["type"] == "done"


def test_repair_once_and_keep_current_code_in_context():
    async def collect(model):
        return [x async for x in run_agent(model, "增加筛选", HTML, [{"role": "user", "content": "原始需求"}])]
    model = ScriptedModel([plan(), result("<p>broken</p>"), result()])
    events = asyncio.run(collect(model))
    assert any(x.get("stage") == "repair" for x in events)
    assert events[-1]["type"] == "result"
    context = json.loads(model.calls[1][1].split("\n计划：")[0])
    assert context["current_html"] == HTML
    assert context["recent_messages"][0]["content"] == "原始需求"
    broken = ScriptedModel([plan(), result("bad"), result("still bad")])
    with pytest.raises(ModelError, match="自动修复后"):
        asyncio.run(collect(broken))
    assert len(broken.calls) == 3


def test_input_limits_csrf_and_download(setup):
    _, app = setup
    with TestClient(app) as client:
        pid = project(client)
        for prompt in ["", "   ", "a" * 4001]:
            assert client.post(f"/api/projects/{pid}/generate", json={"prompt": prompt}).status_code == 422
        assert client.put(f"/api/projects/{pid}/state", json={"value": {"x": "a" * 20001}}).status_code == 413
        assert client.post("/api/projects", headers={"Origin": "https://evil.example"}, json={}).status_code == 403
        assert client.post("/api/projects", headers={"Sec-Fetch-Site": "cross-site"}, json={}).status_code == 403
        generate(client, pid)
        download = client.get(f"/api/projects/{pid}/download")
        assert download.status_code == 200
        assert "attachment" in download.headers["content-disposition"]
        assert "window.demoStorage=" in download.text


def test_missing_key_does_not_fallback_to_mock(tmp_path):
    app = create_app(Settings(mode="deepseek", database=str(tmp_path / "db")))
    with TestClient(app) as client:
        pid = project(client)
        assert client.get("/api/status").json()["configured"] is False
        assert client.post(f"/api/projects/{pid}/generate", json={"prompt": "hello"}).status_code == 503
        assert client.get(f"/api/projects/{pid}").json()["versions"] == []


def test_daily_quota_and_busy_claim(setup):
    settings, app = setup; settings.owner_limit = 1
    with TestClient(app) as client:
        pid = project(client); generate(client, pid)
        assert client.post(f"/api/projects/{pid}/generate", json={"prompt": "again"}).status_code == 429
    store = app.state.store
    other = store.create("another-owner", "Test")["id"]
    store.start_run(other, "another-owner", "task", 20, 100)
    with pytest.raises(ValueError, match="已有任务"):
        store.start_run(other, "another-owner", "task", 20, 100)
    # A fresh process marks stale runs interrupted and allows a new attempt.
    restarted = Store(settings.database)
    assert restarted.get(other, "another-owner")["last_run"]["status"] == "interrupted"


@pytest.mark.parametrize("html", ["", "<html><head></head><body>", '<html><head><script src="https://x"></script></head><body></body></html>', '<html><head></head><body><iframe></iframe></body></html>'])
def test_structural_rejections(html):
    assert check_html(html)


def test_key_never_in_status_or_error(tmp_path):
    app = create_app(Settings(api_key="test-secret-do-not-return", database=str(tmp_path / "db")))
    with TestClient(app) as client:
        assert "test-secret" not in client.get("/api/status").text
