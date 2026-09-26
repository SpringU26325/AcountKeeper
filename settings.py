"""记住「上次导出路径」的读写模块（对应需求 3.11）。

职责边界（刻意收窄，便于单测和被不同入口复用）：
- 只负责读写 settings.json 里的 last_export_dir，并把它整理成可直接使用的 Path。
- 不含任何 UI 代码、不 import customtkinter、不接触数据库（不 import sqlite3 / store）。
  因此它既能被启动流程调用，也能被导出流程调用，且无需 GUI 环境即可测试。
- 数据库路径固定为 config.DB_PATH，本模块完全不涉及，也不读写任何数据库路径字段。

与旧方案（用户自定义 csv_dir + 启动回退弹窗）的关键差别：
last_export_dir 只是「另存为」对话框的便利起始目录，属于锦上添花的小功能。
因此任何异常（文件缺失、解析失败、字段非法、目录已删除或不可写）都只做**静默回退**——
回退到 DEFAULT_EXPORT_DIR 并在控制台打一句警告，不再向调用方抛「需要弹窗告诉用户配置错了」的信号。
没有「用户配置错了」这个概念，自然也不需要 is_fallback / is_first_run 两个布尔量。

对外只暴露三个函数：
- load_settings()              读配置 → (last_export_dir, has_saved_dir)，任何异常都回退默认值，绝不抛出
- save_settings(last_export_dir) 写配置 → bool，表示是否真的写成功
- get_last_export_dir()        拿到本次可用的起始目录，并确认它当前确实可写
"""

import json
import os
from pathlib import Path

from config import DEFAULT_EXPORT_DIR, SETTINGS_PATH

# settings.json 里唯一使用的字段名。抽成常量，避免「读」和「写」两处各写一遍字符串而写歪。
SETTINGS_KEY = "last_export_dir"


def _warn(message: str) -> None:
    """统一的警告出口。

    配置有问题属于「可恢复的异常」：按项目约定用 print 打到控制台并继续运行，
    而不是抛异常——需求 3.11 明确要求配置损坏时不能崩溃。
    这里刻意不用弹窗：settings 模块不得依赖 UI，而「上次导出目录失效」只是便利功能失灵，
    静默回退到默认目录即可，没必要打断用户。
    """
    print(f"警告：{message}")


def _parse_dir(value: object) -> Path | None:
    """把 settings.json 里的原始值转成 Path；非法则返回 None。

    返回 None 而不是直接返回默认值，是为了让调用方能区分
    「读到了合规路径」和「字段有问题，只能当没记住」这两种情况——
    前者要正常使用，后者必须回退，不能混为一谈。
    """
    # JSON 里正常只会是字符串；数字/布尔/null/列表等一律视为非法配置。
    if not isinstance(value, (str, Path)):
        _warn(f"settings.json 中的 {SETTINGS_KEY} 类型不合法，将忽略该记录。")
        return None

    text = str(value).strip()
    if not text:
        _warn(f"settings.json 中的 {SETTINGS_KEY} 为空，将忽略该记录。")
        return None

    try:
        # expanduser 让配置里写的 "~/xxx" 能正确展开为实际主目录。
        return Path(text).expanduser()
    except (RuntimeError, OSError, ValueError) as error:
        # expanduser 在无法确定用户主目录（例如 HOME 缺失、用户名异常）时会抛错，
        # 这里兜住，保证一个畸形路径不会把整个启动流程带崩。
        _warn(f"settings.json 中的 {SETTINGS_KEY} 无法解析（{error}），将忽略该记录。")
        return None


def _is_usable(directory: Path) -> bool:
    """只读探测：目录当前存在且可写。

    刻意**不**创建目录：这里判断的是「用户上次导出用的目录现在还认不认得」，
    用户并没有要求重建它。静默重建一个已被删除/已拔出的目录属于意外副作用，
    而且本方案失败时是静默回退，用户根本无从察觉，等于偷偷在磁盘上造目录。
    需求 3.11 要求兜住 U 盘拔出、网络盘断开、无权限这类情况，所以必须真去问一次文件系统。
    """
    return directory.is_dir() and os.access(directory, os.W_OK)


