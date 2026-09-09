"""Luogu's hierarchical tag dictionary and problem-tag queries."""

from dataclasses import dataclass
import sqlite3
from pathlib import Path
import re
from typing import Optional

import yaml


@dataclass(frozen=True)
class Tag:
    id: int
    name: str
    category_l1: str
    category_l2: Optional[str]


class TagManager:
    """Manage official-level tag categories and their database dictionary."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.hierarchy = self._load_hierarchy()

    @staticmethod
    def _load_hierarchy() -> dict:
        path = Path(__file__).with_name("config.yaml")
        with path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
        return config.get("tags", {}).get("hierarchy", {})

    def initialize(self) -> None:
        for l1, groups in self.hierarchy.items():
            for l2, names in groups.items():
                for name in names:
                    self.connection.execute(
                        "INSERT OR IGNORE INTO tags(name, category_l1, category_l2) VALUES (?, ?, ?)",
                        (name, l1, l2),
                    )

    def classify_tag(self, name: str) -> tuple[str, str | None]:
        mappings = self.hierarchy
        custom = self._custom_mappings()
        if name in custom:
            return custom[name]["category_l1"], custom[name].get("category_l2")
        if re.fullmatch(r"(19|20)\d{2}", name):
            return "时间", "年份"
        for l1, groups in mappings.items():
            for l2, names in groups.items():
                if name in names:
                    return l1, l2
        return "未分类", None

    def _custom_mappings(self) -> dict:
        path = Path(__file__).with_name("config.yaml")
        with path.open(encoding="utf-8") as stream:
            return (yaml.safe_load(stream) or {}).get("tags", {}).get("custom_mappings", {})

    def get_or_create_tag(self, name: str) -> Tag:
        row = self.connection.execute(
            "SELECT id, name, category_l1, category_l2 FROM tags WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            l1, l2 = self.classify_tag(name)
            self.connection.execute(
                "INSERT INTO tags(name, category_l1, category_l2) VALUES (?, ?, ?)",
                (name, l1, l2),
            )
            row = self.connection.execute(
                "SELECT id, name, category_l1, category_l2 FROM tags WHERE name = ?", (name,)
            ).fetchone()
        return Tag(row["id"], row["name"], row["category_l1"], row["category_l2"])

    def get_tags_by_category(self, category_l1: str, category_l2: str | None = None) -> list[Tag]:
        query = "SELECT id, name, category_l1, category_l2 FROM tags WHERE category_l1 = ?"
        params: list[str] = [category_l1]
        if category_l2:
            query += " AND category_l2 = ?"
            params.append(category_l2)
        query += " ORDER BY category_l2, name"
        return [Tag(*row) for row in self.connection.execute(query, params)]

    def get_algorithm_tags(self, pid: str) -> list[str]:
        return [
            row["name"] for row in self.connection.execute(
                """SELECT t.name FROM problem_tags pt JOIN tags t ON t.id = pt.tag_id
                   WHERE pt.pid = ? AND t.category_l1 = '算法' ORDER BY t.name""", (pid,)
            )
        ]

    def get_problem_with_tags(self, pid: str) -> dict | None:
        problem = self.connection.execute(
            "SELECT pid, title, difficulty, is_solved FROM problems WHERE pid = ?", (pid,)
        ).fetchone()
        if problem is None:
            return None
        tags = [dict(row) for row in self.connection.execute(
            """SELECT t.name, t.category_l1, t.category_l2
               FROM problem_tags pt JOIN tags t ON t.id = pt.tag_id
               WHERE pt.pid = ? ORDER BY t.category_l1, t.category_l2, t.name""", (pid,)
        )]
        grouped: dict[str, list[str]] = {}
        for tag in tags:
            grouped.setdefault(tag["category_l1"], []).append(tag["name"])
        return {**dict(problem), "tags": tags, "tags_by_category": grouped}
