"""用户标签偏好：维护 tags.json 的常用列表与按收支分组的新增记忆。

偏好单独存文件而不并入 settings.json：settings 写入时会整份覆盖，混放标签会被
导出路径更新洗掉。旧版 categories.json 只在首次初始化时读取，不修改、不删除。
这里的顺序是常用候选的用户顺序，不是账本重载后的标签字典序；不查库，也不缓存候选。
保存、删除、置顶只改变常用列表，不修改已记账记录；手输文本拆分也不自动保存候选。
"""

from __future__ import annotations

import atexit
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
import threading
from time import sleep
from typing import Callable, Iterable

from config import CATEGORY_PREFS_PATH, DEFAULT_TAGS, TAG_PREFS_PATH

_SCHEMA_VERSION = 1

# 收支方向与 tags.json 字段名的唯一映射；未登记的方向一律拒绝，避免跨方向串写。
_DIRECTION_KEYS = {"支出": "expense", "收入": "income"}
_KEY_DIRECTIONS = {key: direction for direction, key in _DIRECTION_KEYS.items()}


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


def _remembered_tags(document: dict) -> dict[str, list[str]]:
    """仅识别明确的收支分组，旧文件或非法字段不借另一方向兜底。"""
    raw = document.get("last_used_tags")
    if not isinstance(raw, dict):
        return {}
    return {key: _clean_list(raw[key], f"last_used_tags.{key}")
            for key in ("expense", "income") if key in raw}


def load_remembered_tags(amount_type: str) -> tuple[str, ...]:
    """读取新增方向的完整标签；缺失或损坏均返回空元组，不修写原文件。"""
    key = _DIRECTION_KEYS.get(amount_type)
    document = _read_tags_document()
    # 未知方向绝不能被当成默认支出，避免调用方错误造成跨方向回填。
    return tuple(_remembered_tags(document).get(key, ())) if document and key else ()


def save_remembered_tags(amount_type: str, tags: tuple[str, ...]) -> bool:
    """只更新指定方向；空元组代表用户明确取消该方向的默认记忆。

    这是同步入口，供"清空记忆"这类需要立刻知道成败的路径使用；新增记录的
    连续写盘走 queue_remembered_tags，主线程不再等磁盘。
    """
    key = _DIRECTION_KEYS.get(amount_type)
    if key is None:
        return False
    # 同步值优先：先作废该方向排队中的后台批次，避免旧值晚到覆盖新值。
    _supersede(key)
    # 不让一次可选记忆写入覆盖损坏文件内尚可人工恢复的常用列表。
    document: dict[str, object] | None = _read_tags_document()
    if document is None:
        if TAG_PREFS_PATH.exists():
            return False
        document = {"tags": []}
    remembered = _remembered_tags(document)
    remembered[key] = _clean_list(list(tags), f"last_used_tags.{key}")
    document["last_used_tags"] = remembered
    return _write_document(document)


def _replace_tag_file(temporary: Path) -> None:
    """只对短暂的 Windows 替换拒绝有限等待，持续失败仍交给保存入口反馈。"""
    for attempt in range(3):
        try:
            temporary.replace(TAG_PREFS_PATH)
            return
        except OSError as error:
            # 拒绝访问也可能是永久权限问题，最多三次；其他错误不应靠等待掩盖。
            if getattr(error, "winerror", None) not in (5, 32) or attempt == 2:
                raise
            # 仅复用已关闭的完整临时文件，累计等待150毫秒，不重复修改标签内容。
            sleep((0.05, 0.1)[attempt])


def _write_document(document: dict) -> bool:
    """共同保存候选与方向记忆，临时文件替换避免写入失败截断原偏好。"""
    # 同步入口与后台线程可能同时写同一个文件，临时文件替换必须串行化。
    with _file_lock:
        return _write_document_locked(document)


def _write_document_locked(document: dict) -> bool:
    payload: dict = {"version": _SCHEMA_VERSION, "tags": _tags_list(document)}
    if "last_used_tags" in document:
        payload["last_used_tags"] = _remembered_tags(document)
    temporary: Path | None = None
    try:
        # 用户可能移动或删除数据目录，写入前补建目录以便偏好功能自行恢复。
        TAG_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=TAG_PREFS_PATH.parent,
                                prefix=".tags-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        _replace_tag_file(temporary)  # 关闭句柄后再替换，失败时仍保留原偏好和清理临时文件。
    except OSError as error:
        # 保留原偏好，失败结果交给当前操作反馈，不能冒充已经记住或清除了标签。
        _warn(f"{TAG_PREFS_PATH.name} 写入失败（{error}），本次操作未保存。")
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                _warn(f"临时标签文件清理失败（{error}）。")  # 清理失败不覆盖实际保存结果。
    return True


