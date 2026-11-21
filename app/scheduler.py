"""Runs each agent at its time. If the laptop was asleep at 9:00 and wakes at 11:00, today's run still happens once."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from . import agents, db, settings_store

log = logging.getLogger("scheduler")


def _ran_today(agent: str) -> bool:
    """A scheduled attempt today (even a failed one, so we never retry in a loop), or a successful manual run."""
    today = datetime.now().astimezone().date().isoformat()
    return db.one("""SELECT id FROM runs WHERE agent = ? AND substr(started_at, 1, 10) = ?
                     AND (trigger = 'schedule' OR status IN ('done', 'running'))""", (agent, today)) is not None


def due_agents(now: datetime | None = None) -> list[str]:
    s = settings_store.get_all()
    if not s["agents_enabled"]:
        return []
    now = now or datetime.now()
    hm = now.strftime("%H:%M")
    due = []
    for agent, meta in agents.AGENTS.items():
        short = meta["short"]
        if not s[f"enabled_{short}"]:
            continue
        if agent == "weekly_review" and now.weekday() != s["day_wr"]:
            continue
        if hm >= s[f"time_{short}"] and not _ran_today(agent) and not agents.is_running(agent):
            due.append(agent)
    return due


def next_run_label(agent: str) -> str:
    s = settings_store.get_all()
    short = agents.AGENTS[agent]["short"]
    if not s["agents_enabled"] or not s[f"enabled_{short}"]:
        return "متوقف"
    days = ["الاتنين", "التلات", "الأربع", "الخميس", "الجمعة", "السبت", "الحد"]
    if agent == "weekly_review":
        return f"كل {days[s['day_wr']]} {s['time_wr']}"
    return f"كل يوم {s[f'time_{short}']}"


async def loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            for agent in due_agents():
                # sequential on purpose: media buyer first, so follow-up can read its messages
                await agents.run(agent, trigger="schedule")
        except Exception:  # keep the loop alive no matter what
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
