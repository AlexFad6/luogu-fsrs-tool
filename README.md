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

菜单会依次列出添加记录、爬取题目、复习、推荐、统计等功能，输入 `0` 退出；命令行子命令仍保留用于脚本调用。

```bash
python main.py add P1001 --title "A+B Problem" --difficulty "入门" --tags "模拟" --score 1
python main.py today
python main.py review P1001 --score 0.8
python main.py recommend
python main.py stats
```

首次运行会自动创建 `data/luogu_fsrs.db`。每次启动时会创建当天的数据库备份，并清理超过 7 天的备份。

## 爬取与标签

```bash
python main.py fetch P1001
python main.py fetch P1001 --force
python main.py show P1001
```

`fetch` 默认优先使用本地数据库，已有题目不会重复请求洛谷；本地没有该题时才会爬取。
传入 `--force` 会强制重新爬取，并覆盖本地题目元数据和标签关联。
爬取会保留全部原始标签，并按 [config.yaml](config.yaml) 的洛谷官方一级/二级分类保存。
薄弱标签分析与新题推荐只使用一级分类为“算法”的标签；来源、时间、区域和特殊题目标签不参与推荐。