# ---------- 后台写盘（#87 输入区卡顿） ----------
# 每添加一笔都要更新"沿用上次"，而 tags.json 的临时文件替换在 Windows 上可能被
# 杀软占用并重试 50/100ms（见 _replace_tag_file）；这段等待绝不能压在 UI 主线程上。
_file_lock = threading.RLock()  # 串行化全部写入；可重入，供 _commit_batch 内部复用
_pending: dict[str, tuple[tuple[str, ...], int]] = {}  # 方向 -> (待写标签, 版本号)
_generation: dict[str, int] = {}  # 方向 -> 最新版本号，用于丢弃过期批次
_pending_cond = threading.Condition()  # 保护上面两个字典并唤醒写线程
_writer: threading.Thread | None = None


def _supersede(key: str) -> None:
    """登记新版本并丢掉该方向的排队值；后台批次按版本号自检，过期即丢弃。"""
    with _pending_cond:
        _generation[key] = _generation.get(key, 0) + 1
        _pending.pop(key, None)


def queue_remembered_tags(amount_type: str, tags: tuple[str, ...]) -> None:
    """把"沿用上次"交给后台线程写盘：主线程只登记最新值，绝不等待磁盘。

    同一方向连续登记只保留最后一次（防抖），因此连点添加不会产生多次写盘。
    """
    key = _DIRECTION_KEYS.get(amount_type)
    if key is None:
        return
    cleaned = tuple(_clean_list(list(tags), f"last_used_tags.{key}"))
    global _writer
    with _pending_cond:
        _generation[key] = _generation.get(key, 0) + 1
        _pending[key] = (cleaned, _generation[key])
        if _writer is None or not _writer.is_alive():
            # daemon：进程退出不该被可选偏好写入拖住，收尾由 atexit 兜底。
            _writer = threading.Thread(target=_writer_loop, name="tag-prefs-writer", daemon=True)
            _writer.start()
        _pending_cond.notify()


def _writer_loop() -> None:
    """单写线程：取走整批排队值后一次落盘，避免每个方向各写一遍文件。"""
    while True:
        with _pending_cond:
            while not _pending:
                _pending_cond.wait()
            batch = dict(_pending)
            _pending.clear()
        try:
            _commit_batch(batch)
        except Exception as error:  # 后台线程抛异常会静默杀死写盘能力，必须自己接住。
            _warn(f"标签记忆后台写入异常（{error}），本次未保存。")


def _commit_batch(batch: dict[str, tuple[tuple[str, ...], int]]) -> bool:
    """合并写入一批方向记忆；已在排队期间被同步入口取代的键直接丢弃。"""
    with _file_lock:
        with _pending_cond:
            fresh = {key: tags for key, (tags, gen) in batch.items()
                     if _generation.get(key) == gen}
        if not fresh:
            return True  # 全部过期：同步入口已写入更新的值。
        document = _read_tags_document()
        if document is None:
            if TAG_PREFS_PATH.exists():
                _warn(f"{TAG_PREFS_PATH.name} 不可读，本次记忆未写入。")
                return False
            document = {"tags": []}
        remembered = _remembered_tags(document)
        # 队列里存的是 tuple（不可变、便于防抖比较），落盘必须是 list，
        # 否则 _remembered_tags 会把非列表当脏项丢弃，记忆静默变成空列表。
        remembered.update({key: list(tags) for key, tags in fresh.items()})
        document["last_used_tags"] = remembered
        return _write_document(document)


def flush_remembered_tags() -> None:
    """把排队中的记忆立刻落盘；供退出与测试收尾使用，主线程等待可接受。"""
    with _pending_cond:
        batch = dict(_pending)
        _pending.clear()
    if batch:
        _commit_batch(batch)


# 退出时后台线程可能还没跑完，必须在解释器收尾前补写一次，否则最后一次记忆丢失。
atexit.register(flush_remembered_tags)


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
    document = document or {}
    document["tags"] = tags  # 候选增删置顶保留同一文档中的两组记忆。
    return _write_document(document)


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
