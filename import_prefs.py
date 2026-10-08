"""显式保存导入字段映射；不依赖 UI、数据库、settings 或标签偏好。

指纹由规范化列头及次数生成，不含列顺序，也不合并同义词；保存来源用规范化列名。
候选仅要求结构匹配率至少 60%，应用时仍按唯一列名重新定位，不代表已完成交易语义校验。
保存/覆盖/删除才写 import_mappings.json；损坏文件拒绝继续变更，保留现场给调用方处理。
读写入口可注入 path 以隔离测试；写入采用同目录临时文件完整落盘后原子替换。
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Sequence
import unicodedata

from config import IMPORT_MAPPINGS_PATH


class MappingStorageError(ValueError):
    """损坏或不支持的映射文件必须保留现场，不静默覆盖。"""


@dataclass(frozen=True)
class SavedMapping:
    """保存时原表头及角色来源；sources 内是规范化列名，角色顺序及多来源顺序保留。"""
    fingerprint: str
    name: str
    headers: tuple[str, ...]
    sources: dict[str, tuple[str, ...]]  # frozen 仅禁止字段重赋值，不冻结字典；调用方须按只读值使用。


@dataclass(frozen=True)
class MappingCandidate:
    """结构匹配候选；缺失/新增列按出现次数记录，不是合并去重后的列名集合。"""
    mapping: SavedMapping
    matched_count: int
    total_count: int
    missing_headers: tuple[str, ...]
    new_headers: tuple[str, ...]


def normalize_header(value: str) -> str:
    # 仅统一字符形式和首尾空白，不使用字段同义词词表，避免宽松识别污染严格复用。
    return unicodedata.normalize("NFKC", value).strip().casefold()


def header_fingerprint(headers: Sequence[str]) -> str:
    """序列化规范化列头的多重集合；重排列不变，重复列头次数变化则指纹变化。"""
    counts = Counter(normalize_header(header) for header in headers)
    # 排序保证稳定，保留次数以区别同名列结构；结果是可核对的 JSON 字符串，不是哈希摘要。
    return json.dumps(sorted(counts.items()), ensure_ascii=False, separators=(",", ":"))


def _read_mappings(path: Path) -> list[SavedMapping]:
    """严格读取全部映射；只有不存在视为空列表，损坏和其他 I/O 错误不静默降级。"""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []  # 尚未显式保存时不创建文件。
    except UnicodeDecodeError as error:
        # 编码损坏也属于映射文件错误；不尝试替换字符读取，以免下一次保存覆盖原现场。
        raise MappingStorageError("映射文件编码损坏，请保留并检查原文件。") from error
    try:
        document = json.loads(content)
        # bool 和浮点数会与整数 1 比较相等，版本必须先检查类型，再检查受支持的值。
        if (
            not isinstance(document, dict)
            or type(document.get("version")) is not int
            or document["version"] != 1
        ):
            raise MappingStorageError("映射文件版本不支持。")
        items = document.get("mappings")
        if not isinstance(items, list) or len(items) > 20:
            raise MappingStorageError("映射列表不合法。")
        result = []
        fingerprints = set()
        for item in items:
            if not isinstance(item, dict):
                raise MappingStorageError("映射条目不合法。")
            headers, sources = item.get("headers"), item.get("sources")
            name = item.get("name")
            if (
                not isinstance(headers, list) or not headers
                or not all(isinstance(header, str) for header in headers)
                or not isinstance(name, str) or not name.strip()
                or not isinstance(sources, dict)
            ):
                raise MappingStorageError("映射名称、列头或字段不合法。")
            counts = Counter(normalize_header(header) for header in headers)
            cleaned_sources = {}
            for role, values in sources.items():
                # 保存来源必须是规范化列名且在原表头唯一命中，不能把歧义文件读成可复用映射。
                if (
                    not isinstance(role, str) or not isinstance(values, list)
                    or not all(
                        isinstance(value, str) and value and counts[value] == 1
                        for value in values
                    )
                ):
                    raise MappingStorageError("保存映射中存在缺列或同名列歧义。")
                cleaned_sources[role] = tuple(values)
            fingerprint = header_fingerprint(headers)  # 重算核对，不能信任手工修改后的指纹或重复项。
            if item.get("fingerprint") != fingerprint or fingerprint in fingerprints:
                raise MappingStorageError("映射指纹不一致或重复。")
            fingerprints.add(fingerprint)
            result.append(SavedMapping(fingerprint, name, tuple(headers), cleaned_sources))
        return result
    except (json.JSONDecodeError, TypeError) as error:
        # 读取失败必须阻止后续整份写入，不能拿空列表覆盖仍有恢复价值的原文件。
        raise MappingStorageError("映射文件无法解析，请保留并检查原文件。") from error


def list_saved_mappings(path: Path = IMPORT_MAPPINGS_PATH) -> list[SavedMapping]:
    """按保存顺序现读完整映射，不创建文件，也不在内存缓存另一份列表。"""
    return _read_mappings(Path(path))


def find_mapping_candidates(
    headers: Sequence[str], path: Path = IMPORT_MAPPINGS_PATH,
) -> list[MappingCandidate]:
    """返回结构匹配率至少 60% 的候选，保持文件顺序；不自动应用或选中映射。"""
    current = Counter(normalize_header(header) for header in headers)
    candidates = []
    for mapping in _read_mappings(Path(path)):
        saved = Counter(normalize_header(header) for header in mapping.headers)
        # 多重集合交集按较少次数计命中，分母只取保存时列头；当前新增列不稀释原匹配率。
        matched, total = sum((saved & current).values()), sum(saved.values())
        if matched * 5 >= total * 3:  # 整数比较精确覆盖 60% 边界，分母只来自保存表头。
            candidates.append(MappingCandidate(
                mapping, matched, total, tuple((saved - current).elements()),
                tuple((current - saved).elements()),
            ))
    return candidates


def apply_saved_mapping(
    mapping: SavedMapping, headers: Sequence[str],
) -> dict[str, tuple[int, ...]]:
    """按当前表头重新生成零基列索引；缺失/歧义来源排除，角色可返回空元组。"""
    normalized = tuple(normalize_header(header) for header in headers)
    counts = Counter(normalized)
    # 只保留唯一命中的来源；多来源角色可部分命中，不等于角色已通过后续必要字段校验。
    # 列重排后按名字重新定位，新增列不自动分角色，不能沿用保存时的旧位置索引。
    return {
        role: tuple(normalized.index(source) for source in sources if counts[source] == 1)
        for role, sources in mapping.sources.items()
    }


def _write_mappings(path: Path, mappings: list[SavedMapping]) -> None:
    """原子替换标准映射文件；不做失败降级或并发合并，保存错误由调用方处理。"""
    document = {"version": 1, "mappings": [
        {"fingerprint": item.fingerprint, "name": item.name, "headers": item.headers,
         "sources": item.sources}
        for item in mappings
    ]}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # 同目录完整写入后再原子替换，磁盘／发布失败不能损坏上一份映射。
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".import_mappings-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2)
            # 先刷新 Python 缓冲并同步文件，关闭句柄后替换，避免发布半份 JSON 或 Windows 占用错误。
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            # 只清本次创建的临时文件；替换成功它已不存在，失败时也不删除旧映射文件。
            temporary.unlink(missing_ok=True)


def save_mapping(
    headers: Sequence[str], sources: dict[str, tuple[int, ...]], name: str | None = None,
    *, overwrite: bool = False, path: Path = IMPORT_MAPPINGS_PATH,
) -> SavedMapping:
    """显式保存角色的零基列索引；同指纹需 overwrite 授权，最多 20 份且不自动淘汰。"""
    if not headers or not all(isinstance(header, str) for header in headers):
        raise ValueError("保存映射需要有效列头。")
    normalized = tuple(normalize_header(header) for header in headers)
    counts = Counter(normalized)
    saved_sources = {}
    for role, indices in sources.items():
        if not isinstance(role, str):
            raise ValueError("字段角色名称必须是字符串。")
        chosen = []
        for index in indices:
            # bool 不能冒充列号；无名/同名列本次仍可按位置用，但不能持久化成唯一名称来源。
            if type(index) is not int or not 0 <= index < len(normalized):
                raise ValueError("字段列位置不合法。")
            source = normalized[index]
            if not source or counts[source] != 1:
                raise ValueError("同名或无名列存在歧义，可用于本次导入，但不能保存。")
            chosen.append(source)  # 保存列名而非索引，后续列顺序变化仍能重新定位。
        saved_sources[role] = tuple(chosen)
    display_name = name if name is not None else (
        datetime.now().strftime("%m%d %H:%M") + " " + " / ".join(headers[:2])
    )
    if not display_name.strip():
        raise ValueError("映射名称不能为空。")
    fingerprint = header_fingerprint(headers)
    existing = _read_mappings(Path(path))  # 先严格现读；文件损坏时阻断保存，不自行重建或清空。
    same = next((index for index, item in enumerate(existing)
                 if item.fingerprint == fingerprint), None)
    mapping = SavedMapping(fingerprint, display_name.strip(), tuple(headers), saved_sources)
    if same is not None:
        if not overwrite:
            raise ValueError("同指纹映射已存在，需要明确确认覆盖。")
        existing[same] = mapping  # 覆盖不增加数量，满 20 份也允许。
    else:
        if len(existing) >= 20:
            raise ValueError("已保存 20 份映射，不能新增。")
        existing.append(mapping)
    _write_mappings(Path(path), existing)
    return mapping


def delete_mapping(fingerprint: str, path: Path = IMPORT_MAPPINGS_PATH) -> bool:
    """按指纹删除并整份保存；未找到返回 False，不动其他偏好或账本。"""
    mappings = _read_mappings(Path(path))
    remaining = [item for item in mappings if item.fingerprint != fingerprint]
    if len(remaining) == len(mappings):
        return False
    _write_mappings(Path(path), remaining)
    return True  # UI 是否改变选择由 UI 管，偏好层不自动选择其他项。
