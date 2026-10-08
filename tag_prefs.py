"""用户标签偏好：维护 tags.json 中唯一的、有序标签列表。

偏好单独存文件而不并入 settings.json：settings 写入时会整份覆盖，混放标签会被
导出路径更新洗掉。旧版 categories.json 只在首次初始化时读取，不修改、不删除。
这里的顺序是常用候选的用户顺序，不是账本重载后的标签字典序；不查库，也不缓存候选。
保存、删除、置顶只改变常用列表，不修改已记账记录；手输文本拆分也不自动保存候选。
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
        # JSON 脏项只从本次返回结果排除，读取本身不修写文件；保留其余可用候选。
        if not isinstance(item, str) or not item.strip():
            dropped += 1
            continue
        tag = item.strip()
        if tag not in cleaned:
            cleaned.append(tag)
    if dropped:
        _warn(f"{field} 中有 {dropped} 项不是合法标签名，已忽略。")
    return cleaned


def split_tag_input(text: str) -> tuple[str, ...]:
    """新增区和编辑区共用的纯拆分，返回按首次出现去重的完整标签元组，不读写文件。

    手输多标签按顿号分隔；CSV 标签字段另用竖线，本函数不用于导出回读或解析表格摘要。
    清理规则与候选列表一致，但输入尾部多一个顿号是正常操作，不按文件脏项发警告。
    """
    cleaned: list[str] = []
    # 先去空再判重，允许 0 标签；不截断名称，结果供 chips 收集完整输入。
    for part in (text or "").split("、"):
        tag = part.strip()
        if tag and tag not in cleaned:
            cleaned.append(tag)
    return tuple(cleaned)


def _read_tags_document() -> dict | None:
    """只读 tags.json；缺失或结构不可读返回 None，是否重写交给显式变更入口。"""
    if not TAG_PREFS_PATH.exists():
        return None
    try:
        with TAG_PREFS_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        # 候选读取故障降为空列表，不让可选的常用标签功能阻断记账；这里不覆盖原文件。
        _warn(f"{TAG_PREFS_PATH.name} 读取或解析失败（{error}），本次按空列表处理。")
        return None
    if not isinstance(raw, dict):
        _warn(f"{TAG_PREFS_PATH.name} 根节点不是对象，本次按空列表处理。")
        return None
    if raw.get("version") != _SCHEMA_VERSION:
        # 版本不匹配只警告；只要 tags 列表结构仍可读，就不因版本字段阻断候选。
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
    document = _read_tags_document()  # 每次从唯一偏好列表取值，不再合并历史标签复活用户已删候选。
    if document is None:
        return []
    return _tags_list(document)


def _write_document(document: dict) -> bool:
    """只写 version/tags 标准结构；直接整份覆盖，未采用导入映射的临时文件原子替换。"""
    payload = {"version": _SCHEMA_VERSION, "tags": _tags_list(document)}
    try:
        # 用户可能移动或删除数据目录，写入前补建目录以便偏好功能自行恢复。
        TAG_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TAG_PREFS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError as error:
        # 返回 False 只表示未能保存，直接写入失败并不承诺原文件仍完整；由调用方反馈结果。
        _warn(f"{TAG_PREFS_PATH.name} 写入失败（{error}），本次操作未保存。")
        return False
    return True


def _apply(name: str, change: Callable[[str, list[str]], bool]) -> bool:
    """对现读列表应用变更；True 表示成功或无需变更，不保证本次实际写过文件。"""
    cleaned = (name or "").strip()
    if not cleaned:
        _warn("标签名为空，本次操作未保存。")
        return False

    document = _read_tags_document()
    tags = [] if document is None else _tags_list(document)
    # change 的 False 表示列表未变，不是操作失败；只有有效变更才重写标准结构。
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
    """从常用候选真删除，不删除账本标签；本已不存在时幂等成功且不重写文件。"""
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
    # 存在即跳过，即使文件损坏或用户清空也不重新播种，以免常用列表被静默恢复。
    if TAG_PREFS_PATH.exists():
        return False

    legacy: tuple[list[str], list[str]] | None = None
    if CATEGORY_PREFS_PATH.exists():
        legacy = _read_legacy_categories()
    if legacy is not None:
        # 合法但两段都空的旧文件也代表用户选择，不能以空列表为由改用默认标签。
        expense, income = legacy
        # expense 保留完整原序；income 只补未出现的名字，合并结果不排序。
        tags = _clean_list(expense + income, "legacy.tags")
    else:
        history_tags = _clean_list(list(history or ()), "history")
        # 只有没有可用旧文件时才播种预置与数据库历史，且按首次出现顺序去重。
        tags = _clean_list(list(DEFAULT_TAGS) + history_tags, "tags")
    return _write_document({"tags": tags})
