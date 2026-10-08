# 接入 DeepSeek、上传 GitHub、分享在线 Demo

这个项目是 FastAPI + SQLite 应用。源码已上传至 <https://github.com/zpf3893/atoms-demo>。`http://127.0.0.1:8765` 只在你的电脑上可访问；要分享完整工作台，需要把后端一起部署到公网。上传 GitHub 只是分享源码，不会自动产生可操作的 Demo 链接。

## 1. 在本机启用 DeepSeek

用 VS Code 打开本项目根目录，编辑未纳入 Git 的 `.env`，填写自己的 Key：

```dotenv
DEEPSEEK_API_KEY=在这里填写你自己的密钥
DEEPSEEK_MODEL=deepseek-flash
LLM_MODE=deepseek
```

若找不到 `.env` 或保存后服务仍显示没有 Key，可在项目终端运行 `python3 scripts/configure_key.py`，它会隐藏输入并写入正确文件。保存后，在项目目录启动：

```bash
bash run.sh
```

打开 `http://127.0.0.1:8765/`，选一个模板或创建空白项目，再提交一条简单需求，确认右侧出现新版本。当前本机 8765 服务已在 DeepSeek 模式完成一次真实联调；重新运行前先停止占用该端口的旧服务。Key 必须有效、账户有可用额度、服务器能连接 DeepSeek。一个需求通常发起两次模型请求，修复时可能有第三次；模型调用按 DeepSeek 账户计费。

不要把 Key 发到聊天、写进页面代码或提交到 GitHub。`.env` 已在 `.gitignore` 中；`.env.example` 只含空值和示例配置。只看到“DeepSeek 已配置”说明服务读到了 Key；每个新部署环境仍应做一次真实生成验收。

## 2. 上传源码到 GitHub

本项目目录已准备为独立 Git 仓库，并有本地初始提交。提交使用通用的 `Atoms Demo` 署名；如需在 GitHub 显示你的个人署名，可在首次推送前设置本仓库的 `git config user.name` 和 `git config user.email`，再执行 `git commit --amend --no-edit --reset-author`。如果你还没登录 GitHub，先在浏览器登录。访问 <https://github.com/new>，创建名为 `atoms-demo` 的**空仓库**；不要勾选自动添加 README、`.gitignore` 或许可证。想让任何人看源码选 Public；只给指定人员看选 Private，然后邀请协作者。

在本项目目录运行下面两行，把 `你的用户名` 换成 GitHub 用户名：

```bash
git remote add origin https://github.com/你的用户名/atoms-demo.git
git push -u origin main
```

GitHub 会引导你完成身份验证。推送成功后，源码链接是 `https://github.com/你的用户名/atoms-demo`。此处上传的是源码，不包含本机 `.env`、数据库、虚拟环境。之后改代码可以用 `git add . && git commit -m "说明修改内容" && git push` 更新。推送前可用 `git status --short` 检查新增文件。

## 3. 在 Render 部署完整工作台

1. 在 Render 创建 **Web Service**，授权连接你的 GitHub 账号并选择 `zpf3893/atoms-demo`，然后选择 `main` 分支和 **Python 3** 运行环境。Root Directory 留空。要让后续推送自动部署，应使用 GitHub 账号连接方式，保持 Auto-Deploy 为 **On Commit**；不要只用“Public Git Repository URL”方式连接。
2. Build Command：`pip install -r requirements.txt`。
3. Start Command：`uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`。
4. Health Check Path：`/healthz`。
5. 因为项目使用 SQLite，选支持持久磁盘的**付费 Web Service**，在 Advanced 或服务的 Disk 页面挂载到 `/var/data`，并设置 `DATABASE_PATH=/var/data/demo.db`。Render 免费 Web Service 不支持持久磁盘，休眠、重启或重新部署会丢失本地数据库。先根据 Render 页面确认当时的计划价格。
6. 在 Render 的 **Environment** 页面分别设置：`LLM_MODE=deepseek`、`DEEPSEEK_API_KEY=你的密钥`、`DEEPSEEK_MODEL=deepseek-flash`、`COOKIE_SECURE=true`、`DATABASE_PATH=/var/data/demo.db`。建议公开演示时将 `MAX_RUNS_PER_OWNER_PER_DAY` 设为 `3`、`MAX_RUNS_GLOBAL_PER_DAY` 设为 `10`，再按实际需求调整。
7. 部署成功后，Render 会给出 `https://你的服务名.onrender.com` 形式的 HTTPS 地址。用另一台设备打开，创建项目并刷新页面，确认版本与业务数据仍在；再验证一次真实生成和“停止”按钮。

云端会创建一份新的 SQLite 数据库，不会自动带上电脑里已有的项目。需要把本机成果展示到云端时，先在本机项目页面导出 JSON 备份，再从云端页面导入；导入后确认代码版本和业务数据。云端每个浏览器通过自己的 Cookie 区分项目，没有账号和跨设备同步。

当前项目没有账号登录。知道链接的人都能使用工作台，启用 API 后也可能消耗你的模型额度。只需要展示已经做好的页面时，可先用 `LLM_MODE=mock` 分享工作台模板，或导出单个应用的 HTML 页面；需要公开开放 AI 生成时，应保持较低的每日次数上限并监控 DeepSeek 账户用量。

## 4. 后续升级迭代

1. 在本机修改代码，先运行与改动相关的测试，并在本机页面操作验证。改动数据结构前先从云端项目页面导出 JSON 备份。
2. 在项目目录检查待上传文件并推送到 GitHub：

   ```bash
   git status --short
   git add .
   git diff --cached --stat
   git commit -m "描述这次修改"
   git push origin main
   ```

   上传前确认没有密钥、数据库或个人备份；`.env` 已被 Git 忽略，真实 Key 只保存在本机和 Render 环境变量中。
3. 如果 Render 的 Auto-Deploy 是 **On Commit**，每次推送到 `main` 就会自动构建和部署。到 Render 服务的 Deploys 页面看结果；若自动部署被关闭，可点 **Manual Deploy → Deploy latest commit**。只改 Render 环境变量时，在 Environment 页面保存并选择部署，让新值生效。
4. 持久磁盘挂载路径和 `DATABASE_PATH` 不变时，重新部署会保留云端 SQLite 数据。挂载磁盘的服务在更新时可能短暂中断；部署完成后检查 `/healthz`、首页、已有项目和一次预览操作。云端没有账号同步，不同浏览器看不到彼此的项目。

## 官方参考

- [DeepSeek 模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)
- [GitHub 上传本地项目](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)
- [Render FastAPI 部署](https://render.com/docs/deploy-fastapi)、[Web Service 设置](https://render.com/docs/web-services)
- [Render 持久磁盘](https://render.com/docs/disks)、[免费服务限制](https://render.com/docs/free)、[环境变量](https://render.com/docs/configure-environment-variables)
