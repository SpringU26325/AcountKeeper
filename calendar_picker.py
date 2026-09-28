"""日历选择器弹窗（需求 3.12）。

对外只暴露 ask_date()：给「添加记录」和「编辑记录」的日期字段提供一个
可视化日历，减少用户手动敲 YYYY-MM-DD 时的格式错误。

实现上刻意不引入 tkcalendar 等新依赖，纯 CustomTkinter 拼装，
以便和项目内其他弹窗共用同一套配色、圆角与字体。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import calendar
import tkinter as tk

import customtkinter as ctk

# 弹窗尺寸：宽 320 刚好容纳 7 列日期，高 380 容纳表头 + 最多 6 行网格 + 底部按钮。
_DIALOG_WIDTH = 320
_DIALOG_HEIGHT = 380

# 与 dialogs.py 内其他弹窗保持一致的浅色主题配色。
_BG_COLOR = "#F0F4F8"
_TITLE_COLOR = "#243447"
_TEXT_COLOR = "#455A64"
_ACCENT_COLOR = "#2F80ED"
_ACCENT_HOVER_COLOR = "#256AC4"
# 星期表头与「今日」按钮用的浅灰底。
_SUBTLE_COLOR = "#E3EAF2"
_SUBTLE_HOVER_COLOR = "#D2DEE9"

# 星期表头顺序固定为「日 一 二 三 四 五 六」（需求 3.12），
# 因此下面用 firstweekday=SUNDAY 的 Calendar 实例来切分周，而不是改 calendar 模块的全局设置。
_WEEKDAY_HEADERS = ("日", "一", "二", "三", "四", "五", "六")
_CALENDAR = calendar.Calendar(firstweekday=calendar.SUNDAY)


def _apply_logo_icon(window: tk.Misc) -> None:
    """为弹窗设置项目 logo 图标。

    这里没有复用 dialogs._apply_logo_icon：dialogs.py 需要导入本模块调用 ask_date，
    若再反向导入会形成循环导入，所以保留一份极小的私有实现。
    """
    try:
        icon_path = Path(__file__).resolve().parent / "image" / "logo.ico"
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        # 图标只是装饰，缺失或平台不支持时静默忽略，绝不能因此让日历打不开。
        pass


def _center_window(window: tk.Misc, size: tuple[int, int] | None = None) -> None:
    """按窗口的真实尺寸，把它摆到屏幕水平居中、垂直略偏上的位置。

    这里用原生 wm_geometry 而不是 CTkToplevel.geometry()：后者会把宽高**和坐标**
    一起按 DPI 缩放（见 ctk_toplevel.geometry 的 _apply_geometry_scaling），
    而 winfo_* 系列返回的都是物理像素，两者混用会让窗口整体偏几十像素。

    size 用来传入「上一次显示时量到的尺寸」：复用打开要在 deiconify 之前摆位，
    而那里不能读 winfo_width()（见 ask_date 的约束说明）。
    """
    width, height = size if size is not None else (window.winfo_width(), window.winfo_height())
    target_x = max((window.winfo_screenwidth() - width) // 2, 0)
    # 垂直方向不用严格二分：略偏上更符合视觉重心，也不会被任务栏压住。
    target_y = max((window.winfo_screenheight() - height) // 3, 0)
    # 仅在位置确实需要变化时才移动：移动本身会再次触发 <Configure>，
    # 少了这个判断就会无限自我触发（死循环）。
    if (window.winfo_x(), window.winfo_y()) != (target_x, target_y):
        window.wm_geometry(f"+{target_x}+{target_y}")


def _parse_iso_date(value: str) -> date | None:
    """尽力解析 YYYY-MM-DD 字符串，失败一律返回 None，绝不抛异常。"""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (AttributeError, ValueError):
        return None


# ==================== 方案 3：弹窗实例复用 ====================
# 状态拆成两个模块级容器，把「窗口级」和「打开级」严格分开：
#   _win —— 窗口级：整个进程只建一次窗，只有主窗口销毁时才真正 destroy。
#   _ses —— 打开级：每次「打开 → 关闭」是一个完整周期，关闭时收尾、打开时整体重建。
# 所有回调都从这两个容器「现读」，不靠闭包捕获，因此复用后不会读到上一次的旧状态
# （日期格子每次重建，_choose 若捕获旧值就会写到上一月去）。
_win: SimpleNamespace | None = None
_ses: SimpleNamespace | None = None


def _render_month() -> None:
    """按 _ses.state 里的年月重绘日期网格。

    月份切换时直接销毁旧按钮重新生成，比逐个改文字更简单，也不会残留上一月的高亮状态。
    """
    state = _ses.state
    year = int(state["year"])
    month = int(state["month"])
    selected = state["selected"]
    day_grid = _win.day_grid
    _win.month_label.configure(text=f"{year} 年 {month} 月")

    # 【复用前提】每次重绘都必须把上一批格子**真正销毁**：复用后窗口不重建，
    # 漏掉这一行就会在换月后残留旧按钮。
    for child in day_grid.winfo_children():
        child.destroy()

    # 注意：monthcalendar 是 calendar 模块的**函数**，Calendar **实例**上并没有它，
    # 实例对应的方法是 monthdayscalendar。若误调 monthcalendar 会直接抛 AttributeError，
    # 而异常发生在按钮回调里不会关窗口，表现就是「弹窗在、日期全空白」。
    # 用 monthdayscalendar 还顺便避开了模块级函数依赖全局 firstweekday 的问题：
    # 全局状态一旦被其他代码改过，下面的星期表头就会和日期列对不上。
    # 返回值为按周分行的二维列表，本月之外的日子用 0 补齐。
    weeks = _CALENDAR.monthdayscalendar(year, month)
    for row, week in enumerate(weeks):
        for column, day in enumerate(week):
            if day == 0:
                # 非本月日期：需求要求留空，不放任何可点击控件。
                ctk.CTkLabel(day_grid, text="", height=30).grid(
                    row=row, column=column, padx=1, pady=1, sticky="nsew"
                )
                continue

            # 只有「年月日」全部一致才高亮，避免每月同号的日子被误点亮。
            is_selected = (
                isinstance(selected, date)
                and selected.year == year
                and selected.month == month
                and selected.day == day
            )
            ctk.CTkButton(
                day_grid,
                text=str(day),
                # 用默认参数把「这一格代表哪天」钉死在闭包里；回调本体再读 _ses.state，
                # 两者都指向本次打开，复用后不会串到上一月。
                command=lambda picked=day: _choose(picked),
                width=36,
                height=30,
                corner_radius=8,
                font=_win.font,
                # 选中日期用主题蓝底白字，其余用白底深字，一眼可辨。
                fg_color=_ACCENT_COLOR if is_selected else "#FFFFFF",
                hover_color=_ACCENT_HOVER_COLOR if is_selected else _SUBTLE_HOVER_COLOR,
                text_color="#FFFFFF" if is_selected else _TEXT_COLOR,
            ).grid(row=row, column=column, padx=1, pady=1, sticky="nsew")


def _shift_month(delta: int) -> None:
    """上一月 / 下一月：跨年时自动借位（1 月减一月 → 上一年 12 月）。"""
    state = _ses.state
    month = int(state["month"]) + delta
    year = int(state["year"])
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1
    state["year"], state["month"] = year, month
    _render_month()


def _choose(day: int) -> None:
    """点击某一天：写回结果并关闭弹窗（关闭 = 收尾 + 隐藏 + 唤醒等待方）。"""
    state = _ses.state
    _ses.result[0] = date(int(state["year"]), int(state["month"]), day).isoformat()
    _close()


def _goto_today() -> None:
    """「今日」按钮：跳回今天所在月份并高亮今天（不关闭弹窗，仍需点日期确认）。"""
    today = _ses.today
    _ses.state["year"], _ses.state["month"], _ses.state["selected"] = (
        today.year,
        today.month,
        today,
    )
    _render_month()


def _cancel() -> None:
    """取消：保持 result[0] 为 None，调用方据此判断用户放弃选择。"""
    _close()


def _recenter(_event: tk.Event | None = None) -> None:
    """按「真实尺寸」把窗口摆到屏幕中央，且只在尺寸变化时动手。

    不能用 winfo_reqwidth() 预算：CustomTkinter 会按 DPI 缩放控件，Tk 随后还会把
    窗口撑到内容所需大小，一次算出的坐标必然偏斜；而窗口未映射时 winfo_width()
    又会谎报 1。所以监听 <Configure>，用当时的真实宽高重算，自我校正。

    但 <Configure> 在「尺寸变化」和「位置变化」时**都会**触发，所以不能每次都居中：
    用户拖动标题栏 → 坐标改变 → 若此时重新居中，窗口会被立刻拽回屏幕中心，
    手感上就是「窗口拖不动」。因此只在尺寸变化时居中，纯位移直接忽略。
    """
    dialog = _win.dialog
    width, height = dialog.winfo_width(), dialog.winfo_height()
    # 窗口尚未映射时 winfo_width() 只返回 1，用它算出的坐标毫无意义，
    # 居中反而会让窗口先闪一下再归位，所以跳过，等真正的尺寸事件。
    if width <= 1 or height <= 1:
        return
    size = (width, height)
    # last_size 存的是元组本身；若存成 [元组] 的列表，下面 `size == last_size`
    # 会变成「元组 == 列表」，恒为 False，守卫就失效了。
    if size == _ses.last_size:
        return  # 尺寸没变却收到事件 → 是拖动产生的位移，尊重用户摆的位置
    _ses.last_size = size
    _center_window(dialog)


def _deferred_setup() -> None:
    """窗口级的一次性 setup，压到首个 after_idle 执行，且整个进程只做一次。

    【方案 1 的延续】protocol / <Escape> 绑定与 logo 图标都属于「窗口第一眼显示
    出来时不必须已经在位」的工作：iconbitmap 要读 .ico 文件，bind 要往解释器里
    装脚本，而它们晚 10ms 到位对用户没有任何差别。

    刻意**留在同步段**的三样东西，都不可挪：
      - _render_month()：它是弹窗的主体内容。挪到 after_idle 就会先弹出一个
        空白日历再填进去，属于观感倒退，不是优化。
      - _recenter()：窗口先按内容撑大、再被摆到屏幕中央，靠的就是它；
        延后会让窗口先在默认位置露一帧。
      - grab_set()：它是模态性本身，不是性能开关（实测只要 0.01ms，
        既跑得快又不可省）。
    """
    # 守卫：after_idle 的回调**不随窗口隐藏/销毁自动失效**（它是解释器空闲队列里
    # 的一张便条）。复用后窗口一直存在，这里要防的是「主窗口已经退出」这一种。
    if _win is None:
        return
    try:
        if not _win.dialog.winfo_exists():
            return
    except tk.TclError:
        return
    _win.setup_done = True
    _apply_logo_icon(_win.dialog)
    # 右上角关闭按钮等同「取消」，避免出现状态不明确的弹窗。
    _win.dialog.protocol("WM_DELETE_WINDOW", _cancel)
    # ESC 取消，与 ask_month / ask_edit_record 的操作习惯保持一致。
    _win.dialog.bind("<Escape>", lambda _event: _cancel())
    # <Destroy> 兜底：主窗口退出会连带销毁本弹窗，而那条路径上没人会调 _close()，
    # 挂在 wait_variable 上的调用方就会永远等不到信号，这里补一次唤醒。
    _win.dialog.bind("<Destroy>", _on_destroy, add="+")


def _wake() -> None:
    """唤醒挂在 wait_variable 上的调用方：变量只要「值有变化」即可，不必有意义。"""
    if _ses is None:
        return
    try:
        _ses.signal.set(int(_ses.signal.get()) + 1)
    except (tk.TclError, ValueError):
        # 主窗口退出时 Tcl 变量可能已经被回收，此时也没人在等结果了，忽略即可。
        pass


def _cleanup() -> None:
    """本次打开的收尾：取消句柄 → 解绑 → 释放 grab。刻意**不 destroy**（复用前提）。"""
    if _win is None or _ses is None:
        return
    dialog = _win.dialog
    # ① 取消 after_idle 句柄：它是解释器空闲队列里的便条，窗口隐藏并不会让它失效。
    if _ses.setup_idle[0] is not None:
        try:
            dialog.after_cancel(_ses.setup_idle[0])
        except (tk.TclError, ValueError):
            pass
        _ses.setup_idle[0] = None
    # ② 解绑 <Configure>：当初用 tk.Misc.bind 就是为了拿到 funcid，这里按 funcid 精确摘除。
    #    不能用 dialog.unbind(...)：CTk 的覆写收到非 None 的 funcid 会抛 ValueError，
    #    而 CTkEntry/CTkFrame 的 bind 又根本不返回 funcid。
    if _ses.configure_funcid[0] is not None:
        try:
            tk.Misc.unbind(dialog, "<Configure>", _ses.configure_funcid[0])
        except (tk.TclError, ValueError):
            pass
        _ses.configure_funcid[0] = None
    # ③ 释放 grab：实测 withdraw() 不会代劳（隐藏后 grab_current 仍指向本窗），
    #    不显式释放会把后续点击全部吸到一个看不见的窗口上。
    try:
        dialog.grab_release()
    except tk.TclError:
        pass


def _close() -> None:
    """关闭本次打开：收尾 → 隐藏 → 唤醒等待方。窗口留给下一次复用（不 destroy）。"""
    if _win is None or _ses is None:
        return
    _cleanup()
    # 记下本次真正显示时的尺寸，供下一次复用打开预置坐标（此刻窗口可见，值是可信的）。
    if _win.dialog.winfo_width() > 1:
        _win.last_size = (_win.dialog.winfo_width(), _win.dialog.winfo_height())
    try:
        # 用 tk.Wm.withdraw 绕开 CTkToplevel.withdraw 的覆写：后者会在 CTk 自己正在
        # 操作标题栏时置位 _withdraw_called_after_...，干扰它的状态机。
        tk.Wm.withdraw(_win.dialog)
    except tk.TclError:
        pass
    _ses.open = False
    _wake()


def _on_destroy(event: tk.Event) -> None:
    """弹窗被真正销毁时（只有主窗口退出这一条路）兜底唤醒等待方，避免卡死。"""
    if _win is None or event.widget is not _win.dialog:
        return
    _wake()


def _ensure_window(parent: tk.Misc) -> None:
    """懒创建弹窗：只有第一次打开时才真正建窗，之后一直复用同一个实例。

    窗口级的东西（控件树、字体）只在这里建一次；打开级状态一律不进这里，
    全部由 ask_date 每次打开时重建。回调都是模块级函数、一律现读 _win / _ses，
    所以不存在「闭包捕获到上一次状态」的问题。
    """
    global _win
    if _win is not None:
        return

    dialog = ctk.CTkToplevel(parent)
    dialog.title("选择日期")
    # 【Step 2.1】同 category_picker：tk.Wm.resizable 绕开 CTkToplevel.resizable 的覆写，
    # 避免它在 Windows 上额外安排 after(10, _windows_set_titlebar_color)（实测 sync 12.5ms /
    # visible 34.8ms）。代价可忽略：_last_resizable_args 仅被写入、CTk 内部无读取点，且本弹窗尺寸固定。
    tk.Wm.resizable(dialog, False, False)
    dialog.transient(parent)
    dialog.configure(fg_color=_BG_COLOR)
    # logo 图标（iconbitmap）刻意不在这里设：它要读一次 image/logo.ico，属于
    # 「晚一帧再设也看不出来」的工作，与 protocol / <Escape> 一起挪到下面的
    # _deferred_setup 里做。
    # 先给一个尺寸提示：CTk 会把逻辑像素按 DPI 放大；随后 Tk 还可能按内容再撑大，
    # 所以最终位置交给下面的 <Configure> 回调按真实尺寸校正。
    dialog.geometry(f"{_DIALOG_WIDTH}x{_DIALOG_HEIGHT}")

    dialog_font = ("Microsoft YaHei UI", 11)

    # ---------- 顶部：◀ 上一月 / 年月标题 / 下一月 ▶ ----------
    header = ctk.CTkFrame(dialog, fg_color="transparent")
    header.pack(fill="x", padx=16, pady=(16, 8))
    # 让中间标题占据全部剩余宽度，两侧箭头按钮保持固定大小。
    header.grid_columnconfigure(1, weight=1)

    ctk.CTkButton(
        header,
        text="◀",
        command=lambda: _shift_month(-1),
        width=36,
        height=32,
        corner_radius=8,
        fg_color=_SUBTLE_COLOR,
        hover_color=_SUBTLE_HOVER_COLOR,
        text_color=_TEXT_COLOR,
        font=dialog_font,
    ).grid(row=0, column=0)

    month_label = ctk.CTkLabel(
        header,
        text="",
        font=("Microsoft YaHei UI", 13, "bold"),
        text_color=_TITLE_COLOR,
    )
    month_label.grid(row=0, column=1)

    ctk.CTkButton(
        header,
        text="▶",
        command=lambda: _shift_month(1),
        width=36,
        height=32,
        corner_radius=8,
        fg_color=_SUBTLE_COLOR,
        hover_color=_SUBTLE_HOVER_COLOR,
        text_color=_TEXT_COLOR,
        font=dialog_font,
    ).grid(row=0, column=2)

    # ---------- 中部：星期表头（灰底） ----------
    weekday_bar = ctk.CTkFrame(dialog, fg_color=_SUBTLE_COLOR, corner_radius=8)
    weekday_bar.pack(fill="x", padx=16)
    for column, name in enumerate(_WEEKDAY_HEADERS):
        # uniform 保证 7 列等宽，与下方日期网格的列宽严格对齐。
        weekday_bar.grid_columnconfigure(column, weight=1, uniform="weekday")
        ctk.CTkLabel(
            weekday_bar,
            text=name,
            font=("Microsoft YaHei UI", 11),
            text_color=_TEXT_COLOR,
        ).grid(row=0, column=column, pady=4, sticky="nsew")

    # ---------- 主体：7 列日期网格 ----------
    day_grid = ctk.CTkFrame(dialog, fg_color="transparent")
    day_grid.pack(fill="both", expand=True, padx=16, pady=(6, 0))
    for column in range(7):
        day_grid.grid_columnconfigure(column, weight=1, uniform="day")

    # ---------- 底部：今日 / 取消 ----------
    footer = ctk.CTkFrame(dialog, fg_color="transparent")
    footer.pack(fill="x", padx=16, pady=(8, 16))
    # 左右两列等宽，让两个按钮宽度一致、整体对称（沿用 dialogs.py 的按钮风格）。
    footer.grid_columnconfigure((0, 1), weight=1)

    ctk.CTkButton(
        footer,
        text="今日",
        command=_goto_today,
        height=34,
        corner_radius=9,
        fg_color=_SUBTLE_COLOR,
        hover_color=_SUBTLE_HOVER_COLOR,
        text_color=_TEXT_COLOR,
        font=dialog_font,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ctk.CTkButton(
        footer,
        text="取消",
        command=_cancel,
        height=34,
        corner_radius=9,
        fg_color="#90A4AE",
        hover_color="#78909C",
        font=dialog_font,
    ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    # 窗口级引用集中放进 _win，供上面的模块级回调「现读」（回调在调用时才解析名字，
    # 所以这里最后赋值也来得及）。
    _win = SimpleNamespace(
        dialog=dialog,
        font=dialog_font,
        month_label=month_label,
        day_grid=day_grid,
        setup_done=False,
        # 上一次真正显示时量到的尺寸；None 表示窗口还没成功显示过（首次打开）。
        last_size=None,
    )


def ask_date(parent: ctk.CTk, initial_date: str) -> str | None:
    """弹出日历选择器。

    Args:
        parent: 父窗口，弹窗会以 transient 方式挂在它上面。
        initial_date: 预选日期（YYYY-MM-DD），同时决定初始展示的月份。

    Returns:
        选中日期（YYYY-MM-DD 字符串）；用户点「取消」、按 ESC 或点右上角关闭时返回 None。
    """
    global _ses

    # 【打开前】先把上一次遗留的会话收干净：单例复用要求任何时刻最多只有一个日历在等，
    # 否则会出现「两个弹窗同时可见」和「两次 wait_variable 同时挂起」。
    if _ses is not None and _ses.open:
        _close()

    # 【懒创建】首次打开才真正建窗，而且**构造即显示**：刻意不在建窗后立刻藏起来。
    # 因为 CTkToplevel 构造时会安排 after(5, _revert_withdraw_after_windows_set_titlebar_color)，
    # 那笔回调会把紧随其后的隐藏动作原样撤销（实测隐藏后 viewable 仍为 1），
    # 所以「建完就藏」的复用写法在这里不成立。
    _ensure_window(parent)
    dialog = _win.dialog
    # parent 每次可能不同（主窗口 / 编辑记录弹窗），transient 必须每次重设：
    # 认错父窗口会导致弹窗跑到主窗口下面，或跟着父窗口一起最小化消失。
    dialog.transient(parent)

    today = date.today()
    # 初始选中的日期跟随 initial_date；传入非法/空字符串时退回今天，保证弹窗始终可用。
    initial = _parse_iso_date(initial_date) or today
    # 【打开级状态】每次打开整体重建，所有字段都是「本次打开」的新值。用模块级 _ses
    # 承载、回调一律现读，因此 state / result / signal 天然是「跨打开长期存活且始终
    # 指向本次」的那一份，日期格子的闭包不会串月。
    _ses = SimpleNamespace(
        open=True,
        # 必须清 None：不清的话上一次的选择会变成这一次的返回值。
        result=[None],
        # 每次新建：wait_variable 等的是「这一次」的信号（见本函数末尾的说明）。
        signal=tk.IntVar(master=dialog),
        today=today,
        # 用 dict 保存可变状态（当前展示的年月 + 高亮日期），方便被多个回调共享修改。
        state={"year": initial.year, "month": initial.month, "selected": initial},
        # 必须复位：不复位时复用打开「尺寸没变」，_recenter 会把居中逻辑整个拦掉，
        # 弹窗就会停在用户上次拖动到的位置。
        last_size=None,
        # 两个单元素列表当句柄槽，回调内部可原地改写（省掉一遍 nonlocal）。
        setup_idle=[None],
        configure_funcid=[None],
    )

    # 【setup 只做一次】protocol / <Escape> / <Destroy> / logo 图标都是窗口级的，
    # 只安排一次即可。若首次打开的 idle 还没跑就被关掉，_cleanup 会取消它，
    # 此时 setup_done 仍为 False，下一次打开会重新安排，不会永久丢失。
    if not _win.setup_done and _ses.setup_idle[0] is None:
        _ses.setup_idle[0] = dialog.after_idle(_deferred_setup)

    # 【(b) 预置坐标】复用打开时改用「上一次显示量到的尺寸」先摆到中心再显示：
    # 窗口第一帧就落在中心，不会先在用户上次拖到的位置露一帧再跳回来（实测那段
    # 错位帧 29~46ms）。首次打开没有可信尺寸，跳过，交给后面的 _recenter。
    if _win.last_size is not None:
        _center_window(dialog, _win.last_size)

    # 【先显示】deiconify 必须早于任何依赖 winfo_width()/height() 的计算：
    # 窗口未映射时这些值不可信（首次映射前恒为 1）。
    tk.Wm.deiconify(dialog)

    # 每次打开重新登记 <Configure>，拿 funcid 以便关闭时精确解绑。
    # 必须 add="+"：CTkToplevel 内部也用 <Configure> 跟踪窗口尺寸，直接 bind 会顶掉它。
    _ses.configure_funcid[0] = tk.Misc.bind(dialog, "<Configure>", _recenter, add="+")

    # 首次绘制必须在控件（month_label / day_grid）创建之后。
    # 这里额外包一层容错，但复用后**不能再 destroy**：渲染失败与用户取消走同一条路
    # （隐藏 + 返回 None），否则会留下一个关不掉的空壳窗口，用户以为程序卡死了。
    try:
        _render_month()
    except Exception as exc:  # noqa: BLE001 - 日历是纯辅助功能，任何意外都不应影响主界面
        print(f"警告：日历渲染失败，已取消本次选择（{exc}）。")
        _close()
        return None

    # 渲染完成后再按「真实尺寸」校正一次位置：复用打开时窗口不会重新映射、尺寸也可能
    # 没变，光靠 <Configure> 可能一次都不触发，这里显式补一次。
    _recenter()

    # 锁住焦点并阻塞等待。这里不能用 wait_window：复用后关闭只是 withdraw，窗口永不
    # 销毁，wait_window 会一直不返回。改为等一个每次打开都新建的变量，由 _close()
    # （用户选中/取消）与 _on_destroy（主窗口退出）负责唤醒。
    session = _ses
    dialog.grab_set()
    dialog.wait_variable(session.signal)
    return session.result[0]
