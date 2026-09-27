"""用户类别偏好（需求 3.13 第二轮）：用户主动保存的类别 + 被隐藏的类别。

为什么另立一个模块、而不是塞进 settings.py：settings.json 的写入是**整份覆盖**
（settings.save_settings 直接 json.dump 一个只含 last_export_dir 的字典），
类别数据一旦住进同一个文件，下次导出路径写回就会把它整段抹掉。而且 settings.py 的
模块文档已经把职责写死成「只负责读写 last_export_dir、任何异常都静默回退」，
而类别是用户显式点过「+ 保存」的**主动意图**，静默丢弃等于数据损失，容错等级根本不同。
所以这里另开一份 categories.json，与 account.db / settings.json 同目录，便于整体备份迁移。

数据模型（按收支分开，与 config 里那两套预置类别对齐）：

    {"version": 1,
     "expense": {"user": [...], "hidden": [...]},
     "income":  {"user": [...], "hidden": [...]}}

user 与 hidden 刻意分成两个列表而不是 [{"name": ..., "hidden": true}]：
保存是加法、隐藏是减法，本来就是两组集合运算，分开存读取即用；合并存法还要处理
「同名两条」的冲突，复杂度全无收益。

**hidden 是纯粹的「排除集」，它不会把名字从 user 里删掉**，两者互不干扰。
这是刻意的：如果隐藏时顺手删掉 user，那么「隐藏一个用户自建类别 → 再恢复它」
就会两头落空（user 里没了、hidden 里也没了），类别直接人间蒸发。
拆成两个互不相干的集合后，恢复只要把它从 hidden 里拿掉，它会以原来的身份回来
（预置项靠预置身份、用户自建项靠 user 身份），重名由 build_candidates 统一去重。

对外契约：
- 方向参数只认 "expense" / "income"（模块常量 EXPENSE / INCOME）。
  「支出 / 收入」这种说法属于界面层，数据层不认，避免两边口径漂移。
- 读：load_state / build_candidates，任何异常都降级成空状态，绝不抛。
- 写：save_user / hide / restore，返回布尔值表示是否真的落盘成功；
  文件损坏时**拒绝写入**，宁可不保存也不覆盖用户可能还能手工抢救的文件。
- 迁移：ensure_migrated，只在文件不存在时把「历史用过的类别」快照进 user。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from config import (
    CATEGORY_PREFS_PATH,
    DEFAULT_EXPENSE_CATEGORIES,
    DEFAULT_INCOME_CATEGORIES,
)

# 方向标识。界面层负责把「支出 / 收入」翻译成这两个值，数据层只认它们。
EXPENSE = "expense"
INCOME = "income"

# 文件格式版本。读取时只认这个数字：认不出来就当作「看不懂的文件」拒绝写入，
# 防止旧版本程序把新版本写的数据洗掉（用户降级运行的场景）。
_SCHEMA_VERSION = 1

# 文件读取的三种状态。单独区分 missing 与 broken 是必要的：
#   - missing：用户还没用过这个功能 → 用空状态，并**允许写入**（首次保存要能建文件）；
#   - broken：解析不了或版本不认识 → 必须**拒绝写入**，否则一次保存就会把用户
#     手工修得回来的文件覆盖成空壳，属于不可逆的数据损失。
_OK = "ok"
_MISSING = "missing"
_BROKEN = "broken"


@dataclass(frozen=True)
class CategoryState:
    """某一个方向（支出或收入）的类别偏好。"""

    user: tuple[str, ...]  # 用户主动保存过的类别
    hidden: tuple[str, ...]  # 被隐藏的类别（含预置），按隐藏先后排列


def _warn(message: str) -> None:
    """统一的警告出口。

    与 settings.py 同款：配置类问题只打到控制台并继续运行，不抛异常、不弹窗
    （数据层不得依赖 UI）。写失败会另外通过返回值告诉界面层，由界面层决定怎么提示。
    """
    print(f"警告：{message}")


def _direction_key(direction: str) -> str:
    """校验并归一化方向参数；非法值属于编程错误，直接抛。"""
    key = str(direction).strip().lower()
    if key not in (EXPENSE, INCOME):
        raise ValueError(
            f"未知的收支方向：{direction!r}（只支持 {EXPENSE!r} / {INCOME!r}）"
        )
    return key


def _clean_list(value: object, field: str) -> tuple[str, ...]:
    """把 JSON 里读到的一串值整理成「去空白、去空项、去重」的字符串元组。

    单项脏数据只丢那一项、不判整份文件损坏：手改文件写错一个字符，不该让
    「保存类别」这个功能整体瘫痪。去重按首次出现的顺序保留，这样 hidden 的
    「隐藏先后」和 user 的「保存先后」都稳定可预期。用元组是因为这两个集合
    对外只读，元组能挡住调用方原地 append 而污染缓存。
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        _warn(f"{field} 不是列表，已忽略该字段。")
        return ()

    cleaned: list[str] = []
    dropped = 0
    for item in value:
        if not isinstance(item, str):
            dropped += 1
            continue
        name = item.strip()
        if not name:
            dropped += 1
            continue
        if name not in cleaned:
            cleaned.append(name)
    if dropped:
        _warn(f"{field} 中有 {dropped} 项不是合法类别名，已忽略。")
    return tuple(cleaned)


