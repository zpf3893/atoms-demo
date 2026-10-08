"""Set the local DeepSeek key without echoing it or adding it to shell history."""
from getpass import getpass
from pathlib import Path
import os
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def save_key(path: Path, key: str) -> None:
    if not key or any(char in key for char in "\r\n\x00"):
        raise ValueError("Key 不能为空，也不能包含换行。")
    original = path.read_text(encoding="utf-8") if path.exists() else EXAMPLE.read_text(encoding="utf-8")
    line = "DEEPSEEK_API_KEY=" + key
    if re.search(r"(?m)^\s*DEEPSEEK_API_KEY\s*=.*$", original):
        updated = re.sub(r"(?m)^\s*DEEPSEEK_API_KEY\s*=.*$", lambda _: line, original, count=1)
    else:
        updated = original.rstrip("\n") + "\n" + line + "\n"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".env-", delete=False) as temp:
            temp_path = Path(temp.name)
            os.chmod(temp_path, 0o600)
            temp.write(updated)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if not sys.stdin.isatty():
        raise SystemExit("请在 VS Code 终端中交互运行此脚本，避免密钥出现在命令历史。")
    try:
        key = getpass("请输入 DeepSeek API Key（输入时不会显示）：").strip()
        save_key(ENV_FILE, key)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消，配置未修改。")
        raise SystemExit(1)
    except ValueError as error:
        print(error)
        raise SystemExit(1)
    print("已保存到本项目 .env。密钥内容未输出。")
