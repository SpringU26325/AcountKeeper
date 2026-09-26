"""日历选择器弹窗（需求 3.12）。

对外只暴露 ask_date()：给「添加记录」和「编辑记录」的日期字段提供一个
可视化日历，减少用户手动敲 YYYY-MM-DD 时的格式错误。

实现上刻意不引入 tkcalendar 等新依赖，纯 CustomTkinter 拼装，
以便和项目内其他弹窗共用同一套配色、圆角与字体。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
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


def _center_window(window: tk.Misc) -> None:
    """按窗口当前的真实尺寸，把它摆到屏幕水平居中、垂直略偏上的位置。

    这里用原生 wm_geometry 而不是 CTkToplevel.geometry()：后者会把宽高**和坐标**
    一起按 DPI 缩放（见 ctk_toplevel.geometry 的 _apply_geometry_scaling），
    而 winfo_* 系列返回的都是物理像素，两者混用会让窗口整体偏几十像素。
    """
    width, height = window.winfo_width(), window.winfo_height()
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


def ask_date(parent: ctk.CTk, initial_date: str) -> str | None:
    """弹出日历选择器。

    Args:
        parent: 父窗口，弹窗会以 transient 方式挂在它上面。
        initial_date: 预选日期（YYYY-MM-DD），同时决定初始展示的月份。

    Returns:
        选中日期（YYYY-MM-DD 字符串）；用户点「取消」、按 ESC 或点右上角关闭时返回 None。
    """
    dialog = ctk.CTkToplevel(parent)
    dialog.title("选择日期")
    dialog.resizable(False, False)
    dialog.transient(parent)
    dialog.configure(fg_color=_BG_COLOR)
    _apply_logo_icon(dialog)
    # 先给一个尺寸提示：CTk 会把逻辑像素按 DPI 放大；随后 Tk 还可能按内容再撑大，
    # 所以最终位置交给下面的 <Configure> 回调按真实尺寸校正。
    dialog.geometry(f"{_DIALOG_WIDTH}x{_DIALOG_HEIGHT}")

    dialog_font = ("Microsoft YaHei UI", 11)
    # 用单元素列表承载返回值：闭包内部可直接写入，无需 nonlocal 声明。
    result: list[str | None] = [None]

    today = date.today()
    # 初始选中的日期跟随 initial_date；传入非法/空字符串时退回今天，保证弹窗始终可用。
    initial = _parse_iso_date(initial_date) or today
    # 用 dict 保存可变状态（当前展示的年月 + 高亮日期），方便被多个闭包共享修改。
    state: dict[str, int | date] = {
        "year": initial.year,
        "month": initial.month,
        "selected": initial,
    }

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

    def _render_month() -> None:
        """按当前年月重绘日期网格。

        月份切换时直接销毁旧按钮重新生成，比逐个改文字更简单，也不会残留上一月的高亮状态。
        """
        year = int(state["year"])
        month = int(state["month"])
        selected = state["selected"]
        month_label.configure(text=f"{year} 年 {month} 月")

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
                    command=lambda picked=day: _choose(picked),
                    width=36,
                    height=30,
                    corner_radius=8,
                    font=dialog_font,
                    # 选中日期用主题蓝底白字，其余用白底深字，一眼可辨。
                    fg_color=_ACCENT_COLOR if is_selected else "#FFFFFF",
                    hover_color=_ACCENT_HOVER_COLOR if is_selected else _SUBTLE_HOVER_COLOR,
                    text_color="#FFFFFF" if is_selected else _TEXT_COLOR,
                ).grid(row=row, column=column, padx=1, pady=1, sticky="nsew")

    def _shift_month(delta: int) -> None:
        """上一月 / 下一月：跨年时自动借位（1 月减一月 → 上一年 12 月）。"""
        month = int(state["month"]) + delta
        year = int(state["year"])
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        state["year"], state["month"] = year, month
        _render_month()

    def _choose(day: int) -> None:
        """点击某一天：写回结果并关闭弹窗。"""
        result[0] = date(int(state["year"]), int(state["month"]), day).isoformat()
        dialog.destroy()

    def _goto_today() -> None:
        """「今日」按钮：跳回今天所在月份并高亮今天（不关闭弹窗，仍需点日期确认）。"""
        state["year"], state["month"], state["selected"] = (
            today.year,
            today.month,
            today,
        )
        _render_month()

    def _cancel() -> None:
        """取消：保持 result[0] 为 None，调用方据此判断用户放弃选择。"""
        dialog.destroy()

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

    # 右上角关闭按钮等同「取消」，避免出现状态不明确的弹窗。
    dialog.protocol("WM_DELETE_WINDOW", _cancel)
    # ESC 取消，与 ask_month / ask_edit_record 的操作习惯保持一致。
    dialog.bind("<Escape>", lambda _event: _cancel())

    # 首次绘制必须在控件（month_label / day_grid）创建之后。
    # 这里额外包一层容错：一旦渲染失败，宁可关掉弹窗也不能留一个「空壳窗口」——
    # 空壳窗口只能点右上角关闭，用户会以为程序卡死了。
    try:
        _render_month()
    except Exception as exc:  # noqa: BLE001 - 日历是纯辅助功能，任何意外都不应影响主界面
        print(f"警告：日历渲染失败，已取消本次选择（{exc}）。")
        dialog.destroy()
        return None

    # 渲染完成后再按「真实尺寸」重新居中。
    # 不能用 winfo_reqwidth() 预算：CustomTkinter 会按 DPI 缩放控件，Tk 随后还会把
    # 窗口撑到内容所需大小，一次算出的坐标必然偏斜；而窗口未映射时 winfo_width()
    # 又会谎报 1。所以监听 <Configure>，用当时的真实宽高重算，自我校正。
    # 必须 add="+"：CTkToplevel 内部也用 <Configure> 跟踪窗口尺寸，直接 bind 会顶掉它。
    #
    # 但 <Configure> 在「尺寸变化」和「位置变化」时**都会**触发，所以不能每次都居中：
    # 用户拖动标题栏 → 坐标改变 → 若此时重新居中，窗口会被立刻拽回屏幕中心，
    # 手感上就是「窗口拖不动」。因此这里只在**尺寸发生变化**时才居中，
    # 纯位移（拖动）直接忽略，把位置完全交还给用户。
    # 注意 last_size 存的是元组本身（用 nonlocal 赋值）；若存成 [元组] 的列表，
    # 下面 `size == last_size` 会变成「元组 == 列表」，恒为 False，守卫就失效了。
    last_size: tuple[int, int] | None = None

    def _recenter(_event: tk.Event | None = None) -> None:
        nonlocal last_size
        width, height = dialog.winfo_width(), dialog.winfo_height()
        # 窗口尚未映射时 winfo_width() 只返回 1，用它算出的坐标毫无意义，
        # 居中反而会让窗口先闪一下再归位，所以跳过，等真正的尺寸事件。
        if width <= 1 or height <= 1:
            return
        size = (width, height)
        if size == last_size:
            return  # 尺寸没变却收到事件 → 是拖动产生的位移，尊重用户摆的位置
        last_size = size
        _center_window(dialog)

    dialog.bind("<Configure>", _recenter, add="+")

    # 锁住焦点并阻塞等待，保证 return 拿到的结果一定是用户已经选完或取消后的状态。
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0]