def _read_document() -> tuple[dict | None, str]:
    """读 categories.json，返回 (文档, 状态)。状态含义见 _OK / _MISSING / _BROKEN。"""
    if not CATEGORY_PREFS_PATH.exists():
        return None, _MISSING

    try:
        # 显式 UTF-8：Windows 上 open() 默认用 GBK，中文类别名会乱码甚至解析失败。
        with CATEGORY_PREFS_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        # ValueError 覆盖 json.JSONDecodeError（文件被截断、被手工改坏等情况）。
        _warn(f"{CATEGORY_PREFS_PATH.name} 读取或解析失败（{error}），将忽略该文件。")
        return None, _BROKEN

    if not isinstance(raw, dict):
        # 内容是合法 JSON 但根节点不是对象（如 []、"abc"），同样按损坏处理。
        _warn(f"{CATEGORY_PREFS_PATH.name} 根节点不是对象，将忽略该文件。")
        return None, _BROKEN

    if raw.get("version") != _SCHEMA_VERSION:
        _warn(
            f"{CATEGORY_PREFS_PATH.name} 的格式版本为 {raw.get('version')!r}，"
            f"当前程序只认识 {_SCHEMA_VERSION}，将忽略该文件。"
        )
        return None, _BROKEN

    return raw, _OK


def _section(document: dict, key: str) -> CategoryState:
    """取出文档里某一个方向的偏好；该段缺失或结构不对时按空处理。"""
    raw = document.get(key)
    if raw is None:
        return CategoryState((), ())
    if not isinstance(raw, dict):
        _warn(f"{CATEGORY_PREFS_PATH.name} 的 {key} 段不是对象，已忽略该段。")
        return CategoryState((), ())
    return CategoryState(
        _clean_list(raw.get("user"), f"{key}.user"),
        _clean_list(raw.get("hidden"), f"{key}.hidden"),
    )


def load_state(direction: str) -> CategoryState:
    """读取某个方向的类别偏好；文件缺失或损坏时返回空状态（不抛异常）。"""
    key = _direction_key(direction)
    document, _status = _read_document()
    if document is None:
        return CategoryState((), ())
    return _section(document, key)


def build_candidates(direction: str) -> tuple[list[str], list[str]]:
    """算出下拉候选，返回 (可见候选, 已隐藏项)。

    可见候选 = 预置类别 + user，整体去重后减去 hidden。预置在前、user 追加在后：
    常用类别位置固定、用户能形成肌肉记忆，新保存的类别只出现在列表下方，
    不会把预置项挤走（与第一轮的合并规则保持一致）。

    每次都从文件现算、不在内存里留缓存：调用点全是用户点击（点 ▼ / 点 + / 点 ✕），
    文件不到 1KB，读一次的代价可以忽略；换来的是「弹窗里改完，界面立刻生效」，
    不需要任何跨模块通知机制——这正是把合并逻辑收敛到本模块的前提。
    """
    key = _direction_key(direction)
    presets = (
        DEFAULT_INCOME_CATEGORIES if key == INCOME else DEFAULT_EXPENSE_CATEGORIES
    )
    state = load_state(key)
    hidden = set(state.hidden)

    visible: list[str] = []
    # 预置项与 user 可能重名（迁移进来的历史类别里就有「餐饮」这类预置名），
    # 所以这里统一去重，保证同一个名字只出现一次。
    for name in list(presets) + list(state.user):
        if name not in hidden and name not in visible:
            visible.append(name)

    return visible, list(state.hidden)


def _write_document(document: dict) -> bool:
    """整份写回 categories.json，返回是否写成功。"""
    document["version"] = _SCHEMA_VERSION
    try:
        # 数据目录可能被用户整个删掉，写之前先补建，避免 open() 直接报错。
        CATEGORY_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False 让中文类别以原文存盘，用户手工查看/编辑都直观；
        # indent=2 便于人读，也让 Git diff 干净。
        with CATEGORY_PREFS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
    except OSError as error:
        _warn(f"{CATEGORY_PREFS_PATH.name} 写入失败（{error}），本次操作未保存。")
        return False
    return True


