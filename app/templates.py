"""Small, useful starter apps that work without a model or external services.

The HTML files are ordinary, self-contained documents. The workbench injects
``window.demoStorage`` so each project's application data stays separate.
"""
from pathlib import Path


TEMPLATE_DIR = Path(__file__).with_name("templates")

_CATALOG = (
    {
        "id": "tasks",
        "title": "今日清单",
        "description": "安排待办与截止日期，筛选进度，把每一天过得有条理。",
        "category": "效率工具",
        "accent": "#7178c7",
        "prompt": "创建一个中文待办应用，支持新增事项、截止日期、完成状态、筛选和删除；保存所有数据。",
    },
    {
        "id": "expenses",
        "title": "生活账本",
        "description": "记录每笔日常支出，按月份与分类查看，了解钱花在哪里。",
        "category": "生活管理",
        "accent": "#649787",
        "prompt": "创建一个个人支出账本，支持金额、日期、分类、备注、月度合计、分类筛选和删除；保存所有数据。",
    },
    {
        "id": "focus",
        "title": "一刻专注",
        "description": "设定专注与休息时长，暂停或继续计时，积累自己的专注记录。",
        "category": "效率工具",
        "accent": "#c18767",
        "prompt": "创建一个番茄钟，支持自定义专注与休息时长、开始、暂停、继续、重置和完成记录；刷新后恢复进度。",
    },
)


def get_template(template_id: str) -> dict:
    """Return an independent metadata dictionary including executable HTML.

    Unknown IDs raise KeyError. The path comes from the fixed catalog, never
    from unchecked input, so callers can safely expose this through an API.
    """
    item = next((item for item in _CATALOG if item["id"] == template_id), None)
    if item is None:
        raise KeyError(template_id)
    html = (TEMPLATE_DIR / f"{item['id']}.html").read_text(encoding="utf-8")
    return {**item, "html": html}


def list_templates() -> list[dict]:
    """Public catalog, with static HTML suitable for script-disabled previews."""
    return [get_template(item["id"]) for item in _CATALOG]
