"""Read configuration once when the process starts; never return the key to the UI."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    mode: str = "deepseek"
    database: str = str(ROOT / "data/demo.db")
    timeout: int = 120
    owner_limit: int = 20
    global_limit: int = 100
    cookie_secure: bool = False

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        database = Path(os.getenv("DATABASE_PATH", "data/demo.db"))
        if not database.is_absolute():
            database = ROOT / database
        mode = os.getenv("LLM_MODE", "deepseek").strip().lower()
        if mode not in {"mock", "deepseek"}:
            raise ValueError("LLM_MODE must be deepseek or mock")
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            mode=mode, database=str(database),
            timeout=int(os.getenv("MODEL_TIMEOUT_SECONDS", "120")),
            owner_limit=int(os.getenv("MAX_RUNS_PER_OWNER_PER_DAY", "20")),
            global_limit=int(os.getenv("MAX_RUNS_GLOBAL_PER_DAY", "100")),
            cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        )