def _apply(
    direction: str,
    name: str,
    change: Callable[[str, list[str], list[str]], None],
) -> bool:
    """保存 / 隐藏 / 恢复三者的公共骨架：读文档 → 改这一个方向的两份列表 → 整份写回。"""
    key = _direction_key(direction)
    cleaned = (name or "").strip()
    if not cleaned:
        # 空类别名进列表只会变成一行点不出任何意义的空条目，直接拒绝。
        _warn("类别名为空，本次操作未保存。")
        return False

    document, status = _read_document()
    if status == _BROKEN:
        # 损坏时不写入：overwrite 掉一个用户可能还修得回来的文件是不可逆的。
        _warn("配置文件已损坏，为避免覆盖用户数据，本次操作未写入。")
        return False
    if document is None:
        # 首次使用：从最小骨架开始，真正创建文件发生在下面写回的时候。
        document = {}

    state = _section(document, key)
    user = list(state.user)
    hidden = list(state.hidden)
    change(cleaned, user, hidden)
    # 只覆盖本方向那一段；另一方向的数据原样保留（document 是原地改的）。
    document[key] = {"user": user, "hidden": hidden}
    return _write_document(document)


def save_user(direction: str, name: str) -> bool:
    """把类别加进 user（用户主动点「+ 保存」）；已在 user 里则不重复添加。

    同时把它从 hidden 里拿掉：用户明确要求这个类别常驻列表，这个动作本身就
    隐含了「别再隐藏它」，留着 hidden 里的同名项只会自相矛盾（既排除又常驻）。
    """
    def change(name_text: str, user: list[str], hidden: list[str]) -> None:
        if name_text in hidden:
            hidden.remove(name_text)
        if name_text not in user:
            user.append(name_text)

    return _apply(direction, name, change)


def hide(direction: str, name: str) -> bool:
    """隐藏类别（用户点 ✕）。语义是「永久隐藏」：不碰数据库，也不动 config 的预置元组。

    预置项与用户自建项走完全相同的路径——「人情」这类预置项也能被隐藏。
    **刻意不把名字从 user 里删掉**（原因见模块开头）：user 记的是「用户保存过什么」，
    hidden 记的是「现在排除掉什么」，两件事，混在一起会让恢复时无处可寻。
    """
    def change(name_text: str, _user: list[str], hidden: list[str]) -> None:
        if name_text not in hidden:
            hidden.append(name_text)

    return _apply(direction, name, change)


def restore(direction: str, name: str) -> bool:
    """恢复被隐藏的类别（用户点 ↺）：把它从 hidden 里拿掉即可。

    user 不动：预置项靠预置身份重新出现，用户自建项靠 user 里那条记录重新出现，
    两种身份都在，恢复后必然能回到列表里。
    """
    def change(name_text: str, _user: list[str], hidden: list[str]) -> None:
        if name_text in hidden:
            hidden.remove(name_text)

    return _apply(direction, name, change)


def ensure_migrated(
    expense: list[str] | None, income: list[str] | None
) -> bool:
    """把第一轮的「数据库 DISTINCT 历史类别」一次性快照进 categories.json。

    这一步是必须的：候选来源从「预置 + 数据库历史」换成「预置 + user − hidden」之后，
    如果 user 还是空的，用户已经用出来的自定义类别（比如「打车」）会当场从列表里消失。
    迁移就是把这个「已经用出来的结果」固化成 user，之后数据库不再参与候选
    ——这正是「越用越乱」的止损点。

    用「文件是否存在」充当迁移标记，不额外加 migrated 字段：
    - 文件不存在 → 现在迁移（数据库为空时也写一份空 user 的文件，避免下次又迁一次）；
    - 文件已存在 → 什么都不做，哪怕它已经损坏（损坏交给 _apply 去拒绝写入，
      这里绝不能自作主张覆盖用户的文件）。

    预置同名项不做剔除：迁移是**无损快照**，历史里有「餐饮」就照抄，重名会在
    build_candidates 合并时自然去重，不影响可见候选。

    返回值：True 表示本次真的执行了迁移（新建了文件），False 表示无需迁移或写失败。
    """
    if CATEGORY_PREFS_PATH.exists():
        return False

    document = {
        EXPENSE: {
            "user": list(_clean_list(expense, f"{EXPENSE}.user")),
            "hidden": [],
        },
        INCOME: {
            "user": list(_clean_list(income, f"{INCOME}.user")),
            "hidden": [],
        },
    }
    return _write_document(document)
