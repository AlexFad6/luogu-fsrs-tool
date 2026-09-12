"""CLI entry point for the Luogu FSRS review tool."""

import re
import json
import sqlite3
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

import click
import yaml
from rich.console import Console
from rich.table import Table

import db
from fsrs_engine import initial_state, review_state
from fsrs_engine import review_state_by_rating
from fsrs_engine import retrievability
from recommender import new_recommendations, statistics
from crawler import LuoguCrawler
from tag_manager import TagManager
from review_scoring import infer_rating, timing_metrics
from tag_stats import weakness_stats
from fsrs import Rating

console = Console()
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
            state = initial_state(score)
            db.add_review(connection, pid, score, attempt_type="initial",
                          retrievability=state["retrievability"])
            db.save_card_state(connection, pid, state)
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
@click.argument("url")
@click.option("--force", is_flag=True, help="覆盖本地已有题目元数据")
def training(url: str, force: bool) -> None:
    """从洛谷题单页面批量导入题目，不逐题访问页面。"""
    crawler = LuoguCrawler()
    connection = setup()
    try:
        problems = crawler.fetch_training(url)
        saved_count = 0
        skipped_count = 0
        with connection:
            for problem in problems:
                existing = db.get_problem(connection, problem["pid"])
                if existing is not None and not force:
                    skipped_count += 1
                    continue
                db.save_problem(connection, problem)
                saved_count += 1
        console.print(f"题单导入完成：共 {len(problems)} 题，新增/更新 {saved_count} 题，跳过 {skipped_count} 题。")
    except Exception as exc:
        raise click.ClickException(f"题单导入失败：{exc}") from exc
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
@click.option("--score", type=click.FloatRange(0, 1))
@click.option("--auto", "automatic", is_flag=True, help="根据历史用时和错误提交自动推断评分")
@click.option("--duration", type=click.IntRange(0), help="本次用时（分钟）")
@click.option("--wrong-submissions", type=click.IntRange(0), default=0, show_default=True)
@click.option("--saw-solution", is_flag=True, help="本次复习前看过题解")
def review(pid: str, score: float | None, automatic: bool, duration: int | None,
           wrong_submissions: int, saw_solution: bool) -> None:
    """完成一次复习并安排下次复习。"""
    connection = setup()
    try:
        row = connection.execute("SELECT * FROM card_states WHERE pid = ?", (pid,)).fetchone()
        if row is None:
            raise click.ClickException(f"未找到题目 {pid} 的卡片，请先使用 add")
        existing = dict(row)
        existing["due"] = db.parse_datetime(existing.pop("due_date"))
        existing["last_review"] = db.parse_datetime(existing["last_review"])
        history = db.get_attempts(connection, pid)
        if automatic and score is not None:
            raise click.UsageError("--auto 不能与 --score 同时使用")
        if score is None and not automatic:
            raise click.UsageError("请提供 --score，或使用 --auto")
        problem = db.get_problem(connection, pid)
        current = {"duration": duration, "wrong_submissions": wrong_submissions,
                   "saw_solution": saw_solution, "difficulty": problem["difficulty"],
                   "due_date": existing["due"],
                   "attempt_type": "initial" if not history else "review",
                   "_pool_history": db.get_all_attempts_for_stats(connection)}
        if automatic:
            rating, reason = infer_rating(history, current)
            console.print(f"自动推断：{rating.name}（{reason}）")
            if not click.confirm("确认使用该评分？", default=True):
                console.print("已取消。")
                return
            score = {Rating.Again: 0.0, Rating.Hard: 0.3,
                     Rating.Good: 0.5, Rating.Easy: 0.8}[rating]
        else:
            rating = None
        with connection:
            attempt_type = current["attempt_type"]
            if attempt_type == "initial":
                db.add_review(connection, pid, score, duration=duration,
                          wrong_submissions=wrong_submissions,
                          saw_solution=saw_solution, attempt_type="initial")
                state = initial_state(score)
            else:
                db.add_review(connection, pid, score, duration=duration,
                          wrong_submissions=wrong_submissions,
                          saw_solution=saw_solution, attempt_type="review",
                          retrievability=retrievability(existing))
                state = review_state_by_rating(existing, rating) if rating else review_state(existing, score)
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
            timing_weak = ", ".join(
                f"{item['tag']}={item['weakness']:.2f}"
                for item in weakness_stats(connection)
                if item["weakness"] is not None and item["weakness"] > 0
            )
            if timing_weak:
                console.print(f"  用时弱项: {timing_weak}")
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
        console.print("  标签用时弱项:")
        for item in weakness_stats(connection):
            value = "数据不足" if item["weakness"] is None else f"{item['weakness']:.2f}"
            console.print(f"    {item['tag']}: {value}（样本 {item['samples']}）")
        all_attempts = db.get_all_attempts_for_stats(connection)
        console.print("  题目用时基线：")
        baseline_rows = []
        for problem in connection.execute("SELECT pid, title, difficulty FROM problems ORDER BY pid"):
            history = [row for row in all_attempts if row["pid"] == problem["pid"]]
            floor, baseline, count = timing_metrics(
                history, {"difficulty": problem["difficulty"], "_pool_history": all_attempts}
            )
            card = connection.execute(
                "SELECT * FROM card_states WHERE pid = ?", (problem["pid"],)
            ).fetchone()
            current_retrievability = None
            if card is not None:
                card_state = dict(card)
                card_state["due"] = db.parse_datetime(card_state.pop("due_date"))
                card_state["last_review"] = db.parse_datetime(card_state["last_review"])
                current_retrievability = retrievability(card_state)
            baseline_rows.append(
                (current_retrievability if current_retrievability is not None else 1.0,
                 problem, floor, baseline, count, current_retrievability)
            )
        baseline_rows.sort(key=lambda row: (row[0], row[1]["pid"]))
        table = Table(show_header=True, header_style="bold")
        table.add_column("题号")
        table.add_column("题目")
        table.add_column("Floor", justify="right")
        table.add_column("Baseline", justify="right")
        table.add_column("尝试次数", justify="right")
        table.add_column("可提取度 R", justify="right")
        for _, problem, floor, baseline, count, current_retrievability in baseline_rows:
            table.add_row(
                problem["pid"], problem["title"], f"{floor:.2f}", f"{baseline:.2f}",
                str(count), "-" if current_retrievability is None
                else f"{current_retrievability:.2f}",
            )
        console.print(table)
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


