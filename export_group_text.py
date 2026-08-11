from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

GROUP_ID = 808688505
START_LOCAL = dt.datetime(2025, 7, 27, tzinfo=dt.timezone(dt.timedelta(hours=8)))
END_LOCAL = dt.datetime(2026, 7, 27, tzinfo=dt.timezone(dt.timedelta(hours=8)))
UTC8 = dt.timezone(dt.timedelta(hours=8))
ELEMENT_FIELD = 40800
TEXT_FIELD = 45101
CONTENT_TYPE_FIELD = 45002
CONTENT_TYPES = {
    1: "文字", 2: "图片/PicElement", 3: "文件", 4: "语音/PTT", 5: "视频",
    6: "QQ 系统表情", 7: "回复块", 8: "系统提示/GrayTip", 9: "红包",
    10: "Ark 卡片", 11: "商城表情", 14: "Markdown", 16: "旧版转发",
    17: "Markdown 按钮", 21: "通话记录",
}
MESSAGE_CATEGORIES = ("文字", "表情包", "图片", "视频", "拍一拍", "其他")

# Unicode Emoji property ranges used by the package-free sequence scanner. Keeping
# these intervals isolated makes a future Unicode data update straightforward.
EMOJI_RANGES = (
    (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122), (0x2139, 0x2139),
    (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B), (0x2328, 0x2328),
    (0x23CF, 0x23CF), (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x24C2, 0x24C2),
    (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE),
    (0x2600, 0x2604), (0x260E, 0x260E), (0x2611, 0x2611), (0x2614, 0x2615),
    (0x2618, 0x2618), (0x261D, 0x261D), (0x2620, 0x2620), (0x2622, 0x2623),
    (0x2626, 0x2626), (0x262A, 0x262A), (0x262E, 0x262F), (0x2638, 0x263A),
    (0x2640, 0x2640), (0x2642, 0x2642), (0x2648, 0x2653), (0x265F, 0x2660),
    (0x2663, 0x2663), (0x2665, 0x2666), (0x2668, 0x2668), (0x267B, 0x267B),
    (0x267E, 0x267F), (0x2692, 0x2697), (0x2699, 0x2699), (0x269B, 0x269C),
    (0x26A0, 0x26A1), (0x26A7, 0x26A7), (0x26AA, 0x26AB), (0x26B0, 0x26B1),
    (0x26BD, 0x26BE), (0x26C4, 0x26C5), (0x26C8, 0x26C8), (0x26CE, 0x26CF),
    (0x26D1, 0x26D1), (0x26D3, 0x26D4), (0x26E9, 0x26EA), (0x26F0, 0x26F5),
    (0x26F7, 0x26FA), (0x26FD, 0x26FD), (0x2702, 0x2702), (0x2705, 0x2705),
    (0x2708, 0x270D), (0x270F, 0x270F), (0x2712, 0x2712), (0x2714, 0x2714),
    (0x2716, 0x2716), (0x271D, 0x271D), (0x2721, 0x2721), (0x2728, 0x2728),
    (0x2733, 0x2734), (0x2744, 0x2744), (0x2747, 0x2747), (0x274C, 0x274C),
    (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757), (0x2763, 0x2764),
    (0x2795, 0x2797), (0x27A1, 0x27A1), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
    (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50),
    (0x2B55, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297),
    (0x3299, 0x3299), (0x1F000, 0x1FAFF),
)
VARIATION_SELECTORS = {0xFE0E, 0xFE0F}
ZWJ = 0x200D
KEYCAP = 0x20E3
BLACK_FLAG = 0x1F3F4
CANCEL_TAG = 0xE007F


class ProtobufError(ValueError):
    pass


@dataclass
class SenderInfo:
    nt_uid: str | None = None
    uin: int | None = None
    nickname: str | None = None


@dataclass
class Statistics:
    sender_messages: Counter[str]
    sender_days: dict[str, set[dt.date]]
    senders: dict[str, SenderInfo]
    exact_texts: Counter[str]
    emojis: Counter[str]
    monthly_emojis: dict[str, Counter[str]]
    message_categories: Counter[str]
    element_types: Counter[int]
    mixed_type_patterns: Counter[tuple[int, ...]]
    diagnostics: Counter[str]


