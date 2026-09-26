"""用户自定义 CSV 导出目录的读写模块（对应需求 3.11）。

职责边界（刻意收窄，便于单测和被不同入口复用）：
- 只负责读写 settings.json 里的 csv_dir，并把它整理成真实可用的 Path。
- 不含任何 UI 代码、不 import customtkinter、不接触数据库（不 import sqlite3 / store）。
  因此它既能被启动流程调用，也能被设置界面调用，且无需 GUI 环境即可测试。
- 数据库路径固定为 config.DB_PATH，本模块完全不涉及，也不读写任何数据库路径字段。

配置状态通过返回值里的两个布尔量传给调用方，两者语义严格区分、不可混用：
- is_fallback=True ：读到了配置但内容不可用（解析失败/字段缺失/路径非法/目录建不出来），
  属于**真正的异常回退**，需求 3.11 要求必须弹窗告知用户；
- is_first_run=True：settings.json 根本不存在，属于**首次运行的正常情况**，不是错误。
弹窗属于界面职责，所以本模块只负责「把信号传出去」，由 UI 层决定怎么提示
（首次运行就不该用「之前设置的目录不可用」这种措辞打扰用户）。

对外只暴露三个函数：
- load_settings()         读配置 → (csv_dir, is_fallback, is_first_run)，任何异常都回退默认值，绝不抛出
- save_settings(csv_dir)  写配置 → bool，表示是否真的写成功
- get_effective_csv_dir() 拿到最终生效的 csv_dir，并确认真实可用
"""

import json
import os
from pathlib import Path

from config import DEFAULT_CSV_DIR, SETTINGS_PATH


def _warn(message: str) -> None:
    """统一的警告出口。

    配置有问题属于「可恢复的异常」：按项目约定用 print 打到控制台并继续运行，
    而不是抛异常——需求 3.11 明确要求配置损坏时不能崩溃。
    这里刻意不用弹窗，避免 settings 模块依赖 UI（弹窗交给调用方）。
    """
    print(f"警告：{message}")


def _parse_csv_dir(value: object) -> Path | None:
    """把 settings.json 里的原始值转成 Path；非法则返回 None。

    返回 None 而不是直接返回默认值，是为了让调用方能区分
    「读到了合规路径」和「字段有问题、必须回退」这两种情况——
    后者需要置起 is_fallback 让 UI 弹窗，前者不能误报。
    """
    # JSON 里正常只会是字符串；数字/布尔/null/列表等一律视为非法配置。
    if not isinstance(value, (str, Path)):
        _warn(f"settings.json 中的 csv_dir 类型不合法，将回退默认目录：{DEFAULT_CSV_DIR}")
        return None

    text = str(value).strip()
    if not text:
        _warn(f"settings.json 中的 csv_dir 为空，将回退默认目录：{DEFAULT_CSV_DIR}")
        return None

    try:
        # expanduser 让配置里写的 "~/xxx" 能正确展开为实际主目录。
        return Path(text).expanduser()
    except (RuntimeError, OSError, ValueError) as error:
        # expanduser 在无法确定用户主目录（例如 HOME 缺失、用户名异常）时会抛错，
        # 这里兜住，保证一个畸形路径不会把整个启动流程带崩。
        _warn(f"settings.json 中的 csv_dir 无法解析（{error}），将回退默认目录：{DEFAULT_CSV_DIR}")
        return None


