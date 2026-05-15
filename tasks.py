"""Task list loader and helpers for tasks.yaml.

Single source of truth for operational tasks (non-training-session items).
Consumed by the FastAPI dashboard (/api/tasks) and calendar_export.py
(all-day events alongside training sessions).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent
TASKS_PATH = ROOT / "tasks.yaml"


CATEGORY_GLYPH = {
    "setup": "⚙",
    "benchmark": "⌖",
    "logistics": "✈",
    "gear": "❄",
    "recovery": "✚",
}


@dataclass
class Task:
    id: str
    title: str
    due: date | None
    category: str
    context: str
    done: bool
    done_on: date | None

    @property
    def glyph(self) -> str:
        return CATEGORY_GLYPH.get(self.category, "·")

    def days_until(self, today: date) -> int | None:
        if self.due is None:
            return None
        return (self.due - today).days

    def urgency(self, today: date) -> str:
        """red (<= 0 / overdue), amber (<= 7), grey otherwise."""
        if self.done:
            return "done"
        d = self.days_until(today)
        if d is None:
            return "grey"
        if d < 0:
            return "overdue"
        if d <= 7:
            return "red"
        if d <= 30:
            return "amber"
        return "grey"


def _coerce_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def load() -> list[Task]:
    raw = yaml.safe_load(TASKS_PATH.read_text()) or []
    return [
        Task(
            id=item["id"],
            title=item["title"],
            due=_coerce_date(item["due"]),
            category=item.get("category", "setup"),
            context=(item.get("context") or "").strip(),
            done=bool(item.get("done", False)),
            done_on=_coerce_date(item.get("done_on")),
        )
        for item in raw
    ]


def save(tasks: list[Task]) -> None:
    """Round-trip back to YAML, preserving field order."""
    data = [
        {
            "id": t.id,
            "title": t.title,
            "due": t.due.isoformat() if t.due else None,
            "category": t.category,
            "context": t.context,
            "done": t.done,
            "done_on": t.done_on.isoformat() if t.done_on else None,
        }
        for t in tasks
    ]
    TASKS_PATH.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def toggle(task_id: str, today: date | None = None) -> Task | None:
    today = today or date.today()
    tasks = load()
    for t in tasks:
        if t.id == task_id:
            t.done = not t.done
            t.done_on = today if t.done else None
            save(tasks)
            return t
    return None


def open_tasks(today: date | None = None) -> list[Task]:
    """Sorted: overdue first, then by due date. Done tasks excluded."""
    today = today or date.today()
    return sorted(
        (t for t in load() if not t.done),
        key=lambda t: (t.due is None, t.due or date.max),
    )


def due_within(days: int, today: date | None = None) -> list[Task]:
    today = today or date.today()
    cutoff = today.toordinal() + days
    return [t for t in open_tasks(today) if t.due is not None and t.due.toordinal() <= cutoff]
