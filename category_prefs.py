"""用户类别偏好（需求 3.13 第三轮）：用户自己维护的类别列表（user）。

为什么另立一个模块、而不是塞进 settings.py：settings.json 的写入是**整份覆盖**
（settings.save_settings 直接 json.dump 一个只含 last_export_dir 的字典），
类别数据一旦住进同一个文件，下次导出路径写回就会把它整段抹掉。而且 settings.py 的
模块文档已经把职责写死成「只负责读写 last_export_dir、任何异常都静默回退」，
而类别是用户显式点过「+ 保存」的**主动意图**，静默丢弃等于数据损失，容错等级根本不同。
所以这里另开一份 categories.json，与 account.db / settings.json 同目录，便于整体备份迁移。

数据模型（按收支分开，每段就是一个类别名列表）：

    {"version": 1,
     "expense": ["餐饮", "打车"],
     "income":  ["工资"]}

**user 列表就是唯一真源**：往里面加一项，它就出现在下拉候选里；从里面删一项，
它就真的不再出现。没有 hidden、没有「已隐藏视图」、也没有「恢复」——那是上一轮把
「移除」做成可逆操作时留下的中间态，把一次删除拆成「隐藏 / 恢复」两步，用户得先
想明白「我是暂时不想看见，还是永久不要」才能决定点哪个按钮，语义太重。

**列表的顺序也是真实顺序**：下拉列表就照这个顺序显示，所以「置顶」不需要任何额外
的权重字段，在数据层就是把该项挪到列表最前（见 move_to_top）。

**预置类别只在首次启动（categories.json 不存在）时作为 user 的初始值写进去一次**，
之后 config.py 里的 DEFAULT_*_CATEGORIES 就不再参与任何运算。这是刻意的：如果每次
算候选都把预置常量并进来，用户删掉一个预置项（比如「人情」）之后它下次又会自己
回来，删除动作等于无效。把预置「一次性转正」成普通列表项，「删了就是真的没了」
这条语义才立得住。

对外契约：
- 方向参数只认 "expense" / "income"（模块常量 EXPENSE / INCOME）。
  「支出 / 收入」这种说法属于界面层，数据层不认，避免两边口径漂移。
- 读：build_candidates，任何异常都降级成空列表，绝不抛。
- 写：save_user / delete_user / move_to_top，返回布尔值表示是否真的成功了。
  读到不符合新结构的格式（文件坏了、根不是对象、某一段是 dict 而不是 list）
  一律当空列表处理并**允许写入**：本项目尚未发布过、没有需要保护的存量数据，
  下次保存直接覆盖成新结构即可，不必用「拒绝写入」那套保守策略。
- 首次启动：ensure_migrated，只在文件不存在时把「预置 + 历史类别」写进 user。
"""

from __future__ import annotations

import json
from typing import Callable

from config import (
    CATEGORY_PREFS_PATH,
    DEFAULT_EXPENSE_CATEGORIES,
    DEFAULT_INCOME_CATEGORIES,
)

# 方向标识。界面层负责把「支出 / 收入」翻译成这两个值，数据层只认它们。
EXPENSE = "expense"
INCOME = "income"

# 文件格式版本。只写不校验：本项目从未发布过，不存在需要按版本分支处理的存量文件；
# 读到不认识的版本只打一条警告，处理方式与其它不认识的格式完全一样（当空、允许覆盖）。
_SCHEMA_VERSION = 1


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


