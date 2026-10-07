"""显式保存导入映射；不依赖 UI、数据库、settings 或标签偏好。"""

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
    fingerprint: str
    name: str
    headers: tuple[str, ...]
    sources: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class MappingCandidate:
    mapping: SavedMapping
    matched_count: int
    total_count: int
    missing_headers: tuple[str, ...]
    new_headers: tuple[str, ...]


def normalize_header(value: str) -> str:
    # 仅统一字符形式和首尾空白，不使用字段同义词词表，避免宽松识别污染严格复用。
    return unicodedata.normalize("NFKC", value).strip().casefold()


def header_fingerprint(headers: Sequence[str]) -> str:
    counts = Counter(normalize_header(header) for header in headers)
    return json.dumps(sorted(counts.items()), ensure_ascii=False, separators=(",", ":"))


def _read_mappings(path: Path) -> list[SavedMapping]:
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
                if (
                    not isinstance(role, str) or not isinstance(values, list)
                    or not all(
                        isinstance(value, str) and value and counts[value] == 1
                        for value in values
                    )
                ):
                    raise MappingStorageError("保存映射中存在缺列或同名列歧义。")
                cleaned_sources[role] = tuple(values)
            fingerprint = header_fingerprint(headers)
            if item.get("fingerprint") != fingerprint or fingerprint in fingerprints:
                raise MappingStorageError("映射指纹不一致或重复。")
            fingerprints.add(fingerprint)
            result.append(SavedMapping(fingerprint, name, tuple(headers), cleaned_sources))
        return result
    except (json.JSONDecodeError, TypeError) as error:
        raise MappingStorageError("映射文件无法解析，请保留并检查原文件。") from error


def list_saved_mappings(path: Path = IMPORT_MAPPINGS_PATH) -> list[SavedMapping]:
    return _read_mappings(Path(path))


def find_mapping_candidates(
    headers: Sequence[str], path: Path = IMPORT_MAPPINGS_PATH,
) -> list[MappingCandidate]:
    current = Counter(normalize_header(header) for header in headers)
    candidates = []
    for mapping in _read_mappings(Path(path)):
        saved = Counter(normalize_header(header) for header in mapping.headers)
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
    normalized = tuple(normalize_header(header) for header in headers)
    counts = Counter(normalized)
    # 只返回保存过的角色；新增列不自动分配，缺失／当前同名歧义来源留空待手动指定。
    return {
        role: tuple(normalized.index(source) for source in sources if counts[source] == 1)
        for role, sources in mapping.sources.items()
    }


def _write_mappings(path: Path, mappings: list[SavedMapping]) -> None:
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
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_mapping(
    headers: Sequence[str], sources: dict[str, tuple[int, ...]], name: str | None = None,
    *, overwrite: bool = False, path: Path = IMPORT_MAPPINGS_PATH,
) -> SavedMapping:
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
            if type(index) is not int or not 0 <= index < len(normalized):
                raise ValueError("字段列位置不合法。")
            source = normalized[index]
            if not source or counts[source] != 1:
                raise ValueError("同名或无名列存在歧义，可用于本次导入，但不能保存。")
            chosen.append(source)
        saved_sources[role] = tuple(chosen)
    display_name = name if name is not None else (
        datetime.now().strftime("%m%d %H:%M") + " " + " / ".join(headers[:2])
    )
    if not display_name.strip():
        raise ValueError("映射名称不能为空。")
    fingerprint = header_fingerprint(headers)
    existing = _read_mappings(Path(path))
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
    mappings = _read_mappings(Path(path))
    remaining = [item for item in mappings if item.fingerprint != fingerprint]
    if len(remaining) == len(mappings):
        return False
    _write_mappings(Path(path), remaining)
    return True  # UI 是否改变选择由 UI 管，偏好层不自动选择其他项。