def _pid_from_input(value: str) -> tuple[str, str | None]:
    value = value.strip()
    if value.startswith(("http://", "https://")):
        parts = [part for part in urlparse(value).path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "problem":
            return validate_pid(parts[-1]), value
        raise click.BadParameter("网址必须是洛谷题目链接")
    return validate_pid(value), None


def _training_url(value: str) -> str | None:
    """Return a canonical training URL when input identifies a problem set."""
    value = value.strip()
    if value.isdigit():
        return f"https://www.luogu.com.cn/training/{value}"
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "training" and parts[-1].isdigit():
            return value
    return None


def _problem_needs_metadata(problem: sqlite3.Row | None, pid: str) -> bool:
    """Identify placeholder problems created before metadata was available."""
    if problem is None or problem["title"] != pid:
        return problem is None
    try:
        tags = json.loads(problem["all_tags"] or "[]")
    except (TypeError, ValueError):
        tags = []
    return not problem["difficulty"] and not tags


def _save_problem_for_ac(connection: sqlite3.Connection, pid: str, link: str,
                         problem: sqlite3.Row | None) -> sqlite3.Row | None:
    """Ensure an AC result has a persisted problem before creating its card."""
    if not _problem_needs_metadata(problem, pid):
        return problem
    try:
        fetched = LuoguCrawler().fetch_problem(pid)
        fetched["source_url"] = link
        db.save_problem(connection, fetched)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        console.print(f"获取题目 {pid} 元数据失败，将使用占位信息：{exc}", markup=False)
        if problem is None:
            db.save_problem(connection, {"pid": pid, "title": pid, "all_tags": [],
                                         "source_url": link})
    return db.get_problem(connection, pid)


def _import_luogu_problem() -> None:
    """Import a training set or one problem, preferring training-set detection."""
    value = click.prompt("输入题单链接、题单 ID、题目链接或题号")
    training_url = _training_url(value)
    force = click.confirm("是否覆盖本地已有题目？", default=False)
    if training_url is not None:
        training.callback(training_url, force)
        return
    if value.startswith(("http://", "https://")):
        parts = [part for part in urlparse(value).path.split("/") if part]
        if not (len(parts) >= 2 and parts[-2] == "problem"):
            raise click.BadParameter("无法识别链接：请输入洛谷题单链接或题目链接")
    pid, _ = _pid_from_input(value)
    fetch.callback((pid,), 1.0, force)


def _start_solving() -> None:
    """Run a timed solving session and save it when the user reports AC."""
    connection = setup()
    try:
        due = db.due_problems(connection)[:5]
        new = new_recommendations(connection, max(0, 5 - len(due)))
    finally:
        connection.close()
    candidates = due + new
    console.print("\n每日推荐：")
    if due:
        console.print(f"  复习题（{len(due)} 题）")
        for index, row in enumerate(due, 1):
            console.print(f"    {index}. {row['pid']} - {row['title']}")
    if new:
        console.print(f"  新题（{len(new)} 题）")
        for index, row in enumerate(new, len(due) + 1):
            console.print(f"    {index}. {row['pid']} - {row['title']}")
    if not candidates:
        console.print("  当前没有推荐题目。")
    value = click.prompt("输入推荐题目编号，或输入题号/题目链接")
    link = None
    if value.strip().isdigit():
        index = int(value.strip())
        if not 1 <= index <= len(candidates):
            raise click.BadParameter("推荐题目编号超出范围")
        pid = candidates[index - 1]["pid"]
    else:
        pid, link = _pid_from_input(value)
    if link is None:
        link = f"https://www.luogu.com.cn/problem/{pid}"
        webbrowser.open(link)
    console.print(f"开始做题：{pid}。输入 1 记录 AC，输入 2 记录 WA，输入 0 放弃本次记录。")
    started = time.monotonic()
    wrong_submissions = 0
    while True:
        result = click.prompt("本次结果", type=click.IntRange(0, 2))
        if result == 0:
            console.print("已退出，本次做题记录未保存。")
            return
        if result == 1:
            break
        wrong_submissions += 1
        console.print(f"已记录 WA（累计 {wrong_submissions} 次）。")
    duration = max(1, round((time.monotonic() - started) / 60))
    connection = setup()
    try:
        with connection:
            problem = db.get_problem(connection, pid)
            problem = _save_problem_for_ac(connection, pid, link, problem)
            if problem is None:
                raise click.ClickException(f"无法保存题目 {pid}")
            connection.execute("UPDATE problems SET is_solved = 1 WHERE pid = ?", (pid,))
            if problem["title"] == pid and not problem["difficulty"]:
                state = initial_state(0.5)
                db.add_review(connection, pid, 0.5, duration=duration,
                              wrong_submissions=wrong_submissions, attempt_type="initial",
                              retrievability=state["retrievability"])
                db.save_card_state(connection, pid, state)
                rating_text = "Good（新题）"
            else:
                card = connection.execute("SELECT * FROM card_states WHERE pid = ?", (pid,)).fetchone()
                if card is None:
                    state = initial_state(0.5)
                    db.add_review(connection, pid, 0.5, duration=duration,
                                  wrong_submissions=wrong_submissions, attempt_type="initial",
                                  retrievability=state["retrievability"])
                    db.save_card_state(connection, pid, state)
                    rating_text = "Good（自动创建卡片）"
                else:
                    history = db.get_attempts(connection, pid)
                    card_state = dict(card)
                    card_state["due"] = db.parse_datetime(card_state.pop("due_date"))
                    card_state["last_review"] = db.parse_datetime(card_state["last_review"])
                    current_retrievability = retrievability(card_state)
                    rating, reason = infer_rating(
                        history, {"duration": duration, "wrong_submissions": wrong_submissions,
                         "difficulty": problem["difficulty"],
                         "due_date": card_state["due"],
                         "retrievability": current_retrievability,
                         "attempt_type": "review",
                         "_pool_history": db.get_all_attempts_for_stats(connection)}
                    )
                    db.add_review(connection, pid, {
                        Rating.Again: 0.0, Rating.Hard: 0.3,
                        Rating.Good: 0.5, Rating.Easy: 0.8,
                    }[rating], duration=duration, wrong_submissions=wrong_submissions,
                                  attempt_type="review",
                                  retrievability=current_retrievability)
                    state = card_state
                    db.save_card_state(connection, pid, review_state_by_rating(state, rating))
                    previous_reviews = [
                        row for row in history
                        if row.get("attempt_type", "review") == "review"
                        and row.get("duration") is not None
                    ]
                    previous_duration = (
                        previous_reviews[-1]["duration"] if previous_reviews else duration
                    )
                    improvement_match = re.search(r"imp=([-+]?\d+(?:\.\d+)?)", reason)
                    improvement_text = improvement_match.group(1) if improvement_match else "0.00"
                    rating_text = (
                        f"{rating.name}（用时 {previous_duration:g}->{duration:g} 分钟，"
                        f"imp={improvement_text} -> {rating.name}）"
                    )
    finally:
        connection.close()
    console.print(f"AC，已记录用时 {duration} 分钟，评分：{rating_text}。")


def interactive_menu() -> None:
    """Run the numbered interactive interface used when no command is supplied."""
    options = ["开始做题", "每日推荐", "导入洛谷题目",
               "学习统计", "查看题目详情", "浏览标签库"]
    while True:
        choice = _menu_choice("洛谷 FSRS 复习工具", options)
        try:
            if choice == 0:
                _start_solving()
            elif choice == 1:
                recommend.callback(5)
            elif choice == 2:
                _import_luogu_problem()
            elif choice == 3:
                stats.callback()
            elif choice == 4:
                pid = validate_pid(click.prompt("题号"))
                show.callback(pid)
            elif choice == 5:
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
