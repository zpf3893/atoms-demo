# 接入 DeepSeek、上传 GitHub、分享在线 Demo

这个项目是 FastAPI + SQLite 应用。`http://127.0.0.1:8765` 只在你的电脑上可访问；要分享完整工作台，需要把后端一起部署到公网。上传 GitHub 只是分享源码，不会自动产生可操作的 Demo 链接。

## 1. 在本机启用 DeepSeek

用 VS Code 打开本项目根目录，编辑未纳入 Git 的 `.env`，填写自己的 Key：

```dotenv
DEEPSEEK_API_KEY=在这里填写你自己的密钥
DEEPSEEK_MODEL=deepseek-flash
LLM_MODE=deepseek
```

保存后，在项目目录启动：

```bash
PORT=8766 bash run.sh
```

打开 `http://127.0.0.1:8766/`，选一个模板或创建空白项目，再提交一条简单需求，确认右侧出现新版本。当前 8765 端口是离线演示服务；使用 8766 可以同时保留它。端口 8766 默认使用项目 `data/demo.db`，它与当前离线预览的数据文件分开。Key 必须有效、账户有可用额度、服务器能连接 DeepSeek。一个需求通常发起两次模型请求，修复时可能有第三次；模型调用按 DeepSeek 账户计费。

不要把 Key 发到聊天、写进页面代码或提交到 GitHub。`.env` 已在 `.gitignore` 中；`.env.example` 只含空值和示例配置。只看到“DeepSeek 已配置”说明服务读到了 Key，仍应做一次真实生成验收。

## 2. 上传源码到 GitHub

本项目目录已准备为独立 Git 仓库，并有本地初始提交。提交使用通用的 `Atoms Demo` 署名；如需在 GitHub 显示你的个人署名，可在首次推送前设置本仓库的 `git config user.name` 和 `git config user.email`，再执行 `git commit --amend --no-edit --reset-author`。如果你还没登录 GitHub，先在浏览器登录。访问 <https://github.com/new>，创建名为 `atoms-demo` 的**空仓库**；不要勾选自动添加 README、`.gitignore` 或许可证。想让任何人看源码选 Public；只给指定人员看选 Private，然后邀请协作者。

在本项目目录运行下面两行，把 `你的用户名` 换成 GitHub 用户名：

```bash
git remote add origin https://github.com/你的用户名/atoms-demo.git
git push -u origin main
```

GitHub 会引导你完成身份验证。推送成功后，源码链接是 `https://github.com/你的用户名/atoms-demo`。此处上传的是源码，不包含本机 `.env`、数据库、虚拟环境。之后改代码可以用 `git add . && git commit -m "说明修改内容" && git push` 更新。推送前可用 `git status --short` 检查新增文件。

## 3. 在 Render 部署完整工作台

1. 在 Render 创建 **Web Service**，连接上面的 GitHub 仓库，选择 `main` 分支和 **Python 3** 运行环境。项目根目录就是仓库根目录。
2. Build Command：`pip install -r requirements.txt`。
3. Start Command：`uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`。
4. Health Check Path：`/healthz`。
5. 因为项目使用 SQLite，选支持持久磁盘的**付费 Web Service**，挂载路径 `/var/data`，并设置 `DATABASE_PATH=/var/data/demo.db`。Render 免费 Web Service 不支持持久磁盘，重启或重新部署可能丢失项目数据。先根据 Render 页面确认当时的计划价格。
6. 在 Render 的 **Environment** 页面分别设置：`LLM_MODE=deepseek`、`DEEPSEEK_API_KEY=你的密钥`、`DEEPSEEK_MODEL=deepseek-flash`、`COOKIE_SECURE=true`、`DATABASE_PATH=/var/data/demo.db`。建议公开演示时将 `MAX_RUNS_PER_OWNER_PER_DAY` 设为 `3`、`MAX_RUNS_GLOBAL_PER_DAY` 设为 `10`，再按实际需求调整。
7. 部署成功后，Render 会给出 `https://你的服务名.onrender.com` 形式的 HTTPS 地址。用另一台设备打开，创建项目并刷新页面，确认版本与业务数据仍在；再验证一次真实生成和“停止”按钮。

当前项目没有账号登录。知道链接的人都能使用工作台，启用 API 后也可能消耗你的模型额度。只需要展示已经做好的页面时，可先用 `LLM_MODE=mock` 分享工作台模板，或导出单个应用的 HTML 页面；需要公开开放 AI 生成时，应保持较低的每日次数上限并监控 DeepSeek 账户用量。

## 官方参考

- [DeepSeek 模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)
- [GitHub 上传本地项目](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)
- [Render FastAPI 部署](https://render.com/docs/deploy-fastapi)、[Web Service 设置](https://render.com/docs/web-services)
- [Render 持久磁盘](https://render.com/docs/disks)、[免费服务限制](https://render.com/docs/free)、[环境变量](https://render.com/docs/configure-environment-variables)