def _prepare_directory(directory: Path) -> bool:
    """写配置前的准备：先确保目录存在，再确认可写；任一步失败返回 False。

    与 _is_usable 的区别在于「要不要动手创建」：写配置说明用户刚刚确实选了/用了这个目录，
    补建它是合理的；而读取阶段只是在回忆，不该有副作用。
    这里校验是必要的——把一条「确定用不了」的路径持久化下来，等于让下次读取必然降级，白留脏配置。
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _warn(f"无法创建目录 {directory}（{error}），本次记录未保存。")
        return False

    # os.access 是最轻量的可写性探测：不像「写个探针文件再删掉」那样会在用户目录里留垃圾。
    if not os.access(directory, os.W_OK):
        _warn(f"目录不可写：{directory}，本次记录未保存。")
        return False

    return True


def load_settings() -> tuple[Path, bool]:
    """读取 settings.json 中的 last_export_dir，返回 (目录, 是否真的记住了)。

    第二个布尔量的含义：True = 配置文件里确实有一条合法的 last_export_dir；
    False = 文件不存在 / 解析失败 / 字段缺失或非法，此时第一个元素是 DEFAULT_EXPORT_DIR。

    本函数只负责「读 + 判定」，不做任何写操作（写入一律走 save_settings）。

    容错策略（逐级降级，绝不抛异常给调用方）：
    - 文件不存在     → 返回默认值（用户还没导出过，属正常情况，不警告）；
    - 内容解析失败   → 打印警告，返回默认值；
    - 根节点不是对象 → 打印警告，返回默认值；
    - 字段缺失或非法 → 打印警告，返回默认值。
    """
    if not SETTINGS_PATH.exists():
        # 文件不存在说明用户从未导出过（或删掉了配置），直接用默认目录，不必打扰。
        return DEFAULT_EXPORT_DIR, False

    try:
        # 显式指定 UTF-8：Windows 上 open() 默认用 GBK，中文路径会乱码甚至解析失败。
        with SETTINGS_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        # ValueError 覆盖 json.JSONDecodeError（文件被截断、被手工改坏等情况）。
        _warn(f"settings.json 读取或解析失败（{error}），本次使用默认目录：{DEFAULT_EXPORT_DIR}")
        return DEFAULT_EXPORT_DIR, False

    if not isinstance(raw, dict):
        # 内容是合法 JSON 但根节点不是对象（如 []、"abc"），同样按损坏处理。
        _warn(f"settings.json 根节点不是对象，本次使用默认目录：{DEFAULT_EXPORT_DIR}")
        return DEFAULT_EXPORT_DIR, False

    # 用 get() 而不是 []：字段缺失时得到 None，由 _parse_dir 统一走「非法→忽略」分支。
    directory = _parse_dir(raw.get(SETTINGS_KEY))
    if directory is None:
        return DEFAULT_EXPORT_DIR, False

    return directory, True


def save_settings(last_export_dir: Path) -> bool:
    """把用户实际选择过的导出目录写入 settings.json，返回是否写入成功。

    返回 bool 而不是抛异常：写失败只值得在控制台提一句——这块信息只是「下次导出时的起始目录」，
    不该影响本次已经完成的导出结果（所以调用方拿到 False 时无需弹窗告警）。

    写入前先做路径规范化与可用性校验，避免把一条「确定用不了」的路径持久化下来——
    否则下次读取必然降级，等于给未来留一份脏配置。
    """
    try:
        # expanduser 展开 ~，resolve 转成绝对路径，避免受当前工作目录影响
        # （打包后 cwd 常与源码目录不同，相对路径会指向意外位置）。
        normalized = Path(last_export_dir).expanduser().resolve()
    except (RuntimeError, OSError, ValueError) as error:
        _warn(f"导出目录规范化失败（{error}），本次记录未保存。")
        return False

    # 目录不存在就创建；建不出来说明路径不可用，直接判失败，不写入配置。
    if not _prepare_directory(normalized):
        return False

    payload = {SETTINGS_KEY: str(normalized)}

    try:
        # 配置目录可能还没建（用户把数据目录整个删掉后重启），先补建再写。
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False 让中文路径以原文保存，便于用户手工查看；
        # indent=2 让文件易读、Git diff 友好。
        with SETTINGS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError as error:
        _warn(f"settings.json 写入失败（{error}），本次记录未保存。")
        return False

    return True


def get_last_export_dir() -> tuple[Path, bool]:
    """返回本次可用的 (起始目录, 是否用上了记住的目录)，并顺带校验该目录当前可写。

    返回的 Path 一定可以直接塞进 filedialog 的 initialdir：
    - 记住了且现在确实可用 → 原样返回，第二个元素为 True；
    - 没记住、或记录读不出来、或目录已被删除/不可写 → 返回 DEFAULT_EXPORT_DIR，第二个元素为 False。
    调用方不需要（也不应该）自己再判断一次可用性，否则两层逻辑容易走偏。
    """
    directory, has_saved_dir = load_settings()

    if not has_saved_dir:
        return DEFAULT_EXPORT_DIR, False

    directory = directory.resolve()

    if not _is_usable(directory):
        # 读得出来但目录已经不在/不可写：静默回退，不弹窗（需求 3.11 的新方案）。
        _warn(f"上次导出目录当前不可用（{directory}），本次使用默认目录：{DEFAULT_EXPORT_DIR}")
        return DEFAULT_EXPORT_DIR, False

    return directory, True
