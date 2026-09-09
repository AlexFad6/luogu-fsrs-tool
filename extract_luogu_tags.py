#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从洛谷题目列表页保存的 MHTML 文件中提取“选择标签”弹窗里的所有标签，
并按分类整理输出。

用法:
    python extract_luogu_tags.py <mhtml文件路径>

输出示例:
    算法:
          语言入门: [语言入门, 顺序结构, 分支结构, ...]
          字符串: [字符串, 后缀自动机 SAM, ...]
          ...
"""

import email
import re
import sys

# 顶层分类可按需修改
TOP_CATEGORY = "区域"


def read_html_from_mhtml(path: str) -> str:
    """读取 MHTML 文件并解码其中的 text/html 部分（处理 quoted-printable）。"""
    with open(path, "rb") as f:
        msg = email.message_from_bytes(f.read())
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8", errors="replace")
    raise RuntimeError("未在 MHTML 中找到 text/html 部分")


def extract_tag_sections(html: str) -> dict:
    """从 HTML 的标签选择区域提取 {分类名: [标签...]}。"""
    start = html.find('class="tag-select-area"')
    if start == -1:
        raise RuntimeError("未找到标签选择区域（tag-select-area）")
    # 弹窗以“确认”按钮收尾，截到这里即可
    end = html.find("确认", start)
    segment = html[start:end if end != -1 else None]

    # 每个 section = 一个分类：<div class="title">分类名</div><div class="tags">…</div>
    section_re = re.compile(
        r'class="title">([^<]+)</div><div[^>]*class="tags">(.*?)</div></div>',
        re.S,
    )
    tag_re = re.compile(r'class="toggle-tag">(.*?)<!---->')

    result = {}
    for title, tags_html in section_re.findall(segment):
        tags = [t.strip() for t in tag_re.findall(tags_html)]
        if tags:
            result[title.strip().lstrip("\ufeff")] = [t.lstrip("\ufeff") for t in tags]
    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    html = read_html_from_mhtml(sys.argv[1])
    sections = extract_tag_sections(html)

    if not sections:
        print("未提取到任何标签", file=sys.stderr)
        sys.exit(1)

    print(f"{TOP_CATEGORY}:")
    for category, tags in sections.items():
        print(f"      {category}: [{', '.join(tags)}]")


if __name__ == "__main__":
    main()