@dataclass
class ParsedMessage:
    text: str | None
    element_types: list[int]
    parsed: bool


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ProtobufError("truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
    raise ProtobufError("oversized varint")


def iter_fields(data: bytes) -> Iterator[tuple[int, int, object]]:
    offset = 0
    while offset < len(data):
        key, offset = read_varint(data, offset)
        field_number, wire_type = key >> 3, key & 7
        if field_number == 0:
            raise ProtobufError("invalid field number")
        if wire_type == 0:
            value, offset = read_varint(data, offset)
        elif wire_type == 1:
            end = offset + 8
            if end > len(data):
                raise ProtobufError("truncated fixed64")
            value, offset = data[offset:end], end
        elif wire_type == 2:
            length, offset = read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ProtobufError("truncated length-delimited field")
            value, offset = data[offset:end], end
        elif wire_type == 5:
            end = offset + 4
            if end > len(data):
                raise ProtobufError("truncated fixed32")
            value, offset = data[offset:end], end
        else:
            raise ProtobufError(f"unsupported wire type {wire_type}")
        yield field_number, wire_type, value


def parse_message(payload: bytes | None) -> ParsedMessage:
    """Read top-level MsgContent envelopes without treating nested replies as new content."""
    if not payload:
        return ParsedMessage(None, [], False)
    parts: list[str] = []
    element_types: list[int] = []
    try:
        for field, wire_type, element in iter_fields(payload):
            if field != ELEMENT_FIELD or wire_type != 2:
                continue
            assert isinstance(element, bytes)
            content_type: int | None = None
            text_values: list[str] = []
            for child_field, child_wire_type, value in iter_fields(element):
                if child_field == CONTENT_TYPE_FIELD and child_wire_type == 0:
                    assert isinstance(value, int)
                    content_type = value
                elif child_field == TEXT_FIELD and child_wire_type == 2:
                    assert isinstance(value, bytes)
                    try:
                        text = value.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
                    if text:
                        text_values.append(text)
            if content_type is not None:
                element_types.append(content_type)
                if content_type == 1:
                    parts.extend(text_values)
    except ProtobufError:
        return ParsedMessage(None, element_types, False)
    return ParsedMessage("".join(parts) or None, element_types, True)


def extract_text(payload: bytes | None) -> str | None:
    return parse_message(payload).text


def classify_message(parsed: ParsedMessage, outer_type: int | None) -> str:
    """Assign exactly one reporting category; specialized media wins mixed rows."""
    types = set(parsed.element_types)
    # No stable, canonical structural rule for poke/pat has been validated yet.
    if 5 in types or (not parsed.parsed and outer_type == 7):
        return "视频"
    if 6 in types or 11 in types or (not parsed.parsed and outer_type == 17):
        return "表情包"
    if 2 in types:
        return "图片"
    if parsed.text:
        return "文字"
    return "其他"


def sender_key(nt_uid: str | None, uin: int | None) -> str | None:
    if nt_uid:
        return f"nt:{nt_uid}"
    if uin:
        return f"uin:{uin}"
    return None


def valid_nickname(nickname: str | None) -> str | None:
    value = (nickname or "").strip()
    return value if value and "�" not in value else None


def display_sender(nt_uid: str | None, uin: int | None, nickname: str | None) -> str:
    name = valid_nickname(nickname)
    if name:
        identity = str(uin) if uin else (nt_uid or "未知")
        return f"{name}（{identity}）"
    if uin:
        return str(uin)
    return nt_uid or "未知发送者"


def display_sender_info(info: SenderInfo) -> str:
    return display_sender(info.nt_uid, info.uin, info.nickname)


def markdown_text(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def markdown_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def is_emoji_base(codepoint: int) -> bool:
    return any(start <= codepoint <= end for start, end in EMOJI_RANGES)


def is_modifier(codepoint: int) -> bool:
    return 0x1F3FB <= codepoint <= 0x1F3FF


def is_regional_indicator(codepoint: int) -> bool:
    return 0x1F1E6 <= codepoint <= 0x1F1FF


def emoji_component_end(text: str, start: int) -> int | None:
    if start >= len(text) or not is_emoji_base(ord(text[start])):
        return None
    end = start + 1
    if end < len(text) and ord(text[end]) in VARIATION_SELECTORS:
        end += 1
    if end < len(text) and is_modifier(ord(text[end])):
        end += 1
    return end


def emoji_sequence_end(text: str, start: int) -> int | None:
    codepoint = ord(text[start])

    if text[start] in "#*0123456789":
        end = start + 1
        if end < len(text) and ord(text[end]) == 0xFE0F:
            end += 1
        return end + 1 if end < len(text) and ord(text[end]) == KEYCAP else None

    if is_regional_indicator(codepoint):
        if start + 1 < len(text) and is_regional_indicator(ord(text[start + 1])):
            return start + 2
        return None

    if codepoint == BLACK_FLAG:
        end = start + 1
        while end < len(text) and 0xE0020 <= ord(text[end]) <= 0xE007E:
            end += 1
        if end > start + 1 and end < len(text) and ord(text[end]) == CANCEL_TAG:
            return end + 1

    end = emoji_component_end(text, start)
    if end is None:
        return None
    while end < len(text) and ord(text[end]) == ZWJ:
        next_end = emoji_component_end(text, end + 1)
        if next_end is None:
            break
        end = next_end
    return end


def iter_emoji_sequences(text: str) -> Iterator[str]:
    index = 0
    while index < len(text):
        end = emoji_sequence_end(text, index)
        if end is None:
            index += 1
            continue
        yield text[index:end]
        index = end


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def top_items(counter: Counter[str], limit: int = 10) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]


def month_keys() -> list[str]:
    keys: list[str] = []
    year, month = START_LOCAL.year, START_LOCAL.month
    while (year, month) <= (2026, 7):
        keys.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return keys


def export(database: Path, output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    start_ts = int(START_LOCAL.timestamp())
    end_ts = int(END_LOCAL.timestamp())
    counters = {"rows": 0, "exported": 0, "non_text": 0, "system_or_unknown_sender": 0, "parse_errors": 0}
    monthly_counts: dict[str, int] = {}
    stats = Statistics(
        Counter(), defaultdict(set), {}, Counter(), Counter(), defaultdict(Counter),
        Counter(), Counter(), Counter(), Counter(),
    )

    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    query = '''
        SELECT "40001", "40003", "40011", "40020", "40033", "40093", "40050", "40800"
        FROM group_msg_table
        WHERE "40027" = ? AND "40050" >= ? AND "40050" < ?
        ORDER BY "40050", "40003", "40001"
    '''
    try:
        with ExitStack() as stack:
            files = {
                month: stack.enter_context((output_dir / f"{month}.md").open("w", encoding="utf-8", newline="\n"))
                for month in month_keys()
            }
            for month, handle in files.items():
                handle.write(f"# 群 {GROUP_ID} 纯文字聊天记录：{month}\n\n")

            for msg_id, msg_seq, outer_type, nt_uid, uin, nickname, timestamp, payload in connection.execute(query, (GROUP_ID, start_ts, end_ts)):
                counters["rows"] += 1
                parsed = parse_message(payload)
                category = classify_message(parsed, outer_type)
                stats.message_categories[category] += 1
                stats.element_types.update(parsed.element_types)
                if len(set(parsed.element_types)) > 1:
                    stats.mixed_type_patterns[tuple(sorted(set(parsed.element_types)))] += 1
                if not payload:
                    stats.diagnostics["空消息体"] += 1
                elif not parsed.parsed:
                    stats.diagnostics["消息体解析失败"] += 1
                if any(element_type not in CONTENT_TYPES for element_type in parsed.element_types):
                    stats.diagnostics["包含未知元素类型的消息"] += 1
                if 8 in parsed.element_types:
                    stats.diagnostics["未验证的 GrayTip 消息"] += 1
                if 2 in parsed.element_types:
                    stats.diagnostics["可能包含收藏表情的 PicElement 消息"] += 1

                key = sender_key(nt_uid, uin)
                if key is None:
                    counters["system_or_unknown_sender"] += 1
                    continue

                local_time = dt.datetime.fromtimestamp(timestamp, UTC8)
                month = local_time.strftime("%Y-%m")
                stats.sender_messages[key] += 1
                stats.sender_days[key].add(local_time.date())
                info = stats.senders.setdefault(key, SenderInfo())
                if nt_uid:
                    info.nt_uid = nt_uid
                if uin:
                    info.uin = uin
                name = valid_nickname(nickname)
                if name:
                    info.nickname = name

                text = parsed.text
                if not text:
                    counters["non_text"] += 1
                    continue

                text = normalize_text(text)
                stats.exact_texts[text] += 1
                for emoji in iter_emoji_sequences(text):
                    stats.emojis[emoji] += 1
                    stats.monthly_emojis[month][emoji] += 1

                files[month].write(
                    f"## {local_time:%Y-%m-%d %H:%M:%S} · {display_sender(nt_uid, uin, nickname)}\n\n"
                    f"{markdown_text(text)}\n\n"
                    f"<!-- msg_id={msg_id}; msg_seq={msg_seq} -->\n\n"
                )
                counters["exported"] += 1
                monthly_counts[month] = monthly_counts.get(month, 0) + 1
    finally:
        connection.close()

    write_statistics(output_dir, stats, counters)
    write_readme(output_dir, counters, monthly_counts)
    return counters


def sender_rankings(stats: Statistics, by_days: bool = False) -> list[tuple[str, int]]:
    metric = {key: len(days) for key, days in stats.sender_days.items()} if by_days else stats.sender_messages
    return sorted(metric.items(), key=lambda item: (-item[1], item[0]))[:10]


def emoji_codepoints(emoji: str) -> str:
    return " ".join(f"U+{ord(char):04X}" for char in emoji)


def category_confidence(category: str) -> str:
    return {
        "文字": "高",
        "表情包": "高置信下界；不含未识别的收藏表情",
        "图片": "高；但可能包含收藏表情",
        "视频": "高",
        "拍一拍": "尚无通过验证的结构匹配",
        "其他": "兜底类别",
    }[category]


def write_category_statistics(lines: list[str], stats: Statistics, counters: dict[str, int]) -> None:
    total = sum(stats.message_categories.values())
    if total != counters["rows"]:
        raise AssertionError(f"message category total {total} != source rows {counters['rows']}")

    lines.extend([
        "## 消息类别统计", "",
        "以下是互斥的消息行统计：每条数据库记录只归入一个主类别，因此总数等于范围内全部原始记录。",
        "混合消息按固定优先级归类：拍一拍 > 视频 > 表情包 > 图片 > 文字 > 其他。", "",
        "| 类别 | 条数 | 比例 | 置信度/说明 |", "|---|---:|---:|---|",
    ])
    for category in MESSAGE_CATEGORIES:
        count = stats.message_categories[category]
        lines.append(f"| {category} | {count:,} | {count / total:.2%} | {category_confidence(category)} |")
    lines.append(f"| **总计** | **{total:,}** | **100.00%** | — |")
    lines.extend([
        "",
        "> **重要限制：** QQFace 和 MarketFace 可明确计入表情包；收藏表情与普通图片都使用 PicElement，当前无法可靠完全区分。因此“表情包”是保守下界，“图片”可能包含部分收藏表情。“拍一拍”没有可靠验证的独立类型，只有结构验证通过才会计入；本次未验证的 GrayTip 均归入“其他”。",
        "", "### 元素出现次数（可重叠）", "",
        "一条混合消息可以包含多个元素，因此下表次数不能与消息行数直接相加。", "",
        "| 内容类型 | 元素次数 |", "|---|---:|",
    ])
    for element_type, count in sorted(stats.element_types.items(), key=lambda item: (-item[1], item[0])):
        name = CONTENT_TYPES.get(element_type, "未知")
        lines.append(f"| `{element_type}` {name} | {count:,} |")

    lines.extend(["", "### 分类诊断", "", "| 诊断项 | 消息数 |", "|---|---:|"])
    for name in ("空消息体", "消息体解析失败", "包含未知元素类型的消息", "未验证的 GrayTip 消息", "可能包含收藏表情的 PicElement 消息"):
        lines.append(f"| {name} | {stats.diagnostics[name]:,} |")
    lines.append("")


def write_statistics(output_dir: Path, stats: Statistics, counters: dict[str, int]) -> None:
    lines = [
        f"# QQ 群 {GROUP_ID} 聊天统计", "",
        "## 统计口径", "",
        "- 时间：2025-07-27 00:00:00 至 2026-07-26 23:59:59，北京时间（UTC+8）。",
        f"- 用户发言榜基于全部具有明确发送者的原始消息，共 {sum(stats.sender_messages.values()):,} 条；包括图片、视频等非文字消息。",
        f"- 文字消息和 emoji 榜基于成功提取的 {counters['exported']:,} 条 TextElement 文字消息。",
        "- 相同消息按 Unicode NFC 规范化后的完整正文统计，保留大小写、标点及内部空白差异。",
        "- emoji 按完整 Unicode 序列的出现次数统计；肤色、旗帜和 ZWJ 组合分别计为完整的一个 emoji。", "",
    ]
    write_category_statistics(lines, stats, counters)
    lines.extend([
        "## 发言最多的用户前 10", "", "| 排名 | 用户 | 消息数 |", "|---:|---|---:|",
    ])
    for rank, (key, count) in enumerate(sender_rankings(stats), 1):
        lines.append(f"| {rank} | {markdown_cell(display_sender_info(stats.senders[key]))} | {count:,} |")

    lines.extend(["", "## 发言天数最多的用户前 10", "", "| 排名 | 用户 | 发言天数 |", "|---:|---|---:|"])
    for rank, (key, days) in enumerate(sender_rankings(stats, by_days=True), 1):
        lines.append(f"| {rank} | {markdown_cell(display_sender_info(stats.senders[key]))} | {days:,} |")

    lines.extend(["", "## 最常发送的文字消息前 10", "", "| 排名 | 完整消息 | 次数 |", "|---:|---|---:|"])
    for rank, (text, count) in enumerate(top_items(stats.exact_texts), 1):
        lines.append(f"| {rank} | {markdown_cell(text)} | {count:,} |")

    lines.extend(["", "## 最常使用的 emoji 前 10", "", "| 排名 | Emoji | Unicode 码点 | 次数 |", "|---:|:---:|---|---:|"])
    for rank, (emoji, count) in enumerate(top_items(stats.emojis), 1):
        lines.append(f"| {rank} | {emoji} | `{emoji_codepoints(emoji)}` | {count:,} |")

    lines.extend(["", "## 每个月最常使用的 emoji 前 10", ""])
    for month in month_keys():
        lines.extend([f"### {month}", "", "| 排名 | Emoji | Unicode 码点 | 次数 |", "|---:|:---:|---|---:|"])
        ranking = top_items(stats.monthly_emojis[month])
        if ranking:
            for rank, (emoji, count) in enumerate(ranking, 1):
                lines.append(f"| {rank} | {emoji} | `{emoji_codepoints(emoji)}` | {count:,} |")
        else:
            lines.append("| — | — | — | 0 |")
        lines.append("")

    (output_dir / "STATISTICS.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_readme(output_dir: Path, counters: dict[str, int], monthly_counts: dict[str, int]) -> None:
    lines = [
        f"# QQ 群 {GROUP_ID} 纯文字聊天记录", "", "## 导出范围", "",
        "- 时间：2025-07-27 00:00:00 至 2026-07-26 23:59:59",
        "- 时区：北京时间（UTC+8）",
        "- 数据源：`nt_msg_plain.db` 的 `group_msg_table`",
        "- 内容：仅导出能够明确识别的 TextElement 正文；原生 emoji 属于文字，会原样保留", "",
        "## 汇总统计", "", "- [查看发言、文字消息和 emoji 排行](STATISTICS.md)",
        "- 消息类别统计（文字、表情包、图片、视频、拍一拍及其他）也在 `STATISTICS.md` 中。",
        "- 用户榜基于全部具有明确发送者的消息；文字与 emoji 榜基于已导出的纯文字消息。", "",
        "## 导出结果", "",
        f"- 范围内原始记录：{counters['rows']:,} 条",
        f"- 已导出纯文字记录：{counters['exported']:,} 条",
        f"- 无可识别纯文字正文：{counters['non_text']:,} 条",
        f"- 系统或无法确认发送者：{counters['system_or_unknown_sender']:,} 条",
        f"- Protobuf 解析失败：{counters['parse_errors']:,} 条", "", "## 月度文件", "",
        "| 文件 | 纯文字记录数 |", "|---|---:|",
    ]
    for month in month_keys():
        lines.append(f"| [{month}.md]({month}.md) | {monthly_counts.get(month, 0):,} |")
    lines.extend(["", "## 限制", "",
        "- 本次不导出图片、视频、收藏表情、商城表情及其他非文字消息。",
        "- 回复引用中的旧消息文字不会作为新消息正文重复导出。",
        "- 数据库中部分昵称已经是不可逆的乱码，此时仅显示 QQ 号或 nt_uid。",
        "- 每条记录末尾的 HTML 注释保存消息 ID 和群消息序号，Markdown 阅读器通常不会显示。", ""])
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def self_test() -> None:
    def encode_varint(value: int) -> bytes:
        result = bytearray()
        while True:
            byte = value & 0x7F
            value >>= 7
            result.append(byte | (0x80 if value else 0))
            if not value:
                return bytes(result)

    def field(number: int, value: bytes) -> bytes:
        return encode_varint((number << 3) | 2) + encode_varint(len(value)) + value

    text = "测试🙂\n第二行"
    payload = field(ELEMENT_FIELD, encode_varint((CONTENT_TYPE_FIELD << 3) | 0) + encode_varint(1) + field(TEXT_FIELD, text.encode("utf-8")))
    parsed_text = parse_message(payload)
    assert parsed_text.text == text
    assert classify_message(parsed_text, 2) == "文字"

    def typed_element(content_type: int, extra: bytes = b"") -> bytes:
        body = encode_varint((CONTENT_TYPE_FIELD << 3) | 0) + encode_varint(content_type) + extra
        return field(ELEMENT_FIELD, body)

    assert classify_message(parse_message(typed_element(2)), 2) == "图片"
    assert classify_message(parse_message(typed_element(5)), 7) == "视频"
    assert classify_message(parse_message(typed_element(6)), 2) == "表情包"
    assert classify_message(parse_message(typed_element(11)), 17) == "表情包"
    mixed = parse_message(payload + typed_element(2))
    assert classify_message(mixed, 2) == "图片"
    assert classify_message(parse_message(typed_element(99)), 2) == "其他"
    assert classify_message(parse_message(b"\x80"), 7) == "视频"

    quoted = field(ELEMENT_FIELD, encode_varint((CONTENT_TYPE_FIELD << 3) | 0) + encode_varint(7) + field(47423, field(TEXT_FIELD, "引用".encode("utf-8"))))
    assert extract_text(quoted) is None
    assert extract_text(b"\x80") is None
    assert sender_key("u_demo", 123) == "nt:u_demo"
    assert sender_key(None, 123) == "uin:123"
    assert sender_key(None, 0) is None
    assert normalize_text("e\u0301") == "é"
    assert markdown_cell("a|b\\c\nd") == "a\\|b\\\\c<br>d"
    sample = "A🙂👍🏽1️⃣🇨🇳👩‍💻🙂B"
    assert list(iter_emoji_sequences(sample)) == ["🙂", "👍🏽", "1️⃣", "🇨🇳", "👩‍💻", "🙂"]
    assert list(iter_emoji_sequences("1#*\ufe0f\u200d🇨")) == []
    assert top_items(Counter({"b": 2, "a": 2, "c": 1})) == [("a", 2), ("b", 2), ("c", 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="导出指定 QQ 群的纯文字聊天记录和统计")
    parser.add_argument("--database", type=Path, default=Path("nt_msg_plain.db"))
    parser.add_argument("--output", type=Path, default=Path("group_808688505_text_2025-07-27_to_2026-07-26"))
    args = parser.parse_args()
    self_test()
    counters = export(args.database.resolve(), args.output.resolve())
    print(f"导出完成：原始 {counters['rows']:,} 条，纯文字 {counters['exported']:,} 条，已生成统计文件")


if __name__ == "__main__":
    main()
