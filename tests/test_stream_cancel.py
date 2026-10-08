"""Exercise browser-style stream disconnects over a real local HTTP connection.

TestClient buffers response bodies, so it cannot prove that aborting a live
generation stops its model task and releases the per-owner generation lock.
This test uses a deterministic model and an isolated, temporary SQLite database.
"""
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]

# Keep this server in the test file so the production package has no test-only
# endpoints. Disable dotenv before importing the app: tests never read local keys.
APP_SCRIPT = r'''
import asyncio
import json
from pathlib import Path
import sys

import dotenv
import uvicorn
dotenv.load_dotenv = lambda *args, **kwargs: False

from app.config import Settings
from app.main import create_app

database, cancelled_file = sys.argv[2:4]
html = (Path("app") / "example.html").read_text()

class DelayedModel:
    async def complete(self, system, user, max_tokens=8000):
        context = json.loads(user.split("\n计划：")[0])
        if max_tokens == 700:
            return {"title": "断连测试项目", "steps": ["构建应用", "保存数据"]}
        if context["request"] == "cancel during build":
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                Path(cancelled_file).write_text("cancelled")
                raise
        return {"html": html, "summary": "可操作的测试页面"}

app = create_app(Settings(mode="mock", database=database), DelayedModel())
'''

SERVER = APP_SCRIPT + r'''
uvicorn.run(app, fd=int(sys.argv[1]), log_level="error", access_log=False)
'''


def _test_environment(database):
    """Do not inherit a real model configuration into the isolated test server."""
    return {
        **os.environ,
        "DATABASE_PATH": str(database),
        "LLM_MODE": "mock",
        "DEEPSEEK_API_KEY": "",
        "COOKIE_SECURE": "false",
        "PYTHON_DOTENV_DISABLED": "1",
    }


# A separate ASGI regression still runs in sandboxes that forbid loopback
# listeners. It delivers http.disconnect while build is pending, rather than
# relying on TestClient's fully buffered response or cancelling run_agent alone.
ASGI_DISCONNECT = APP_SCRIPT + r'''
import httpx

async def check_disconnect():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        created = await client.post("/api/projects", json={"title": "保留已有成果"})
        assert created.status_code == 201
        path = "/api/projects/" + created.json()["id"]
        generated = await client.post(path + "/generate", json={"prompt": "initial version"})
        assert generated.status_code == 200 and '"type": "done"' in generated.text
        before = (await client.get(path)).json()
        saved_state = {"tasks": [{"id": "keep", "text": "保留已有数据", "done": False}]}
        assert (await client.put(path + "/state", json={"value": saved_state})).status_code == 200
        other = await client.post("/api/projects", json={"title": "同一会话的另一个项目"})
        assert other.status_code == 201
        other_path = "/api/projects/" + other.json()["id"]

        disconnected = asyncio.Event()
        build_started = asyncio.Event()
        body_sent = False
        status = None
        body = json.dumps({"prompt": "cancel during build"}).encode()

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            if message["type"] == "http.response.body" and b'"stage": "build"' in message.get("body", b""):
                build_started.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": path + "/generate", "raw_path": (path + "/generate").encode(),
            "query_string": b"", "root_path": "",
            "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                        (b"cookie", ("atoms_owner=" + client.cookies.get("atoms_owner")).encode())],
            "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
        }
        task = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(build_started.wait(), timeout=3)
            assert status == 200
            busy = await client.post(other_path + "/generate", json={"prompt": "must be busy"})
            assert busy.status_code == 409
            disconnected.set()
            await asyncio.wait_for(task, timeout=3)
        finally:
            if not task.done():
                task.cancel()
        after = (await client.get(path)).json()
        assert Path(cancelled_file).read_text() == "cancelled"
        assert after["last_run"]["status"] in {"cancelled", "interrupted"}
        assert after["current_version"] == before["current_version"]
        assert after["html"] == before["html"]
        assert len(after["versions"]) == len(before["versions"])
        assert (await client.get(path + "/state")).json() == saved_state
        retry = await client.post(path + "/generate", json={"prompt": "retry after cancel"})
        assert retry.status_code == 200 and '"type": "done"' in retry.text
        completed = (await client.get(path)).json()
        assert completed["last_run"]["status"] == "completed"
        assert len(completed["versions"]) == len(before["versions"]) + 1
        assert (await client.get(path + "/state")).json() == saved_state

asyncio.run(check_disconnect())
'''


