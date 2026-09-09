"""Small Luogu problem page crawler."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

class LuoguCrawler:
    """Fetch problem metadata without filtering the source tag list."""

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def _fetch_html(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "luogu-fsrs-tool/1.0"})
        with urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def _fetch_json(self, url: str) -> dict:
        return json.loads(self._fetch_html(url))

    def _tag_names(self) -> dict[int, str]:
        tag_data = self._fetch_json("https://www.luogu.com.cn/_lfe/tags/zh-CN")
        return {item["id"]: item["name"] for item in tag_data.get("tags", [])}

    @staticmethod
    def _embedded_data(html: str) -> dict:
        match = re.search(
            r'<script[^>]*id=["\']lentille-context["\'][^>]*>(.*?)</script>',
            html, flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise ValueError("无法解析洛谷页面数据")
        return json.loads(unescape(match.group(1))).get("data", {})

    def _parse_problem_page(self, html: str) -> dict:
        """Parse the ``lentille-context`` JSON embedded in a Luogu page."""
        problem = self._embedded_data(html).get("problem")
        if isinstance(problem, dict):
            return self._normalize(problem, self._tag_names())
        raise ValueError("无法解析洛谷页面中的题目信息")

    @staticmethod
    def _normalize(problem: dict, tag_names: dict[int, str]) -> dict:
        tags = [tag_names.get(tag, str(tag)) for tag in problem.get("tags", [])]
        pid = problem.get("pid") or problem.get("id")
        title = problem.get("title") or problem.get("name")
        if not pid or not title:
            raise ValueError("页面缺少题号或标题")
        config_path = Path(__file__).with_name("config.yaml")
        with config_path.open(encoding="utf-8") as stream:
            levels = (yaml.safe_load(stream) or {}).get("difficulty", {}).get("levels", [])
        difficulty = levels[problem["difficulty"]-1] if (
            isinstance(problem.get("difficulty"), int)
            and 1 <= problem["difficulty"] <= len(levels)
        ) else problem.get("difficulty")
        return {"pid": str(pid), "title": str(title),
                "difficulty": difficulty, "tags": tags}

    def fetch_problem(self, pid: str) -> dict:
        """Fetch and classify all tags for one problem."""
        url = f"https://www.luogu.com.cn/problem/{pid}?_contentOnly=1"
        raw = self._parse_problem_page(self._fetch_html(url))
        return {**raw, "all_tags": raw["tags"], "tags": raw["tags"],
                "source_url": url}

    def fetch_training(self, url: str) -> list[dict]:
        """Fetch every problem embedded in a training page in one request."""
        data = self._embedded_data(self._fetch_html(url))
        training = data.get("training")
        if not isinstance(training, dict):
            raise ValueError("网址不是有效的洛谷题单页面")
        tag_names = self._tag_names()
        source_url = url.split("#", 1)[0]
        results = []
        for problem in training.get("problems", []):
            normalized = self._normalize(problem, tag_names)
            results.append({
                **normalized,
                "all_tags": normalized["tags"],
                "source_url": source_url,
            })
        return results