def _clean_list(value: object, field: str) -> list[str]:
    """把 JSON 里读到的一串值整理成「去空白、去空项、去重」的字符串列表。

    单项脏数据只丢那一项、不判整份文件报废：手改文件写错一个字符，不该让
    「保存类别」这个功能整体瘫痪。去重按首次出现的顺序保留，这样列表顺序
    （也就是下拉列表的顺序）稳定可预期。返回的是一份**新列表**，调用方拿到后
    可以直接原地增删，不会串改到别处的数据。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        # 典型情况：上一轮结构下的 {"user": [...], "hidden": [...]}——当空处理即可，
        # 反正下一次写入会把这一段换成本轮的裸列表。
        _warn(f"{field} 不是列表（可能是旧结构），本次按空列表处理。")
        return []

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
    return cleaned


def _read_document() -> dict | None:
    """读 categories.json；读不出来时返回 None（不存在 / 解析失败 / 根不是对象）。

    刻意不再区分「文件不存在」与「文件坏了」：新语义下两者处理完全相同——都按空列表
    算候选，并且都允许写入。本项目尚未发布、没有需要保护的存量数据，坏文件下次保存
    直接覆盖成新结构即可，不需要「拒绝写入」那套保守策略。
    """
    if not CATEGORY_PREFS_PATH.exists():
        return None

    try:
        # 显式 UTF-8：Windows 上 open() 默认用 GBK，中文类别名会乱码甚至解析失败。
        with CATEGORY_PREFS_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        # ValueError 覆盖 json.JSONDecodeError（文件被截断、被手工改坏等情况）。
        _warn(f"{CATEGORY_PREFS_PATH.name} 读取或解析失败（{error}），本次按空列表处理。")
        return None

    if not isinstance(raw, dict):
        # 内容是合法 JSON 但根节点不是对象（如 []、"abc"），同样按空处理。
        _warn(f"{CATEGORY_PREFS_PATH.name} 根节点不是对象，本次按空列表处理。")
        return None

    if raw.get("version") != _SCHEMA_VERSION:
        # 只警告、不拒绝：见 _SCHEMA_VERSION 处的说明。
        _warn(
            f"{CATEGORY_PREFS_PATH.name} 的格式版本为 {raw.get('version')!r}，"
            f"当前程序写的是 {_SCHEMA_VERSION}，本次仍按空列表处理。"
        )

    return raw


def _user_list(document: dict, key: str) -> list[str]:
    """取出文档里某一个方向的类别列表；该段缺失或结构不对时按空处理。"""
    return _clean_list(document.get(key), key)


def build_candidates(direction: str) -> list[str]:
    """算出下拉候选：就是该方向的 user 列表本身。

    user 就是唯一真源——加进去的会出现、删掉的会消失，没有预置常量、也没有 hidden
    参与运算（预置只在首次启动时被写进 user 一次，见 ensure_migrated）。

    每次都从文件现算、不在内存里留缓存：调用点全是用户点击（点 ▼ / 点 + / 点 ×），
    文件不到 1KB，读一次的代价可以忽略；换来的是「弹窗里改完，界面立刻生效」，
    不需要任何跨模块通知机制——这正是把这份逻辑收敛到本模块的前提。
    """
    key = _direction_key(direction)
    document = _read_document()
    if document is None:
        return []
    return _user_list(document, key)


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
    change: Callable[[str, list[str]], bool],
) -> bool:
    """save_user / delete_user 的公共骨架：读文档 → 改这一个方向的列表 → 整份写回。

    change 回调返回「列表是否真的被改动过」：没改动就跳过写盘直接返回 True——
    例如删一个本来就不在列表里的名字，目标状态已经达成，再整份重写一遍文件是白做功。
    """
    key = _direction_key(direction)
    cleaned = (name or "").strip()
    if not cleaned:
        # 空类别名进列表只会变成一行点不出任何意义的空条目，直接拒绝。
        _warn("类别名为空，本次操作未保存。")
        return False

    document = _read_document()
    if document is None:
        # 首次使用（或文件读不出来）：从最小骨架开始，真正建文件发生在下面写回时。
        document = {}

    names = _user_list(document, key)
    if not change(cleaned, names):
        return True

    # 只覆盖本方向那一段；另一方向的数据原样保留（document 是原地改的）。
    document[key] = names
    return _write_document(document)


def save_user(direction: str, name: str) -> bool:
    """把类别加进 user（用户主动点「+ 保存」）；已在列表里则不重复添加。

    预置项与用户自建项走完全相同的路径：预置项既然已经在首次启动时写进了 user，
    它在这里就只是一个普通名字，不需要任何特殊分支。
    """
    def change(name_text: str, names: list[str]) -> bool:
        if name_text in names:
            # 已经在列表里：不改动，交给 _apply 跳过写盘（结果对用户是「已保存」）。
            return False
        names.append(name_text)
        return True

    return _apply(direction, name, change)


def delete_user(direction: str, name: str) -> bool:
    """把类别从 user 里删掉（用户点列表项右侧的 ×）。

    这是**真删除**：从列表移除之后，该项目下次不会再出现在候选里。想让它回来，
    只能重新用「+ 保存」输入同名类别——没有 hidden、没有恢复视图、也没有二次确认
    对话框（弹窗里不能用 messagebox，见 category_picker 的说明），需求要的就是
    「删掉就是真的没了」这条简单直白的语义。

    名字本来就不在列表里时同样返回 True，但不写盘（由 _apply 统一处理）：目标状态
    已经达成，再重写一遍文件只是白做功；返回 True 而不是 False，是因为用户的意图
    （「让它不在列表里」）确实已经实现了，弹窗不该为此报一个红字。
    """
    def change(name_text: str, names: list[str]) -> bool:
        if name_text not in names:
            return False
        names.remove(name_text)
        return True

    return _apply(direction, name, change)


def move_to_top(direction: str, name: str) -> bool:
    """把类别移到 user 列表最前（用户点列表项右侧的「顶」）。

    不需要新增任何字段：user 列表的顺序本身就是真实顺序，下拉列表直接照它显示，
    所以「置顶」在数据层就是一次列表内重排。反过来说，一旦引入独立的权重 / 置顶
    标志，就出现了「两份真相」（列表顺序与权重）需要同步，而这正是本模块一直在
    避免的东西。

    两个边界与 delete_user 保持同一套口径（都返回 True、都不写盘）：
    - 名字本来就不在列表里：用户的意图无从实现是因为它根本不存在，界面重画一遍
      列表它就会消失，不该为此报错。
    - 名字已经在第 0 位：置顶是幂等操作，目标状态已经达成，再整份重写一遍文件
      只是白做功（由 _apply 按 change 回调的返回值统一跳过写盘）。
    """
    def change(name_text: str, names: list[str]) -> bool:
        if name_text not in names or names[0] == name_text:
            # 不存在、或已经在最前：目标状态已达成，交给 _apply 跳过写盘。
            return False
        # 先摘下来再插到最前，**不是**与第 0 位交换：交换会把原来第一项扔到被置顶项
        # 原来所在的位置上，等于把其余各项的相对顺序也一并打乱；摘下重插只让
        # 「这一项提前、其余整体后移一格」，符合「移到最前」的直觉。
        names.remove(name_text)
        names.insert(0, name_text)
        return True

    return _apply(direction, name, change)


def ensure_migrated(
    expense: list[str] | None, income: list[str] | None
) -> bool:
    """首次启动时给两个方向的 user 写一份初始值 = 预置类别 + 传进来的历史类别。

    用「文件是否存在」充当「是否已初始化」的标记，不额外加一个 initialized 字段：
    - 文件不存在 → 现在写初值（历史为空也会写出文件，避免下次启动又走一遍这里）；
    - 文件已存在 → 什么都不做。这一道判断就是「删了又回来」的闸门：只要文件还在，
      config.py 里的预置常量就再也不会被读进候选。

    预置在前、历史追加在后，整体去重（历史里本来就可能出现「餐饮」这类预置同名项）。
    这样首启拿到的候选与改动前完全一致，用户看不出差别；历史为空时（本项目从未发布过，
    新用户的库里必然是空的）就等同于「候选 = 预置类别」。

    函数名与签名保持不变：名字里的 migrate 是上一轮「从数据库迁移历史类别」的遗留，
    语义已经变成「初始化 user」。改名要连 ui.py 的调用点一起动，留到清 #47 那一轮
    （届时 store.get_categories 这个唯一调用点也会一并删掉）。

    返回值：True 表示本次真的写了初值（新建了文件），False 表示无需初始化或写失败。
    """
    if CATEGORY_PREFS_PATH.exists():
        return False

    # 先拼接再去重：_clean_list 会顺手去掉空白项与重名，不用另写一套合并逻辑。
    expense_seed = list(DEFAULT_EXPENSE_CATEGORIES) + _clean_list(
        expense, f"{EXPENSE}.history"
    )
    income_seed = list(DEFAULT_INCOME_CATEGORIES) + _clean_list(
        income, f"{INCOME}.history"
    )
    document = {
        EXPENSE: _clean_list(expense_seed, f"{EXPENSE}.user"),
        INCOME: _clean_list(income_seed, f"{INCOME}.user"),
    }
    return _write_document(document)
