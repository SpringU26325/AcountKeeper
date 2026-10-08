"""导出起始目录偏好（需求 3.11）：只读写 settings.json 的 last_export_dir。

不管理账本路径、标签或导入映射，不依赖 GUI；路径常量由 config 提供。
load_settings 只解析字段，返回（目录，是否读到合法配置），不验证目录当前是否可用。
get_last_export_dir 才检查已记住目录的存在及可写性；默认回退目录不在此处验证或补建。
save_settings 规范化/准备用户目录后整份写文件，返回保存结果，不影响已完成的 CSV 导出。
文件读取、解析及保存中已捕获的错误只打控制台警告，不弹窗；此模块不承诺吞掉所有异常。
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
    # 这是当前时刻的权限探测，不锁定目录；真正导出时仍可能遇到权限变化或磁盘故障。
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

    已处理的配置故障（逐级降级）：
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

    return directory, True  # True 只代表字段合法，尚未检查目录存在及可写性。


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
        # 只持久化一个字段并整份覆盖，因此标签/导入映射必须独立存放；这里没有原子替换。
        with SETTINGS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError as error:
        _warn(f"settings.json 写入失败（{error}），本次记录未保存。")
        return False

    return True


def get_last_export_dir() -> tuple[Path, bool]:
    """返回（起始目录，是否用上已记住目录），供另存为对话框使用。

    字段合法且当前目录可用才返回 True；配置故障或目录不可用按已有分支回退默认值。
    默认目录不在此处验证，路径 resolve/探测也未用统一异常捕获包裹；不保证所有路径错误均回退。
    """
    directory, has_saved_dir = load_settings()

    if not has_saved_dir:
        return DEFAULT_EXPORT_DIR, False

    # 仅检查已保存的候选目录，不顺手创建已删除目录或改写失效的偏好文件。
    directory = directory.resolve()

    if not _is_usable(directory):
        # 读得出来但目录已经不在/不可写：静默回退，不弹窗（需求 3.11 的新方案）。
        _warn(f"上次导出目录当前不可用（{directory}），本次使用默认目录：{DEFAULT_EXPORT_DIR}")
        return DEFAULT_EXPORT_DIR, False

    return directory, True
