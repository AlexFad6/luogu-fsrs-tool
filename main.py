"""CLI entry point for the Luogu FSRS review tool."""

import json
import re
import sqlite3
from datetime import datetime, timezone

import click
from rich.console import Console

import db
from fsrs_engine import initial_state, review_state
from recommender import new_recommendations, statistics

console = Console(legacy_windows=False)
# Luogu identifiers use multiple prefixes (for example P, B, CF, and UVA).
PID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$", re.IGNORECASE)


def validate_pid(value: str) -> str:
    value = value.upper()
    if not PID_RE.fullmatch(value):
        raise click.BadParameter(
            "题号必须以字母开头，只能包含字母、数字、下划线，例如 P1001、CF100A"
        )
    return value


def setup() -> sqlite3.Connection:
    connection = db.connect()
    db.initialize_database(connection)
    db.backup_database()
    return connection


@click.group()
def cli() -> None:
    """基于 FSRS 的洛谷刷题复习工具。"""


@cli.command()
@click.argument("pid", callback=lambda ctx, param, value: validate_pid(value))
@click.option("--title", required=True)
@click.option("--difficulty")
@click.option("--tags", default="", help="用逗号分隔多个标签")
@click.option("--score", type=click.FloatRange(0, 1), required=True)
def add(pid: str, title: str, difficulty: str | None, tags: str, score: float) -> None:
    """添加做题记录并创建 FSRS 卡片。"""
    connection = setup()
    try:
        with connection:
            db.upsert_problem(connection, pid, title, difficulty, [x.strip() for x in tags.split(",") if x.strip()])
            db.add_review(connection, pid, score)
            db.save_card_state(connection, pid, initial_state(score))
        console.print(f"[green]已添加 {pid}，下次复习已安排。[/green]")
    except Exception as exc:
        raise click.ClickException(f"保存失败：{exc}") from exc
    finally:
        connection.close()


@cli.command()
def today() -> None:
    """查看今日待复习题目。"""
    connection = setup()
    try:
        rows = db.due_problems(connection)
        console.print(f"📋 今日待复习（{len(rows)} 题）：")
        for index, row in enumerate(rows, 1):
            console.print(f"  {index}. {row['pid']} - {row['title']} [{row['difficulty'] or '未设置'}]")
    finally:
        connection.close()


@cli.command()
@click.argument("pid", callback=lambda ctx, param, value: validate_pid(value))
@click.option("--score", type=click.FloatRange(0, 1), required=True)
def review(pid: str, score: float) -> None:
    """完成一次复习并安排下次复习。"""
    connection = setup()
    try:
        row = connection.execute("SELECT * FROM card_states WHERE pid = ?", (pid,)).fetchone()
        if row is None:
            raise click.ClickException(f"未找到题目 {pid} 的卡片，请先使用 add")
        existing = dict(row)
        existing["due"] = db.parse_datetime(existing.pop("due_date"))
        existing["last_review"] = db.parse_datetime(existing["last_review"])
        with connection:
            db.add_review(connection, pid, score)
            state = review_state(existing, score)
            db.save_card_state(connection, pid, state)
        console.print(f"已完成 {pid}，下次复习：{state['due_date']}")
    finally:
        connection.close()


@cli.command()
def recommend() -> None:
    """推荐复习题和薄弱标签相关的新题。"""
    connection = setup()
    try:
        due = db.due_problems(connection)
        due = due[:5]
        new = new_recommendations(connection, 5 - len(due))
        console.print(f"🎯 今日推荐（共 {len(due) + len(new)} 题）：")
        console.print(f"\n【复习 - {len(due)} 题】")
        for i, row in enumerate(due, 1):
            console.print(f"  {i}. {row['pid']} - {row['title']} [{row['difficulty'] or '未设置'}]")
        console.print(f"\n【新题 - {len(new)} 题】")
        for i, row in enumerate(new, len(due) + 1):
            console.print(f"  {i}. {row['pid']} - {row['title']} [{row['difficulty'] or '未设置'}]")
    finally:
        connection.close()


@cli.command()
def stats() -> None:
    """查看学习统计。"""
    connection = setup()
    try:
        result = statistics(connection)
        weak = ", ".join(f"{tag} (错误率 {rate:.0%})" for tag, rate in result["weak_tags"]) or "无"
        console.print("📊 学习统计：")
        console.print(f"  总题数: {result['total']}\n  已复习: {result['reviewed']}")
        console.print(f"  正确率: {result['accuracy']:.0%}\n  薄弱标签: {weak}")
        console.print(f"  连续打卡: {result['streak']} 天 🔥")
    finally:
        connection.close()


if __name__ == "__main__":
    cli()
