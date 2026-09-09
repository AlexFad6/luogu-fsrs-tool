"""CLI entry point for the Luogu FSRS review tool."""

import re
import json
import sqlite3
import time
from pathlib import Path

import click
import yaml
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


@click.group(invoke_without_command=True)
@click.pass_context
def cli(context: click.Context) -> None:
    """基于 FSRS 的洛谷刷题复习工具。"""
    if context.invoked_subcommand is None:
        interactive_menu()


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
            console.print(f"{category}{f' / {subcategory}' if subcategory else ''}（{len(rows)} 个）")
            for row in rows:
                console.print(f"  {row.name}")
        else:
            console.print("洛谷标签分类体系")
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


def _menu_choice(title: str, options: list[str]) -> int:
    """Display numbered options and return a zero-based selection."""
    console.print(f"\n{title}")
    for index, option in enumerate(options, 1):
        console.print(f"  {index}. {option}")
    console.print("  0. 退出")
    return click.prompt("请输入选项编号", type=click.IntRange(0, len(options))) - 1


def _difficulty_options() -> list[str]:
    config_path = Path(__file__).with_name("config.yaml")
    with config_path.open(encoding="utf-8") as stream:
        return (yaml.safe_load(stream) or {}).get("difficulty", {}).get("levels", [])


def interactive_menu() -> None:
    """Run the numbered interactive interface used when no command is supplied."""
    options = ["添加做题记录", "爬取/读取题目", "查看今日待复习", "完成复习",
               "每日推荐", "学习统计", "查看题目详情", "浏览标签库"]
    while True:
        choice = _menu_choice("洛谷 FSRS 复习工具", options)
        try:
            if choice == 0:
                pid = validate_pid(click.prompt("题号"))
                title = click.prompt("题目标题")
                difficulty = click.prompt("题目难度", type=click.Choice(_difficulty_options()),
                                          default=_difficulty_options()[0])
                tags = click.prompt("标签（逗号分隔）", default="")
                score = click.prompt("本次得分（0 / 0.3 / 0.5 / 0.8 / 1）",
                                     type=click.FloatRange(0, 1))
                add.callback(pid, title, difficulty, tags, score)
            elif choice == 1:
                pid = validate_pid(click.prompt("题号"))
                force = click.confirm("是否强制重新爬取并覆盖本地数据？", default=False)
                fetch.callback((pid,), 1.0, force)
            elif choice == 2:
                today.callback()
            elif choice == 3:
                pid = validate_pid(click.prompt("题号"))
                score = click.prompt("复习得分（0 / 0.3 / 0.5 / 0.8 / 1）",
                                     type=click.FloatRange(0, 1))
                review.callback(pid, score)
            elif choice == 4:
                count = click.prompt("推荐题目数量", type=click.IntRange(1), default=5)
                recommend.callback(count)
            elif choice == 5:
                stats.callback()
            elif choice == 6:
                pid = validate_pid(click.prompt("题号"))
                show.callback(pid)
            elif choice == 7:
                category = click.prompt("一级分类（留空查看全部）", default="")
                subcategory = ""
                if category:
                    subcategory = click.prompt("二级分类（留空查看该一级分类）", default="")
                browse_tags.callback(category or None, subcategory or None)
            elif choice == -1:
                return
        except (click.ClickException, click.BadParameter) as exc:
            console.print(f"[red]{exc}[/red]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n已退出。")
            return


if __name__ == "__main__":
    cli()
