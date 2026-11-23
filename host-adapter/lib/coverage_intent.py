"""Detect prompts that need complete (exhaustive) coverage instead of Top-K recall.

Semantic recall/research return a bounded candidate set, so a question such as
"梳理我这个月的工作" answered from them silently looks complete while it is not.
This module recognises time-window and inventory questions and resolves relative
time expressions to concrete dates in the user's time zone (Asia/Shanghai), so
the hook can route the Agent to ``ep_exhaustive_search`` first.

Pure functions, no I/O; safe to call on every prompt.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
           "十一": 11, "十二": 12, "半": 0}

INVENTORY = re.compile(r"梳理|盘点|汇总|全部|所有|全量|查全|列出|罗列|回顾|复盘|时间线|做过哪些|做了哪些|做了什么|干了什么|都做了|都聊了|有哪些(工作|项目|会话|内容|事)|工作情况|周报|月报|日报")
HISTORY = re.compile(r"做|工作|项目|会话|对话|聊|讨论|处理|任务|进展|记录|内容|客户|方案|历史|写了|改了|修了")
# "Which conversation was X in / where did I put X": the answer is one or more
# sessions, and Top-K recall routinely returns only the most similar one.
LOCATOR = re.compile(
    r"(哪个|哪一个|哪次|哪些|哪条)(对话|会话|聊天|窗口|线程|thread|session)"
    r"|在哪(个|一个)?(对话|会话|窗口|宿主|agent|Agent)"
    r"|(之前|以前|上次|原来|先前|早先)[^。？?!！\n]{0,24}(在哪|放哪|放在哪|存哪|哪里|哪儿|哪个|哪次)"
    r"|(什么时候|哪天)[^。？?!！\n]{0,8}(聊|做|说|讨论|提|写|改)过"
    r"|找(一下|找|到)?(之前|以前|上次)"
)


def _num(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _CN_NUM.get(token)


def _day_bounds(start: dt.date, end: dt.date) -> tuple[str, str]:
    return start.isoformat(), end.isoformat()


def _month_end(year: int, month: int) -> dt.date:
    nxt = dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return nxt - dt.timedelta(days=1)


def resolve_window(text: str, now: dt.datetime | None = None) -> dict[str, Any] | None:
    """Return {start, end, expression} for the first time expression found, else None."""
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    today = now.date()
    t = text or ""

    # Explicit ranges: 2026-11-15 到 2026-11-21 / 11-15到11-21 / 11月15日到21日
    m = re.search(r"(?:(\d{4})[-/.年])?(\d{1,2})[-/.月](\d{1,2})日?\s*(?:到|至|~|～|—|-|–)\s*(?:(\d{4})[-/.年])?(?:(\d{1,2})[-/.月])?(\d{1,2})日?", t)
    if m:
        y1 = int(m.group(1) or today.year); mo1 = int(m.group(2)); d1 = int(m.group(3))
        y2 = int(m.group(4) or y1); mo2 = int(m.group(5) or mo1); d2 = int(m.group(6))
        try:
            s, e = dt.date(y1, mo1, d1), dt.date(y2, mo2, d2)
            if s <= e:
                return {"start": s.isoformat(), "end": e.isoformat(), "expression": m.group(0)}
        except ValueError:
            pass
    # Month ranges: 8–10月 / 8月到10月
    m = re.search(r"(\d{1,2})\s*月?\s*(?:到|至|~|～|—|-|–)\s*(\d{1,2})\s*月", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= b <= 12:
            year = today.year if b <= today.month else today.year - 1
            s, e = dt.date(year, a, 1), _month_end(year, b)
            return {"start": s.isoformat(), "end": e.isoformat(), "expression": m.group(0)}
    # Single explicit date
    m = re.search(r"(?:(\d{4})[-/.年])?(\d{1,2})月(\d{1,2})[日号]|(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        try:
            if m.group(4):
                d = dt.date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
            else:
                d = dt.date(int(m.group(1) or today.year), int(m.group(2)), int(m.group(3)))
            return {"start": d.isoformat(), "end": d.isoformat(), "expression": m.group(0)}
        except ValueError:
            pass
    # Single month: 10月份 / 10月的 (avoid matching "10月15日" handled above)
    m = re.search(r"(?:(\d{4})年)?(\d{1,2}|十一|十二|[一二三四五六七八九十])月(?:份|里|的|内|中)", t)
    if m:
        mo = _num(m.group(2)) or 0
        if 1 <= mo <= 12:
            year = int(m.group(1)) if m.group(1) else (today.year if mo <= today.month else today.year - 1)
            return {"start": dt.date(year, mo, 1).isoformat(), "end": _month_end(year, mo).isoformat(), "expression": m.group(0)}

    rel = [
        (r"今天|今日", lambda: (today, today)),
        (r"昨天|昨日", lambda: (today - dt.timedelta(days=1),) * 2),
        (r"前天", lambda: (today - dt.timedelta(days=2),) * 2),
        (r"本周|这周|这一周|这个星期|本星期", lambda: (today - dt.timedelta(days=today.weekday()), today)),
        (r"上周|上一周|上个星期|上星期", lambda: (today - dt.timedelta(days=today.weekday() + 7), today - dt.timedelta(days=today.weekday() + 1))),
        (r"本月|这个月|这月", lambda: (today.replace(day=1), today)),
        (r"上个月|上月", lambda: ((today.replace(day=1) - dt.timedelta(days=1)).replace(day=1), today.replace(day=1) - dt.timedelta(days=1))),
        (r"今年|本年", lambda: (dt.date(today.year, 1, 1), today)),
        (r"去年", lambda: (dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31))),
    ]
    m = (re.search(r"(?:最近|近|过去|最新)\s*(\d+|[一两二三四五六七八九十]|半)\s*(?:个)?\s*(天|日|周|星期|礼拜|月)", t)
         or re.search(r"(\d+|[一两二三四五六七八九十]|半)\s*个\s*(月|星期|礼拜)(?:的|内|以来|里)?", t)
         or re.search(r"(\d+|[一两二三四五六七八九十])\s*(天|周)(?:的|内|以来|里)", t))
    if m:
        n = _num(m.group(1))
        unit = m.group(2)
        days = (n or 0) * (1 if unit in "天日" else 7 if unit in ("周", "星期", "礼拜") else 30)
        if m.group(1) == "半" and unit == "月":
            days = 15
        if days > 0:
            return {"start": (today - dt.timedelta(days=days - 1)).isoformat(), "end": today.isoformat(), "expression": m.group(0)}
    for pattern, fn in rel:
        mm = re.search(pattern, t)
        if mm:
            s, e = fn()
            return {"start": s.isoformat(), "end": e.isoformat(), "expression": mm.group(0)}
    if re.search(r"最近|近期|这段时间|这阵子", t):
        return {"start": (today - dt.timedelta(days=13)).isoformat(), "end": today.isoformat(), "expression": "最近（默认 14 天）"}
    return None


def detect(text: str, now: dt.datetime | None = None) -> dict[str, Any] | None:
    """Return a coverage contract when the prompt needs complete retrieval."""
    t = " ".join(str(text or "").split())
    if not t:
        return None
    inventory = bool(INVENTORY.search(t))
    history = bool(HISTORY.search(t))
    locator = bool(LOCATOR.search(t))
    window = resolve_window(t, now)
    if not ((window and (inventory or history)) or (inventory and history) or locator):
        return None
    return {
        "route": "exhaustive",
        "window": window,
        "inventory": inventory,
        "locator": locator,
        "time_zone": "Asia/Shanghai",
    }


def instruction(contract: dict[str, Any]) -> str:
    w = contract.get("window") or {}
    args = {"group_by": "project"}
    if contract.get("locator"):
        # Locating a past conversation: list every matching session, not the best match.
        args = {"group_by": "session", "keywords": ["<对象名>", "<别名>"], "sections": ["work_ledger", "summaries"], "group_limit": 40}
        if w:
            args.update({"start_date": w["start"], "end_date": w["end"]})
    elif w:
        # A time-window inventory reads best as one titled row per session.
        args = {"group_by": "session", "start_date": w["start"], "end_date": w["end"], "group_limit": 60}
    import json
    window_text = (f"时间段“{w['expression']}”按北京时间解析为 {w['start']} 至 {w['end']}。" if w else "")
    return (
        "这是需要查全的时间段/盘点类问题：必须先调用 ep_exhaustive_search（建议参数 "
        + json.dumps(args, ensure_ascii=False)
        + "，可再按 host、week 或 project 分组），以 total_matched 与 groups 作为覆盖范围；items 不够时按 next_offset 翻页，或用 sections 只取需要的分区。"
        + window_text
        + ("这是定位历史会话的问题：答案可能分布在多个宿主、多个会话里，把所有命中的会话按时间列出（宿主、日期、标题），不要只给语义最相近的一个；"
           if contract.get("locator") else "")
        + ("" if w else "没有时间段时，把问题里的对象名、客户名、项目名及其别名作为 keywords 传入。")
        + "之后再用 user_recall/user_research 补细节，用 read_source 核对关键原文。user_recall/user_research 是语义 Top-K，不能单独作为“已查全”的依据；回答时写明覆盖的宿主、总数和未读页。"
    )
