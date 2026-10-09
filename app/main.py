"""HTTP endpoints and streaming progress. Run with: uvicorn app.main:app."""
import asyncio
import json
import logging
import re
import secrets
from html.parser import HTMLParser
from typing import Literal
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .agent import check_html, run_agent
from .config import Settings
from .db import Store, now
from .llm import DeepSeek, ModelError

BASE = Path(__file__).parent


class ProjectInput(BaseModel):
    title: str = Field(default="新项目", min_length=1, max_length=60)


class RenameInput(ProjectInput):
    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请输入项目名称")
        return value


class TemplateInput(BaseModel):
    template_id: str = Field(min_length=1, max_length=60)


class VersionInput(BaseModel):
    html: str = Field(min_length=1, max_length=180_000)
    summary: str = Field(default="手动编辑并保存代码", min_length=1, max_length=1200)
    # Required even for a blank project: null expresses an intentionally empty base.
    base_version_id: str | None = Field(max_length=64)


class BackupVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    number: int = Field(ge=1)
    html: str = Field(min_length=1, max_length=180_000)
    prompt: str = Field(max_length=4000)
    summary: str = Field(max_length=1200)
    mode: str = Field(min_length=1, max_length=40)
    source: Literal["manual", "template", "generated"] = "generated"
    created_at: str = Field(min_length=1, max_length=64)


class BackupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["atoms-demo-backup"]
    format_version: Literal[1]
    title: str = Field(min_length=1, max_length=60)
    current_version: str | None = Field(max_length=64)
    html: str = Field(max_length=180_000)
    versions: list[BackupVersion] = Field(max_length=100)
    app_state: dict

    @model_validator(mode="after")
    def validate_snapshot(self):
        if not self.title.strip():
            raise ValueError("项目名称不能为空")
        if len(json.dumps(self.app_state, ensure_ascii=False, allow_nan=False).encode()) > 20_000:
            raise ValueError("应用数据不能超过20KB")
        ids = [version.id for version in self.versions]
        numbers = [version.number for version in self.versions]
        if len(ids) != len(set(ids)) or numbers != sorted(set(numbers)):
            raise ValueError("版本编号重复或顺序错误")
        for version in self.versions:
            if check_html(version.html):
                raise ValueError("备份中包含未通过页面结构检查的代码")
        selected = next((v for v in self.versions if v.id == self.current_version), None)
        if self.versions and selected is None:
            raise ValueError("当前版本不存在于备份中")
        if not self.versions and (self.current_version is not None or self.html):
            raise ValueError("空项目备份不应包含当前代码")
        if selected and selected.html != self.html:
            raise ValueError("当前代码与版本内容不一致")
        return self


def inject_export_adapter(html, adapter):
    """Find an actual head token, ignoring head-like text in HTML comments."""
    class HeadPosition(HTMLParser):
        def __init__(self):
            super().__init__()
            self.position = None

        def handle_starttag(self, tag, attrs):
            if tag == "head" and self.position is None:
                line, offset = self.getpos()
                self.position = sum(len(part) for part in html.splitlines(keepends=True)[:line - 1]) + offset + len(self.get_starttag_text())

    parser = HeadPosition()
    parser.feed(html)
    if parser.position is None:
        raise ValueError("页面缺少 head 标签，无法导出")
    return html[:parser.position] + adapter + html[parser.position:]


class GenerateInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("prompt")
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请输入具体需求")
        return value


class StateInput(BaseModel):
    value: dict


