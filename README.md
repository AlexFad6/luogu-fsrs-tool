# luogu-fsrs-tool

基于 FSRS 的洛谷刷题复习 CLI 工具（Python 3.10+）。

## 安装

```bash
python -m pip install -r requirements.txt
```

## 使用

直接运行会进入数字选择菜单：

```bash
python main.py
```

菜单会提供“开始做题”和“每日推荐”等入口，输入 `0` 退出；命令行子命令仍保留用于脚本调用。

“导入洛谷题目”会优先识别题单：输入题单链接或题单 ID（如 `204`）即可批量导入；
若不是题单，再按题目链接或题号处理。

“开始做题”会先展示每日推荐，输入推荐编号即可选择；也支持直接输入题号或洛谷题目链接。开始后会打开题目页面并计时，
输入 `1` 记录 AC，输入 `2` 记录 WA，输入 `0` 退出且不保存本次记录；AC 后会自动保存用时、错误提交次数并更新/创建 FSRS 卡片。

```bash
python main.py add P1001 --title "A+B Problem" --difficulty "入门" --tags "模拟" --score 1
python main.py today
python main.py review P1001 --score 0.8
python main.py review P1001 --auto --duration 12 --wrong-submissions 0
python main.py recommend
python main.py stats
```

首次运行会自动创建 `data/luogu_fsrs.db`。每次启动时会创建当天的数据库备份，并清理超过 7 天的备份。

## 爬取与标签

```bash
python main.py fetch P1001
python main.py fetch P1001 --force
python main.py training https://www.luogu.com.cn/training/204#problems
python main.py show P1001
```

`fetch` 默认优先使用本地数据库，已有题目不会重复请求洛谷；本地没有该题时才会爬取。
传入 `--force` 会强制重新爬取，并覆盖本地题目元数据和标签关联。
`training` 会直接解析题单页面内嵌的全部题目数据，只访问题单页面和标签字典，不会逐题访问题目页面。
爬取会保留全部原始标签，并按 [config.yaml](config.yaml) 的洛谷官方一级/二级分类保存。
薄弱标签分析与新题推荐只使用一级分类为“算法”的标签；来源、时间、区域和特殊题目标签不参与推荐。

复习记录支持用时、错误提交次数和是否看过题解。`--auto` 会根据同题历史用时衰减
推断 FSRS 评分；显式传入 `--score` 时始终以手动评分为准。

## 鸣谢

本项目基于以下优秀的开源项目构建：

- [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs) ——  间隔重复调度算法（FSRS） 的 Python 实现，本项目的核心复习引擎。感谢 [open-spaced-repetition](https://github.com/open-spaced-repetition) 社区的出色工作。
- [洛谷](https://www.luogu.com.cn/) —— 题目数据来源。