def _ensure_usable(directory: Path) -> bool:
    """尝试创建目录并检查可写性，返回该目录是否真实可用。

    需求 3.11 要求兜住「路径写法合法、但实际用不了」的情况（U 盘拔出、网络盘断开、
    无权限），所以必须真的动一次文件系统去验证，不能只靠字符串格式判断。
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _warn(f"无法创建目录 {directory}（{error}）。")
        return False

    # os.access 是最轻量的可写性探测：不像「写个探针文件再删掉」那样会在用户目录里留垃圾。
    if not os.access(directory, os.W_OK):
        _warn(f"目录不可写：{directory}")
        return False

    return True


def load_settings() -> tuple[Path, bool, bool]:
    """读取 settings.json 中的 csv_dir，返回 (csv_dir, is_fallback, is_first_run)。

    两个布尔量的语义严格互斥，调用方必须分开判断、不能合并成一个「出问题了」：
    - is_first_run=True：settings.json 不存在 —— 首次运行，属正常情况，UI 不应弹窗；
    - is_fallback=True ：文件存在但内容不可用 —— 真正的异常回退，UI 应弹窗提示；
    - 两者皆 False     ：配置读取正常。

    本函数只负责「读 + 判定」，不做任何写操作（写入一律走 save_settings）。

    容错策略（逐级降级，绝不抛异常给调用方）：
    - 文件不存在      → 返回默认值 + is_first_run=True（不是错误，不警告）；
    - 内容解析失败    → 打印警告，返回默认值 + is_fallback=True；
    - 根节点不是对象  → 打印警告，返回默认值 + is_fallback=True；
    - 字段缺失或非法  → 打印警告，返回默认值 + is_fallback=True。
    """
    if not SETTINGS_PATH.exists():
        # 关键语义区分：文件压根不存在说明用户从未配置过，这是「首次运行」而非「回退」。
        # 所以这里置 is_first_run=True、is_fallback=False，让 UI 层能把两种情况分开处理——
        # 首次运行弹「之前设置的目录不可用」会让用户莫名其妙。
        return DEFAULT_CSV_DIR, False, True

    try:
        # 显式指定 UTF-8：Windows 上 open() 默认用 GBK，中文路径会乱码甚至解析失败。
        with SETTINGS_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        # ValueError 覆盖 json.JSONDecodeError（文件被截断、被手工改坏等情况）。
        # 文件确实存在却读不出来，属于真正的异常回退，故 is_fallback=True。
        _warn(f"settings.json 读取或解析失败（{error}），已回退默认目录：{DEFAULT_CSV_DIR}")
        return DEFAULT_CSV_DIR, True, False

    if not isinstance(raw, dict):
        # 内容是合法 JSON 但根节点不是对象（如 []、"abc"），同样按损坏处理。
        _warn(f"settings.json 根节点不是对象，已回退默认目录：{DEFAULT_CSV_DIR}")
        return DEFAULT_CSV_DIR, True, False

    # 用 get() 而不是 []：字段缺失时得到 None，由 _parse_csv_dir 统一走「非法→回退」分支。
    csv_dir = _parse_csv_dir(raw.get("csv_dir"))
    if csv_dir is None:
        return DEFAULT_CSV_DIR, True, False

    return csv_dir, False, False


def save_settings(csv_dir: Path) -> bool:
    """把用户选择的 CSV 目录写入 settings.json，返回是否写入成功。

    返回 bool 而不是抛异常：失败原因（磁盘满、无权限等）需要由 UI 层弹窗告知用户，
    但弹窗是界面职责，本模块不得引入 UI 依赖，因此只把结果传出去。

    写入前先做路径规范化与可用性校验，避免把一条「确定用不了」的路径持久化下来——
    否则下次启动就要走回退流程，等于给未来埋雷。
    """
    try:
        # expanduser 展开 ~，resolve 转成绝对路径，避免受当前工作目录影响
        # （打包后 cwd 常与源码目录不同，相对路径会指向意外位置）。
        normalized = Path(csv_dir).expanduser().resolve()
    except (RuntimeError, OSError, ValueError) as error:
        _warn(f"CSV 目录规范化失败（{error}），本次设置未保存。")
        return False

    # 目录不存在就创建；建不出来说明路径不可用，直接判失败，不写入配置。
    if not _ensure_usable(normalized):
        return False

    payload = {"csv_dir": str(normalized)}

    try:
        # 配置目录可能还没建（用户把数据目录整个删掉后重启），先补建再写。
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False 让中文路径以原文保存，便于用户手工查看；
        # indent=2 让文件易读、Git diff 友好。
        with SETTINGS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError as error:
        _warn(f"settings.json 写入失败（{error}），本次设置未保存。")
        return False

    return True


def get_effective_csv_dir() -> tuple[Path, bool, bool]:
    """返回最终生效的 (csv_dir, is_fallback, is_first_run)，并尽力保证该目录真实可用。

    三种情形与返回的布尔量一一对应，供 UI 层分流处理：
    - 首次运行（is_first_run=True）：直接用默认目录，不弹窗、不算错误；
    - 内容回退（is_fallback=True）：配置损坏，用默认目录并置 is_fallback 交给 UI 弹窗；
    - 读取正常：再做「文件系统层面」的校验——路径写法合法、但目录建不出来或不可写时
      （U 盘拔出、网络盘断开、无权限），同样回退到 DEFAULT_CSV_DIR 并置 is_fallback=True。
    没有最后一层，这类情况会漏到后面导出 CSV 时才炸。
    """
    csv_dir, is_fallback, is_first_run = load_settings()

    if is_first_run:
        # 首次运行：保证默认目录能落地即可，保持 is_first_run=True 让 UI 判断「无需提示」。
        _ensure_usable(DEFAULT_CSV_DIR)
        return DEFAULT_CSV_DIR, False, True

    if is_fallback:
        # 配置内容已判定不可用，只需要保证回退目标能落地
        # （DEFAULT_CSV_DIR 就是 DATA_DIR，config 导入时已创建，这里只是兜底）。
        _ensure_usable(DEFAULT_CSV_DIR)
        return DEFAULT_CSV_DIR, True, False

    csv_dir = csv_dir.resolve()

    if not _ensure_usable(csv_dir):
        # 配置读得出来但目录用不了：仍属异常回退（不是首次运行），需要 UI 弹窗。
        _warn(f"CSV 目录不可用，已回退默认目录：{DEFAULT_CSV_DIR}")
        _ensure_usable(DEFAULT_CSV_DIR)
        return DEFAULT_CSV_DIR, True, False

    return csv_dir, False, False