def create_app(settings=None, model=None):
    settings = settings or Settings.from_env()
    store = Store(settings.database)
    if model is None:
        if settings.mode == "mock":
            from .mock import OfflineModel
            model = OfflineModel()
        else:
            model = DeepSeek(settings)
    app = FastAPI(title="Atoms Demo", docs_url="/docs")
    app.state.store = store

    @app.middleware("http")
    async def session(request: Request, call_next):
        # Opaque, random browser capability. All project queries are scoped to this owner.
        owner = request.cookies.get("atoms_owner", "")
        if not re.fullmatch(r"[a-f0-9]{64}", owner):
            owner = secrets.token_hex(32)
        request.state.owner = owner
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            cross_site = request.headers.get("sec-fetch-site") == "cross-site"
            if cross_site or (origin and urlsplit(origin).netloc != request.url.netloc):
                return JSONResponse({"detail": "不允许跨站提交"}, status_code=403)
        response = await call_next(request)
        if request.cookies.get("atoms_owner") != owner:
            response.set_cookie("atoms_owner", owner, httponly=True, samesite="strict", secure=settings.cookie_secure, max_age=60*60*24*30)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; frame-src 'self'; object-src 'none'; base-uri 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def owned(pid, request):
        project = store.get(pid, request.state.owner)
        if project is None:
            raise HTTPException(404, "项目不存在或不属于当前会话")
        return project

    @app.get("/")
    def index():
        return FileResponse(BASE / "static/index.html")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/api/status")
    def status():
        return {"mode": settings.mode, "configured": bool(settings.api_key) or settings.mode == "mock", "model": settings.model, "owner_limit": settings.owner_limit}

    def capacity(request):
        if store.count(request.state.owner) >= 100:
            raise HTTPException(429, "当前会话最多创建100个项目（含归档项目）")

    def change(action):
        try:
            return action()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OverflowError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/api/templates")
    def templates():
        from .templates import list_templates
        return list_templates()

    @app.get("/api/projects")
    def list_projects(request: Request, archived: bool = False):
        return store.list(request.state.owner, archived)

    @app.post("/api/projects/from-template", status_code=201)
    def from_template(data: TemplateInput, request: Request):
        from .templates import get_template
        capacity(request)
        try:
            template = get_template(data.template_id)
        except KeyError:
            template = None
        if template is None:
            raise HTTPException(404, "模板不存在")
        issues = check_html(template["html"])
        if issues:
            raise HTTPException(422, "模板代码未通过检查：" + "；".join(issues))
        vid = secrets.token_hex(16)
        summary = "已创建「" + template["title"] + "」模板，可直接使用或继续修改。"
        snapshot = {"title": template["title"], "current_version": vid, "app_state": {}, "versions": [{"id": vid, "html": template["html"], "prompt": template["prompt"], "summary": summary, "mode": "template", "source": "template", "created_at": now()}]}
        return change(lambda: store.import_snapshot(request.state.owner, snapshot, notice=summary))

    @app.post("/api/projects/import", status_code=201)
    async def import_project(request: Request):
        capacity(request)
        # Read in chunks so unexpectedly large files fail before JSON parsing.
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 20_000_000:
                raise HTTPException(413, "备份文件不能超过20MB")
            chunks.append(chunk)
        try:
            data = BackupInput.model_validate_json(b"".join(chunks))
        except ValidationError as exc:
            # Pydantic error representations can contain the entire uploaded code.
            raise HTTPException(422, "备份格式或内容无效，请使用本应用导出的JSON备份（最多100个版本、20KB应用数据）。") from exc
        return change(lambda: store.import_snapshot(request.state.owner, data.model_dump()))

    @app.post("/api/projects", status_code=201)
    def create_project(data: ProjectInput, request: Request):
        capacity(request)
        return change(lambda: store.create(request.state.owner, data.title.strip() or "新项目"))

    @app.get("/api/projects/{pid}")
    def get_project(pid: str, request: Request):
        return owned(pid, request)

    @app.patch("/api/projects/{pid}")
    def rename_project(pid: str, data: RenameInput, request: Request):
        owned(pid, request)
        change(lambda: store.rename(pid, data.title))
        return owned(pid, request)

    @app.post("/api/projects/{pid}/archive")
    def archive_project(pid: str, request: Request):
        owned(pid, request)
        change(lambda: store.archive(pid, True))
        return owned(pid, request)

    @app.post("/api/projects/{pid}/unarchive")
    def unarchive_project(pid: str, request: Request):
        owned(pid, request)
        change(lambda: store.archive(pid, False))
        return owned(pid, request)

    @app.delete("/api/projects/{pid}", status_code=204)
    def delete_project(pid: str, request: Request):
        owned(pid, request)
        change(lambda: store.delete_archived(pid))
        return Response(status_code=204)

    @app.post("/api/projects/{pid}/duplicate", status_code=201)
    def duplicate_project(pid: str, request: Request):
        owned(pid, request)
        capacity(request)
        return change(lambda: store.duplicate(pid, request.state.owner))

    @app.get("/api/projects/{pid}/versions/{vid}")
    def get_version(pid: str, vid: str, request: Request):
        owned(pid, request)
        version = store.version(pid, vid)
        if version is None:
            raise HTTPException(404, "版本不存在")
        return version

    @app.post("/api/projects/{pid}/versions", status_code=201)
    def save_version(pid: str, data: VersionInput, request: Request):
        owned(pid, request)
        issues = check_html(data.html)
        if issues:
            raise HTTPException(422, "代码未通过检查：" + "；".join(issues))
        change(lambda: store.save_version(pid, data.html, data.summary.strip() or "手动编辑并保存代码", data.base_version_id))
        return owned(pid, request)

    @app.get("/api/projects/{pid}/backup")
    def backup_project(pid: str, request: Request):
        owned(pid, request)
        snapshot = store.snapshot(pid, request.state.owner)
        # Do not emit a backup that this version of the app cannot reimport.
        if len(snapshot["versions"]) > 100 or len(json.dumps(snapshot, ensure_ascii=False).encode()) > 20_000_000:
            raise HTTPException(413, "项目超过完整备份上限（100个版本、20MB），可先导出当前HTML。")
        return JSONResponse(snapshot, headers={"Content-Disposition": 'attachment; filename="atoms-project-backup.json"'})

    @app.post("/api/projects/{pid}/restore/{vid}")
    def restore(pid: str, vid: str, request: Request):
        owned(pid, request)
        try:
            found = store.restore(pid, vid)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not found:
            raise HTTPException(404, "版本不存在")
        return owned(pid, request)

    @app.get("/api/projects/{pid}/state")
    def get_state(pid: str, request: Request):
        owned(pid, request)
        return store.state(pid)

    @app.put("/api/projects/{pid}/state")
    def set_state(pid: str, data: StateInput, request: Request):
        owned(pid, request)
        # Python's JSON parser accepts NaN/Infinity, but browser JSON and our
        # responses do not. Reject them before persisting an unreadable state.
        try:
            encoded = json.dumps(data.value, ensure_ascii=False, allow_nan=False).encode()
        except ValueError as exc:
            raise HTTPException(422, "应用数据包含无效数值，请使用有限数值。") from exc
        if len(encoded) > 20000:
            raise HTTPException(413, "应用数据不能超过20KB")
        return change(lambda: store.state(pid, data.value))

    @app.get("/api/projects/{pid}/download")
    def download(pid: str, request: Request):
        project = owned(pid, request)
        if not project["html"]:
            raise HTTPException(404, "尚无可导出的代码")
        # Standalone exports use their own local storage, with no embedded key or API access.
        adapter = """<script>window.demoStorage={async load(){try{return JSON.parse(localStorage.getItem('atoms-export-state')||'{}')}catch(e){return {}}},async save(value){localStorage.setItem('atoms-export-state',JSON.stringify(value));return value}};</script>"""
        adapter = adapter.replace("atoms-export-state", "atoms-export-" + pid)
        html = inject_export_adapter(project["html"], adapter)
        return Response(html, media_type="text/html", headers={"Content-Disposition": 'attachment; filename="application.html"'})

    @app.post("/api/projects/{pid}/generate")
    async def generate(pid: str, data: GenerateInput, request: Request):
        project = owned(pid, request)
        if settings.mode == "deepseek" and not settings.api_key:
            raise HTTPException(503, "请先在项目 .env 中填写 DeepSeek API Key，并重启服务。")
        try:
            rid = store.start_run(pid, request.state.owner, data.prompt, settings.owner_limit, settings.global_limit)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OverflowError as exc:
            raise HTTPException(429, str(exc)) from exc

        async def stream():
            queue = asyncio.Queue()

            async def produce():
                try:
                    async for item in run_agent(model, data.prompt, project["html"], project["messages"]):
                        if item["type"] == "result":
                            summary = item["summary"]
                            if settings.mode == "mock":
                                summary = "【离线示例，未调用模型】" + summary
                            store.complete(pid, rid, item["html"], data.prompt, summary, settings.mode, item["title"])
                            item = {"type": "done", "project_id": pid}
                        await queue.put(item)
                except ModelError as exc:
                    store.fail(rid, str(exc))
                    await queue.put({"type": "error", "message": str(exc)})
                except asyncio.CancelledError:
                    store.fail(rid, "生成已停止，可以重新提交需求。", "cancelled")
                    raise
                except Exception as exc:
                    # Do not log model output, credentials, prompt bodies, or raw upstream errors.
                    logging.error("Generation failed (%s)", type(exc).__name__)
                    store.fail(rid, "生成失败，请重试。")
                    await queue.put({"type": "error", "message": "生成失败，请重试。"})
                finally:
                    await queue.put(None)

            task = asyncio.create_task(produce())
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=10)
                    except TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if item is None:
                        break
                    yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                store.fail(rid, "连接中断，请重新打开项目查看结果。", "interrupted")

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    return app


app = create_app()
