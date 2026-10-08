"""Small explicit SQLite repository. Transactions keep versions and messages consistent."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Store:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    current_version TEXT, app_state TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS projects_owner ON projects(owner);
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    number INTEGER NOT NULL, html TEXT NOT NULL, prompt TEXT NOT NULL,
                    summary TEXT NOT NULL, mode TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id, number)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    owner TEXT NOT NULL, status TEXT NOT NULL, prompt TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT, error TEXT
                );
            """)
            # Additive migrations preserve every existing project and version.
            if "archived" not in {r["name"] for r in db.execute("PRAGMA table_info(projects)")}:
                db.execute("ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            if "source" not in {r["name"] for r in db.execute("PRAGMA table_info(versions)")}:
                db.execute("ALTER TABLE versions ADD COLUMN source TEXT NOT NULL DEFAULT 'generated'")
            # This MVP intentionally runs one Uvicorn worker. Recover interrupted jobs on restart.
            db.execute("UPDATE runs SET status='interrupted', error='服务重启，请重试', finished_at=? WHERE status='running'", (now(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, owner, title):
        pid, stamp = uuid.uuid4().hex, now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM projects WHERE owner=?", (owner,)).fetchone()[0] >= 100:
                raise OverflowError("当前会话最多创建100个项目（含归档项目）")
            db.execute("INSERT INTO projects(id,owner,title,created_at,updated_at) VALUES(?,?,?,?,?)", (pid, owner, title, stamp, stamp))
        return self.get(pid, owner)

    def count(self, owner):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM projects WHERE owner=?", (owner,)).fetchone()[0]

    def list(self, owner, archived=False):
        with self.connect() as db:
            rows = db.execute("SELECT p.id,p.title,p.created_at,p.updated_at,p.current_version,p.archived,v.source,v.mode,COALESCE(v.html,'') AS preview_html,(SELECT COUNT(*) FROM versions counts WHERE counts.project_id=p.id) AS version_count FROM projects p LEFT JOIN versions v ON v.id=p.current_version AND v.project_id=p.id WHERE p.owner=? AND p.archived=? ORDER BY p.updated_at DESC", (owner, int(archived)))
            return [dict(row, archived=bool(row["archived"])) for row in rows]

    def get(self, pid, owner):
        with self.connect() as db:
            row = db.execute("SELECT id,title,created_at,updated_at,current_version,app_state,archived FROM projects WHERE id=? AND owner=?", (pid, owner)).fetchone()
            if row is None:
                return None
            project = dict(row)
            project.pop("app_state")
            project["archived"] = bool(project["archived"])
            project["messages"] = [dict(r) for r in db.execute("SELECT role,content,created_at FROM messages WHERE project_id=? ORDER BY id", (pid,))]
            project["versions"] = [dict(r) for r in db.execute("SELECT id,number,prompt,summary,mode,created_at,source FROM versions WHERE project_id=? ORDER BY number DESC", (pid,))]
            version = db.execute("SELECT html,source,mode FROM versions WHERE id=? AND project_id=?", (project["current_version"], pid)).fetchone()
            project["html"] = version["html"] if version else ""
            project["source"] = version["source"] if version else None
            project["mode"] = version["mode"] if version else None
            run = db.execute("SELECT status,prompt,error FROM runs WHERE project_id=? ORDER BY started_at DESC LIMIT 1", (pid,)).fetchone()
            project["last_run"] = dict(run) if run else None
            return project

    def start_run(self, pid, owner, prompt, owner_limit, global_limit):
        """Atomic quota + busy check: two requests cannot both consume the last slot."""
        rid, stamp = uuid.uuid4().hex, now()
        day = stamp[:10]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._editable(db, pid)
            if db.execute("SELECT 1 FROM runs WHERE owner=? AND status='running'", (owner,)).fetchone():
                raise ValueError("已有任务正在生成，请等待完成或停止后重试。")
            count = db.execute("SELECT COUNT(*) FROM runs WHERE owner=? AND substr(started_at,1,10)=?", (owner, day)).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM runs WHERE substr(started_at,1,10)=?", (day,)).fetchone()[0]
            if count >= owner_limit or total >= global_limit:
                raise OverflowError("今天的生成次数已达到上限，请稍后再试。")
            db.execute("INSERT INTO runs(id,project_id,owner,status,prompt,started_at) VALUES(?,?,?,'running',?,?)", (rid, pid, owner, prompt, stamp))
            db.execute("INSERT INTO messages(project_id,role,content,created_at) VALUES(?,'user',?,?)", (pid, prompt, stamp))
        return rid

    def complete(self, pid, rid, html, prompt, summary, mode, title):
        vid, stamp = uuid.uuid4().hex, now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            number = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM versions WHERE project_id=?", (pid,)).fetchone()[0]
            db.execute("INSERT INTO versions(id,project_id,number,html,prompt,summary,mode,created_at,source) VALUES(?,?,?,?,?,?,?,?,?)", (vid, pid, number, html, prompt, summary, mode, stamp, "generated"))
            db.execute("UPDATE projects SET current_version=?, updated_at=?, title=CASE WHEN current_version IS NULL AND title IN ('新项目','未命名项目','未命名应用') THEN ? ELSE title END WHERE id=?", (vid, stamp, title[:60], pid))
            db.execute("INSERT INTO messages(project_id,role,content,created_at) VALUES(?,'assistant',?,?)", (pid, summary, stamp))
            db.execute("UPDATE runs SET status='completed',finished_at=? WHERE id=?", (stamp, rid))
        return vid

    def fail(self, rid, message, status="failed"):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?,error=?,finished_at=? WHERE id=? AND status='running'", (status, message, now(), rid))

    def restore(self, pid, vid):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._editable(db, pid)
            self._idle(db, pid)
            if not db.execute("SELECT 1 FROM versions WHERE id=? AND project_id=?", (vid, pid)).fetchone():
                return False
            db.execute("UPDATE projects SET current_version=?,updated_at=? WHERE id=?", (vid, now(), pid))
            return True

    def state(self, pid, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute("BEGIN IMMEDIATE")
                self._editable(db, pid)
                db.execute("UPDATE projects SET app_state=? WHERE id=?", (json.dumps(value, ensure_ascii=False), pid))
            return json.loads(db.execute("SELECT app_state FROM projects WHERE id=?", (pid,)).fetchone()[0])


    @staticmethod
    def _idle(db, pid):
        if db.execute("SELECT 1 FROM runs WHERE project_id=? AND status='running'", (pid,)).fetchone():
            raise ValueError("项目正在生成，请完成或停止后再操作。")

    @staticmethod
    def _editable(db, pid):
        row = db.execute("SELECT archived FROM projects WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise ValueError("项目不存在")
        if row["archived"]:
            raise ValueError("项目已归档，请先恢复项目。")

    def rename(self, pid, title):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._editable(db, pid)
            self._idle(db, pid)
            db.execute("UPDATE projects SET title=?,updated_at=? WHERE id=?", (title, now(), pid))

    def archive(self, pid, archived):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._idle(db, pid)
            db.execute("UPDATE projects SET archived=?,updated_at=? WHERE id=?", (int(archived), now(), pid))

    def version(self, pid, vid):
        with self.connect() as db:
            row = db.execute("SELECT id,number,html,prompt,summary,mode,source,created_at FROM versions WHERE project_id=? AND id=?", (pid, vid)).fetchone()
            return dict(row) if row else None

    def save_version(self, pid, html, summary, base_version_id, source="manual", mode="manual", prompt="手动编辑代码"):
        vid, stamp = uuid.uuid4().hex, now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._editable(db, pid)
            self._idle(db, pid)
            current = db.execute("SELECT current_version FROM projects WHERE id=?", (pid,)).fetchone()[0]
            if current != base_version_id:
                raise ValueError("当前版本已经变化，请重新加载最新代码后再保存。")
            number = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM versions WHERE project_id=?", (pid,)).fetchone()[0]
            db.execute("INSERT INTO versions(id,project_id,number,html,prompt,summary,mode,created_at,source) VALUES(?,?,?,?,?,?,?,?,?)", (vid, pid, number, html, prompt, summary, mode, stamp, source))
            db.execute("UPDATE projects SET current_version=?,updated_at=? WHERE id=?", (vid, stamp, pid))
            db.execute("INSERT INTO messages(project_id,role,content,created_at) VALUES(?,'assistant',?,?)", (pid, summary, stamp))
        return vid

    def snapshot(self, pid, owner):
        """An explicit allowlist prevents owner capabilities/configuration from export."""
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT title,current_version,app_state FROM projects WHERE id=? AND owner=?", (pid, owner)).fetchone()
            if row is None:
                return None
            versions = [dict(r) for r in db.execute("SELECT id,number,html,prompt,summary,mode,source,created_at FROM versions WHERE project_id=? ORDER BY number", (pid,))]
            html = next((v["html"] for v in versions if v["id"] == row["current_version"]), "")
            return {"format": "atoms-demo-backup", "format_version": 1, "title": row["title"], "current_version": row["current_version"], "html": html, "versions": versions, "app_state": json.loads(row["app_state"])}

    def import_snapshot(self, owner, snapshot, title=None, notice="已恢复项目代码、历史版本和应用数据。"):
        """Import only validated data, assigning fresh IDs and an independent state."""
        pid, stamp = uuid.uuid4().hex, now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM projects WHERE owner=?", (owner,)).fetchone()[0] >= 100:
                raise OverflowError("当前会话最多创建100个项目（含归档项目）")
            db.execute("INSERT INTO projects(id,owner,title,created_at,updated_at,app_state) VALUES(?,?,?,?,?,?)", (pid, owner, title or snapshot["title"], stamp, stamp, json.dumps(snapshot["app_state"], ensure_ascii=False)))
            ids = {}
            for number, version in enumerate(snapshot["versions"], 1):
                vid = uuid.uuid4().hex
                ids[version["id"]] = vid
                db.execute("INSERT INTO versions(id,project_id,number,html,prompt,summary,mode,created_at,source) VALUES(?,?,?,?,?,?,?,?,?)", (vid, pid, number, version["html"], version["prompt"], version["summary"], version["mode"], version["created_at"], version["source"]))
            if snapshot["current_version"]:
                db.execute("UPDATE projects SET current_version=? WHERE id=?", (ids[snapshot["current_version"]], pid))
            db.execute("INSERT INTO messages(project_id,role,content,created_at) VALUES(?,'assistant',?,?)", (pid, notice, stamp))
        return self.get(pid, owner)

    def duplicate(self, pid, owner):
        with self.connect() as db:
            self._idle(db, pid)
        snapshot = self.snapshot(pid, owner)
        return self.import_snapshot(owner, snapshot, title=(snapshot["title"][:55] + " · 副本"), notice="已复制项目代码、历史版本和应用数据。副本中的修改将独立保存。")
