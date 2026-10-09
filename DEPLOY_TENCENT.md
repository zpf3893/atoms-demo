# 腾讯云轻量应用服务器部署（新手版）

这份步骤用于把完整的 Atoms Demo 放到腾讯云服务器。项目已有 Dockerfile；下面的脚本会在服务器构建并启动应用，SQLite 数据保存在 Docker 命名卷中。GitHub 仓库是 <https://github.com/zpf3893/atoms-demo>。

## 第一次部署

1. **先看免费试用**：打开[腾讯云免费体验馆](https://cloud.tencent.com/act/pro/free)，搜索「轻量应用服务器」。官方活动页列有个人认证的 **2 核 2 GB、1 个月**试用；实际资格、名额和可选地域以你登录后的领取页面为准。进入确认页后，核对**实付金额为 0 元**、地域能选**中国香港**、镜像能选**Docker CE 应用模板**、以及是否勾选自动续费；这几项都合适再领取。活动页未说明该试用一定支持香港和 Docker CE，不要只凭控制台横幅判断。

   如果免费试用只有中国内地地域，先不要把它当作本 Demo 的对外分享服务器：腾讯云文档写明，中国内地服务器对外提供网站或 App 服务需要备案，申请备案的实例时长须至少 3 个月。你可以用免费实例学习部署；要按本指南直接分享公网 IP，优先使用**中国香港**地域。免费名额不符合条件时，再到[轻量应用服务器控制台](https://console.cloud.tencent.com/lighthouse/instance/index)选择香港、Docker CE、约 2 核 2 GB、1 个月，按购买页核对价格和续费设置。香港服务器从内地访问可能有跨境网络延迟。
2. 在服务器列表找到新实例，记下**公网 IP**，单击实例卡片上的「登录」。以下命令都在打开的**服务器终端**执行，不是在你电脑的 VS Code 终端执行。
3. 先准备 Git 和 Python，再从 GitHub 下载代码：

   ```bash
   sudo apt update
   sudo apt install -y git python3 curl
   git clone https://github.com/zpf3893/atoms-demo.git
   cd atoms-demo
   python3 scripts/configure_key.py
   ```

   最后一条命令会提示输入 DeepSeek API Key，输入时不会显示字符。Key 只写在服务器的 `.env`，不要发到聊天或放进 GitHub。服务器也需要能访问 DeepSeek API；本机已经配置的 Key 不会自动带到服务器。
4. 启动应用：

   ```bash
   sudo bash scripts/deploy_lighthouse.sh
   curl -fsS http://127.0.0.1:8000/healthz
   ```

   第二条命令应返回健康检查结果。如果失败，用 `sudo docker logs --tail 100 atoms-demo` 看错误；日志里如有敏感信息，分享前先遮掉。
5. 回到腾讯云实例详情的「防火墙」页面，添加一条入站规则：协议 **TCP**、端口 **8000**、来源按演示范围设置。临时公开演示可选全部 IPv4 来源；只给少数人看时尽量限制来源 IP。随后在自己电脑浏览器打开 `http://你的公网IP:8000/`。能打开后，用手机流量再试一次，确认不是只在本机可用。

`http://公网IP:8000` 是便于初次验收的临时链接，**没有 HTTPS 加密**。用于正式公开分享时，建议先配置自己的域名、HTTPS 和反向代理，并把服务器 `.env` 中的 `COOKIE_SECURE` 改为 `true` 后重启。配置 HTTPS 时，把应用的 8000 端口收紧为仅本机访问或在防火墙关闭公网 8000；公开访问只放行 80/443。没有域名时，先用 IP 完成验收，不要把它当作长期正式网站。

当前 Demo 没有账户登录；拿到链接的人可以操作自己的浏览器会话，也可能消耗你的 DeepSeek 额度。部署脚本把调用次数限制为每个浏览器每天 3 次、整站每天 10 次；若只演示现成功能，可把服务器 `.env` 的 `LLM_MODE` 改为 `mock`，然后重启容器。云端数据库起初为空，需要展示本机已有项目时，可以在本机页面导出 JSON 备份，再到云端页面导入。不要上传含密钥的文件。

## 后续更新

先在电脑上改代码、验证、推送到 GitHub。在你电脑的项目终端运行：

```bash
git status --short
git add .
git diff --cached --stat
git commit -m "更新 Demo 功能"
git push origin main
```

然后登录**服务器终端**，进入之前下载的项目目录并重新部署：

```bash
cd ~/atoms-demo
git pull --ff-only origin main
sudo bash scripts/deploy_lighthouse.sh
curl -fsS http://127.0.0.1:8000/healthz
```

以后改服务器的 `.env` 也要重新运行部署脚本，Docker 容器才会读取新设置。脚本只更换容器，`atoms-demo-data` 命名卷仍保存 SQLite 数据；**不要运行** `docker volume rm atoms-demo-data`。重要更新前，在页面导出 JSON 备份，并下载到自己的电脑。服务器到期、被重装或删除时，单靠 Docker 卷不能代替异地备份。

## 常见问题

- 浏览器打不开：确认 `sudo docker ps` 里有 `atoms-demo`、本机健康检查成功、实例公网 IP 正确、腾讯云防火墙已放行 TCP 8000。若系统还启用了 UFW，也要允许该端口。
- 页面能打开但 AI 不生成：确认服务器 `.env` 中已填写有效 Key、DeepSeek 账户有额度、服务器可以访问 `api.deepseek.com`。不要把密钥或完整 `.env` 发给别人。
- `git pull` 提示本地修改冲突：只在服务器改 `.env`，项目代码在电脑上改并推到 GitHub；不要在服务器修改受 Git 管理的代码文件。`.env` 被 Git 忽略，正常更新不会覆盖它。

参考：[免费体验馆](https://cloud.tencent.com/act/pro/free)、[备案和实例时长限制](https://cloud.tencent.com/document/product/1207/44376/)、[腾讯云 Docker CE 模板](https://cloud.tencent.com/document/product/1207/60423)、[轻量应用服务器防火墙](https://cloud.tencent.com/document/product/1207/89060)、[地域和跨境网络说明](https://cloud.tencent.com/document/product/1207/50103)。
