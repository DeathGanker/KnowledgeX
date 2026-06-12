"""清理：根据 state 更新收件箱原文件 —— 删除已处理链接，给失败/跳过的加注释"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from scan import URL_PATTERN
from state import ItemRecord


# 机器生成的临时收件箱文件：全部链接处理完后整文件自动删除（无需用户手动清理）。
# 目前是「缺口补全」（全库问答补缺口生成）。手写的收件箱文件不在此列，只做行级清理。
def _is_transient(name: str) -> bool:
    return "缺口补全" in name


def _annotate_line(line: str, comment: str) -> str:
    stripped = line.rstrip("\n")
    # 已有同类注释就不重复加
    if comment in stripped:
        return line if line.endswith("\n") else line + "\n"
    suffix = "" if line.endswith("\n") else "\n"
    return f"{stripped}  {comment}{suffix}"


def apply_cleanup(vault_root: Path, items: dict[str, ItemRecord]) -> None:
    """按 source_file 分组，每个文件一次性重写。"""
    by_file: dict[str, list[ItemRecord]] = defaultdict(list)
    for rec in items.values():
        if rec.source_file and rec.raw_line:
            by_file[rec.source_file].append(rec)

    for rel_path, records in by_file.items():
        file_path = vault_root / rel_path
        if not file_path.exists():
            continue
        original = file_path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        if not original.endswith("\n") and lines:
            # 确保后续 join 不丢内容
            pass

        # 把记录按 url 长度倒序，避免短 URL 误匹配长 URL 的子串
        records_sorted = sorted(records, key=lambda r: len(r.url or ""), reverse=True)

        new_lines: list[str] = []
        consumed_keys: set[str] = set()  # 防止同一记录消费多次
        for line in lines:
            handled = False
            for rec in records_sorted:
                if rec.url not in line or rec.url in consumed_keys:
                    continue
                if rec.status == "processed":
                    # 整行删除（同时也不要保留空白残骸）
                    handled = True
                    consumed_keys.add(rec.url)
                    break
                if rec.status == "failed":
                    new_lines.append(_annotate_line(line, f"<!-- ❌ 抓取失败: {rec.error or '未知错误'} -->"))
                    handled = True
                    consumed_keys.add(rec.url)
                    break
                if rec.status == "skipped":
                    new_lines.append(_annotate_line(line, f"<!-- ⏭ 跳过: {rec.error or '不受支持'} -->"))
                    handled = True
                    consumed_keys.add(rec.url)
                    break
            if not handled:
                new_lines.append(line)

        new_text = "".join(new_lines)

        # 临时文件（缺口补全）：若已无待处理链接（失败/跳过的行仍含 URL → 会保留），整文件删除。
        if _is_transient(file_path.name) and not URL_PATTERN.search(new_text):
            file_path.unlink()
            continue

        # 收尾：如果文件被清空（只剩纯空行），保留一行说明
        if not new_text.strip():
            new_text = "<!-- 收件箱当前为空 -->\n"
        file_path.write_text(new_text, encoding="utf-8")
