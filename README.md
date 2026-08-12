# qq-chat-exporter-and-report-generator
基于 https://github.com/QQBackup/qq-win-db-key 的qq聊天记录导出与年度总结整理工具

# QQ 群年度报告生成工具

这是一组用于从 QQ NT 聊天数据库导出群聊纯文字记录、统计年度数据，并生成年度报告网页海报的 Python 脚本。

## 功能概览

- `export_group_text.py`：从 `nt_msg_plain.db` 的 `group_msg_table` 中读取指定 QQ 群消息，导出按月拆分的纯文字 Markdown，并生成统计报告。
- `generate_poster.py`：读取 `草稿.md` 中整理好的年度数据，生成可在浏览器中查看的可视化报告页面 `index.html`。
- 支持统计消息类型、每日消息数、年度热词、常见文字消息、常用 emoji、发言最多用户、发言天数最多用户等内容。
- 适合将 QQ 群聊天记录整理成年终总结、社群年度报告或长图海报素材。

## 环境要求

- Python 3.10 或更高版本。
- 无强制第三方 Python 依赖，脚本主要使用标准库：`argparse`、`sqlite3`、`datetime`、`json`、`re`、`pathlib`、`collections` 等。
- 浏览器：用于打开 `generate_poster.py` 生成的 `index.html`。

## 使用方法

### 1. 导出 QQ 群文字记录

准备 QQ NT 的消息数据库文件 `nt_msg_plain.db`，然后运行：

```bash
python export_group_text.py --database nt_msg_plain.db --output group_808688505_text_2025-07-27_to_2026-07-26
```

如果脚本仍位于 `work/` 目录，则运行：

```bash
python work/export_group_text.py --database nt_msg_plain.db --output group_808688505_text_2025-07-27_to_2026-07-26
```

参数说明：

- `--database`：QQ NT 聊天数据库路径，默认值为 `nt_msg_plain.db`。
- `--output`：导出目录，默认值为 `group_808688505_text_2025-07-27_to_2026-07-26`。

导出目录中会生成：

- `YYYY-MM.md`：按月份拆分的纯文字聊天记录。
- `STATISTICS.md`：发言、文字消息、emoji、消息类型等统计结果。
- `README.md`：本次导出数据的说明和月度文件索引。

### 2. 整理年度报告草稿

将统计结果整理到 `草稿.md`。当前脚本会从该文件读取每日消息数表格，同时使用脚本内置的年度热词、常见消息、emoji 和活跃用户榜单数据。

`草稿.md` 中包含的示例模块包括：

- 消息总览：文字、表情包、图片、视频、其他消息数量及占比。
- 每日消息数变化：用于生成折线图。
- 年度热词 Top 10。
- 最常发送的文字消息 Top 10。
- 最常使用的 emoji Top 10。
- 发言最多用户 Top 10。
- 发言天数最多用户 Top 10。

### 3. 生成年度报告页面

在仓库根目录运行：

```bash
python generate_poster.py
```

脚本会读取 `草稿.md` 并生成：

```text
index.html
```

用浏览器打开 `index.html` 即可查看年度报告页面。页面包含消息占比图、每日消息折线图、热词排行、常见消息、emoji 榜、活跃用户榜和结语区域。

- `export_group_text.py` 中的群号和时间范围目前写在脚本常量中，如需复用到其他群或其他年份，需要修改 `GROUP_ID`、`START_LOCAL`、`END_LOCAL`。
- QQ 的收藏表情与普通图片都可能使用 `PicElement`，脚本无法可靠完全区分，因此“表情包”统计是保守下界，“图片”中可能包含部分收藏表情。
- 回复引用中的旧消息文字不会作为新消息正文重复导出。
- 数据库中部分昵称可能已经是不可逆乱码，此时导出结果会优先显示 QQ 号或 `nt_uid`。
