"""Deliberately limited offline fixture. Never silently substituted for a real model."""
import asyncio
import json
from pathlib import Path


class OfflineModel:
    async def complete(self, system, user, max_tokens=8000):
        await asyncio.sleep(0.3)
        if max_tokens == 700:
            return {"title": "我的待办清单", "steps": ["载入离线待办示例", "提供新增、完成、筛选和删除", "保存数据并预览页面"]}
        context = json.loads(user.split("\n计划：")[0])
        html = (Path(__file__).parent / "example.html").read_text()
        dark = "深色" in context.get("request", "") or 'data-theme="dark"' in context.get("current_html", "")
        if "浅色" in context.get("request", ""):
            dark = False
        if dark:
            html = html.replace('data-theme="light"', 'data-theme="dark"')
        return {"html": html, "summary": "已载入待办示例，支持新增、完成、筛选、删除与数据保存。离线模式仅演示这个模板和深浅色切换；自由需求请接入 DeepSeek。"}
