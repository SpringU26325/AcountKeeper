"""用户标签偏好：维护 tags.json 中唯一的、有序标签列表。

偏好单独存文件而不并入 settings.json：settings 写入时会整份覆盖，混放标签会被
导出路径更新洗掉。旧版 categories.json 只在首次初始化时读取，不修改、不删除。
"""

from __future__ import annotations

import json
from typing import Callable, Iterable

from config import CATEGORY_PREFS_PATH, DEFAULT_TAGS, TAG_PREFS_PATH

_SCHEMA_VERSION = 1


def _warn(message: str) -> None:
    """偏好文件问题只警告并降级，不让配置故障阻断程序启动。"""
    print(f"警告：{message}")


def _clean_list(value: object, field: str) -> list[str]:
    """清理列表并按首次出现顺序去重，保证候选顺序就是用户看到的顺序。"""
    if not isinstance(value, list):
        _warn(f"{field} 不是列表，本次按空列表处理。")
        return []

    cleaned: list[str] = []
    dropped = 0
    for item in value:
        if not isinstance(item, str) or not item.strip():
            dropped += 1
            continue
        tag = item.strip()
        if tag not in cleaned:
            cleaned.append(tag)
    if dropped:
        _warn(f"{field} 中有 {dropped} 项不是合法标签名，已忽略。")
    return cleaned


def _read_tags_document() -> dict | None:
    """读取 tags.json；损坏或格式不符时返回 None，写操作可随后恢复标准结构。"""
    if not TAG_PREFS_PATH.exists():
        return None
    try:
        with TAG_PREFS_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        _warn(f"{TAG_PREFS_PATH.name} 读取或解析失败（{error}），本次按空列表处理。")
        return None
    if not isinstance(raw, dict):
        _warn(f"{TAG_PREFS_PATH.name} 根节点不是对象，本次按空列表处理。")
        return None
    if raw.get("version") != _SCHEMA_VERSION:
        # 版本号只写不校验；只要 tags 列表结构仍可读，就不因版本字段阻断候选。
        _warn(f"{TAG_PREFS_PATH.name} 格式版本为 {raw.get('version')!r}，仍尝试读取标签列表。")
    if not isinstance(raw.get("tags"), list):
        _warn(f"{TAG_PREFS_PATH.name} 格式不合法，本次按空列表处理。")
        return None
    return raw


def _read_legacy_categories() -> tuple[list[str], list[str]] | None:
    """只读旧 categories.json；文件损坏或根结构不合法时返回 None 以走默认初始化。"""
    try:
        with CATEGORY_PREFS_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        _warn(f"{CATEGORY_PREFS_PATH.name} 读取或解析失败（{error}），将使用预置与历史标签。")
        return None
    if (
        not isinstance(raw, dict)
        or raw.get("version") != 1
        or not isinstance(raw.get("expense"), list)
        or not isinstance(raw.get("income"), list)
    ):
        _warn(f"{CATEGORY_PREFS_PATH.name} 格式不合法，将使用预置与历史标签。")
        return None
    return (
        _clean_list(raw["expense"], "categories.expense"),
        _clean_list(raw["income"], "categories.income"),
    )


def _tags_list(document: dict) -> list[str]:
    """取新格式的单段标签列表；脏项按现有偏好层规则清理。"""
    return _clean_list(document.get("tags"), "tags")


def build_tag_candidates() -> list[str]:
    """返回 tags.json 当前列表；每次现读，确保弹窗内修改立即反映。"""
    document = _read_tags_document()
    if document is None:
        return []
    return _tags_list(document)


def _write_document(document: dict) -> bool:
    """将标准化后的单段结构写回 tags.json。"""
    payload = {"version": _SCHEMA_VERSION, "tags": _tags_list(document)}
    try:
        # 用户可能移动或删除数据目录，写入前补建目录以便偏好功能自行恢复。
        TAG_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TAG_PREFS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError as error:
        _warn(f"{TAG_PREFS_PATH.name} 写入失败（{error}），本次操作未保存。")
        return False
    return True


def _apply(name: str, change: Callable[[str, list[str]], bool]) -> bool:
    """读单段列表、执行变更并写回；目标状态未改变时跳过无意义的文件重写。"""
    cleaned = (name or "").strip()
    if not cleaned:
        _warn("标签名为空，本次操作未保存。")
        return False

    document = _read_tags_document()
    tags = [] if document is None else _tags_list(document)
    if not change(cleaned, tags):
        return True
    return _write_document({"tags": tags})


def save_tag(name: str) -> bool:
    """追加标签；已存在时保持原顺序并视为成功。"""
    def change(tag: str, tags: list[str]) -> bool:
        if tag in tags:
            return False
        tags.append(tag)
        return True

    return _apply(name, change)


def delete_tag(name: str) -> bool:
    """真删除标签；标签本已不存在时幂等成功且不重写文件。"""
    def change(tag: str, tags: list[str]) -> bool:
        if tag not in tags:
            return False
        tags.remove(tag)
        return True

    return _apply(name, change)


def move_tag_to_top(name: str) -> bool:
    """把标签移到列表首位，保留其余标签的相对顺序。"""
    def change(tag: str, tags: list[str]) -> bool:
        if tag not in tags or tags[0] == tag:
            return False
        # 先移除再插入首位，避免交换操作打乱其它标签原有的相对顺序。
        tags.remove(tag)
        tags.insert(0, tag)
        return True

    return _apply(name, change)


def ensure_tags_initialized(history: Iterable[str] | None) -> bool:
    """只初始化一次；返回 True 表示本次写入了 tags.json。

    tags.json 存在就是已初始化标记，不读取旧文件或数据库历史。旧文件有效时先并入
    expense 全段，再按原序追加 income 中的新项；旧文件非法时视作不存在，转用预置与
    调用方传入的历史标签。此处只接收历史参数，不导入 store，保持偏好层无数据库依赖。
    """
    if TAG_PREFS_PATH.exists():
        return False

    legacy: tuple[list[str], list[str]] | None = None
    if CATEGORY_PREFS_PATH.exists():
        legacy = _read_legacy_categories()
    if legacy is not None:
        expense, income = legacy
        # expense 保留完整原序；income 只补未出现的名字，合并结果不排序。
        tags = _clean_list(expense + income, "legacy.tags")
    else:
        history_tags = _clean_list(list(history or ()), "history")
        # 只有没有可用旧文件时才播种预置与数据库历史，且按首次出现顺序去重。
        tags = _clean_list(list(DEFAULT_TAGS) + history_tags, "tags")
    return _write_document({"tags": tags})