def test_asgi_disconnect_preserves_version_and_releases_lock(tmp_path):
    database = tmp_path / "asgi-stream.sqlite"
    result = subprocess.run(
        [sys.executable, "-c", ASGI_DISCONNECT, "unused", str(database), str(tmp_path / "cancelled.txt")],
        cwd=ROOT, env=_test_environment(database), capture_output=True, text=True, timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def streaming_server(tmp_path):
    """Start one real worker; preserve its OS-reserved port across process launch."""
    if os.name != "posix":
        pytest.skip("This subprocess fixture uses POSIX inherited socket descriptors")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
    except OSError as exc:
        listener.close()
        if exc.errno in {errno.EPERM, errno.EACCES}:
            pytest.skip(f"Environment forbids local HTTP listeners: {exc}")
        raise
    listener.listen(128)
    base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    database = tmp_path / "stream.sqlite"
    cancelled_file = tmp_path / "model-cancelled.txt"
    log_path = tmp_path / "server.log"
    env = _test_environment(database)
    process = None
    try:
        with log_path.open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-c", SERVER, str(listener.fileno()), str(database), str(cancelled_file)],
                cwd=ROOT,
                env=env,
                pass_fds=(listener.fileno(),),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            listener.close()
            deadline = time.monotonic() + 8
            with httpx.Client(base_url=base_url, timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail("Local test server exited: " + log_path.read_text())
                    try:
                        if client.get("/healthz").status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.03)
                else:
                    pytest.fail("Local test server did not become ready: " + log_path.read_text())
            yield base_url, cancelled_file
    finally:
        listener.close()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def test_stream_disconnect_cancels_model_and_releases_owner_lock(streaming_server):
    base_url, cancelled_file = streaming_server
    with httpx.Client(base_url=base_url, timeout=5, trust_env=False) as client:
        created = client.post("/api/projects", json={"title": "保留已有成果"})
        assert created.status_code == 201
        pid = created.json()["id"]
        path = f"/api/projects/{pid}"
        response = client.post(path + "/generate", json={"prompt": "initial version"})
        assert response.status_code == 200
        assert any(json.loads(line[6:])["type"] == "done" for line in response.text.splitlines() if line.startswith("data: "))
        before = client.get(path).json()
        saved_state = {"tasks": [{"id": "keep", "text": "保留已有数据", "done": False}]}
        assert client.put(path + "/state", json={"value": saved_state}).status_code == 200
        other = client.post("/api/projects", json={"title": "同一会话的另一个项目"})
        assert other.status_code == 201
        other_path = "/api/projects/" + other.json()["id"]

        # Consume only progress events, then close the response while the fake
        # model is sleeping. This sends a real disconnect, like fetch.abort().
        with client.stream("POST", path + "/generate", json={"prompt": "cancel during build"}) as response:
            assert response.status_code == 200
            # Keep the iterator alive while checking the owner lock. Discarding
            # a temporary iter_lines() generator on break closes HTTP Core's
            # underlying stream via GeneratorExit, cancelling the task early.
            lines = response.iter_lines()
            for line in lines:
                if line.startswith("data: "):
                    item = json.loads(line[6:])
                    if item.get("stage") == "build":
                        break
            else:
                pytest.fail("Generation ended before the delayed build phase")
            busy = client.post(other_path + "/generate", json={"prompt": "must be busy"})
            assert busy.status_code == 409
        lines.close()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            after = client.get(path).json()
            if after["last_run"]["status"] != "running" and cancelled_file.exists():
                break
            time.sleep(0.03)
        else:
            pytest.fail("Disconnect did not cancel the model and release the run within 5 seconds")

        assert after["last_run"]["status"] in {"cancelled", "interrupted"}
        assert after["current_version"] == before["current_version"]
        assert after["html"] == before["html"]
        assert len(after["versions"]) == len(before["versions"])
        assert client.get(path + "/state").json() == saved_state

        retry = client.post(path + "/generate", json={"prompt": "retry after cancel"})
        assert retry.status_code == 200
        assert any(json.loads(line[6:])["type"] == "done" for line in retry.text.splitlines() if line.startswith("data: "))
        completed = client.get(path).json()
        assert completed["last_run"]["status"] == "completed"
        assert len(completed["versions"]) == len(before["versions"]) + 1
        assert client.get(path + "/state").json() == saved_state
