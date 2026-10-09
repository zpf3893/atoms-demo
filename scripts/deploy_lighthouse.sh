#!/usr/bin/env bash
# 在腾讯云轻量应用服务器的 Docker CE 镜像上运行：sudo bash scripts/deploy_lighthouse.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
  echo "缺少 .env。请先运行 python3 scripts/configure_key.py。" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "找不到 Docker。请确认服务器使用了 Docker CE 应用模板。" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "无法连接 Docker。请在服务器上用 sudo bash scripts/deploy_lighthouse.sh 运行。" >&2
  exit 1
fi

# 先构建镜像；构建失败时，已运行的旧容器仍可继续服务。
docker build -t atoms-demo:local .
docker volume create atoms-demo-data >/dev/null

# 只替换应用容器，不删除存放 SQLite 数据的命名卷。
docker rm -f atoms-demo >/dev/null 2>&1 || true
docker run -d \
  --name atoms-demo \
  --restart unless-stopped \
  --env-file "$PROJECT_DIR/.env" \
  -e PORT=8000 \
  -e DATABASE_PATH=/data/demo.db \
  -e MAX_RUNS_PER_OWNER_PER_DAY=3 \
  -e MAX_RUNS_GLOBAL_PER_DAY=10 \
  -p 8000:8000 \
  --mount type=volume,source=atoms-demo-data,target=/data \
  atoms-demo:local

echo "容器已启动。先运行 curl -fsS http://127.0.0.1:8000/healthz 检查服务。"
echo "公网访问还需要在腾讯云控制台放行 TCP 8000。"
