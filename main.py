"""CLI entry point for the Luogu FSRS review tool."""

import re
import json
import sqlite3
import time

import click
from rich.console import Console

import db
from fsrs_engine import initial_state, review_state
from recommender import new_recommendations, statistics
from crawler import LuoguCrawler
from tag_manager import TagManager

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
            db.save_problem(connection, {
                "pid": pid, "title": title, "difficulty": difficulty,
                "all_tags": [x.strip() for x in tags.split(",") if x.strip()],
            })
            connection.execute("UPDATE problems SET is_solved = 1 WHERE pid = ?", (pid,))
            db.add_review(connection, pid, score)
            db.save_card_state(connection, pid, initial_state(score))
        console.print(f"[green]已添加 {pid}，下次复习已安排。[/green]")
    except Exception as exc:
        raise click.ClickException(f"保存失败：{exc}") from exc
    finally:
        connection.close()


@cli.command()
@click.argument("pids", nargs=-1, required=True, callback=lambda ctx, param, values: tuple(
    validate_pid(value) for value in values
))
@click.option("--delay", "-d", default=1.0, show_default=True, help="请求间隔秒数")
@click.option("--force", is_flag=True, help="忽略本地数据，强制重新爬取并覆盖")
def fetch(pids: tuple[str, ...], delay: float, force: bool) -> None:
    """优先使用本地题目数据；使用 --force 强制重新爬取。"""
    crawler = LuoguCrawler()
    connection = setup()
    try:
        for index, pid in enumerate(pids):
            try:
                existing = db.get_problem_with_tags(connection, pid)
                if existing is not None and not force:
                    saved = existing
                    console.print(f"[cyan]使用本地数据 {pid}: {saved['title']}[/cyan]")
                else:
                    result = crawler.fetch_problem(pid)
                    with connection:
                        db.save_problem(connection, result)
                    saved = db.get_problem_with_tags(connection, result["pid"])
                    console.print(f"[green]已爬取并覆盖 {result['pid']}: {result['title']}[/green]"
                                  if existing is not None else
                                  f"[green]已爬取 {result['pid']}: {result['title']}[/green]")
                console.print(f"  难度: {saved['difficulty'] or '未设置'}")
                for category, names in saved["tags_by_category"].items():
                    console.print(f"  {category}: {', '.join(names)}")
            except Exception as exc:
                console.print(f"[red]爬取 {pid} 失败：{exc}[/red]", markup=False)
            if index < len(pids) - 1:
                time.sleep(delay)
    finally:
        connection.close()


@cli.command()
@click.argument("pid", callback=lambda ctx, param, value: validate_pid(value))
def show(pid: str) -> None:
    """显示题目、标签和复习状态。"""
    connection = setup()
    try:
        problem = db.get_problem(connection, pid)
        if problem is None:
            raise click.ClickException(f"题目 {pid} 不存在，请先使用 fetch")
        def tags(name: str, fallback: str = "[]") -> list[str]:
            try:
                return json.loads(problem[name] or fallback)
            except json.JSONDecodeError:
                return []
        console.print(f"[题目] {pid} - {problem['title']}")
        console.print(f"  难度: {problem['difficulty'] or '未设置'}")
        detailed = db.get_problem_with_tags(connection, pid)
        console.print("  标签分类:")
        for category, names in detailed["tags_by_category"].items():
            console.print(f"    {category}: {'、'.join(names)}")
        state = connection.execute("SELECT * FROM card_states WHERE pid = ?", (pid,)).fetchone()
        if state:
            console.print("\n  复习状态:")
            console.print(f"    下次复习: {state['due_date'] or '未安排'}")
            console.print(f"    复习次数: {state['reps']}")
            console.print(f"    稳定性: {state['stability'] or '未计算'}")
    finally:
        connection.close()


@cli.command(name="tags")
@click.option("--category", "-c", help="一级分类，例如 算法")
@click.option("--subcategory", "-s", help="二级分类，例如 字符串")
def browse_tags(category: str | None, subcategory: str | None) -> None:
    """浏览洛谷标签分类字典。"""
    connection = setup()
    try:
        manager = TagManager(connection)
        if category:
            rows = manager.get_tags_by_category(category, subcategory)
            console.print(f"📂 {category}{f' / {subcategory}' if subcategory else ''}（{len(rows)} 个）")
            for row in rows:
                console.print(f"  {row.name}")
        else:
            console.print("📚 洛谷标签分类体系")
            for l1, groups in manager.hierarchy.items():
                console.print(f"\n{l1}")
                for l2, names in groups.items():
                    console.print(f"  【{l2}】（{len(names)} 个）")
    finally:
        connection.close()


@cli.command()
def today() -> None:
    """查看今日待复习题目。"""
    connection = setup()
    try:
        rows = db.due_problems(connection)
        console.print(f"今日待复习（{len(rows)} 题）：")
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
@click.option("--count", "-c", default=5, show_default=True, help="推荐题目数量")
def recommend(count: int) -> None:
    """推荐复习题和薄弱标签相关的新题。"""
    connection = setup()
    try:
        due = db.due_problems(connection)
        due = due[:count]
        new = new_recommendations(connection, count - len(due))
        console.print(f"今日推荐（共 {len(due) + len(new)} 题）：")
        console.print(f"\n【复习 - {len(due)} 题】")
        for i, row in enumerate(due, 1):
            console.print(f"  {i}. {row['pid']} - {row['title']} [{row['difficulty'] or '未设置'}]")
        console.print(f"\n【新题 - {len(new)} 题】")
        for i, row in enumerate(new, len(due) + 1):
            console.print(f"  {i}. {row['pid']} - {row['title']} [{row['difficulty'] or '未设置'}]")
        if new:
            weak = ", ".join(tag for tag, _ in statistics(connection)["weak_tags"])
    finally:
        connection.close()


@cli.command()
def stats() -> None:
    """查看学习统计。"""
    connection = setup()
    try:
        result = statistics(connection)
        weak = ", ".join(f"{tag} (错误率 {rate:.0%})" for tag, rate in result["weak_tags"]) or "无"
        console.print("学习统计：")
        console.print(f"  总题数: {result['total']}\n  已复习: {result['reviewed']}")
        console.print(f"  正确率: {result['accuracy']:.0%}\n  薄弱标签: {weak}")
        console.print(f"  连续打卡: {result['streak']} 天")
    finally:
        connection.close()


if __name__ == "__main__":
    cli()
