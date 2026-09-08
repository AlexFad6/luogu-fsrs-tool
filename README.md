# luogu-fsrs-tool

基于 FSRS 的洛谷刷题复习 CLI 工具（Python 3.10+）。

## 安装

```bash
python -m pip install -r requirements.txt
```

## 使用

```bash
python main.py add P1001 --title "A+B Problem" --difficulty "入门" --tags "模拟" --score 1
python main.py today
python main.py review P1001 --score 0.8
python main.py recommend
python main.py stats
```

首次运行会自动创建 `data/luogu_fsrs.db`。每次启动时会创建当天的数据库备份，并清理超过 7 天的备份。
