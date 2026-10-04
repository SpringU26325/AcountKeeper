"""日历 / 月历选择器弹窗（需求 3.12、issues #3.2）。

对外两个入口，共用**同一个**复用弹窗实例：
  - ask_date()：日期模式。给「添加记录」和「编辑记录」的日期字段提供可视化
    日历，减少用户手动敲 YYYY-MM-DD 时的格式错误。
  - ask_month()：月份模式。给「月度统计 / 导出 CSV / 查看图表」的月份字段提供
    月历（4 列 × 3 行 + 年份切换），把 issue #3.2 要求的「点选而不是手敲」落地。

两种模式的差异只有两处：主体画什么（日期网格 / 月份网格）、◀ ▶ 翻的是月还是年。
开窗、摆位、模态、收尾这一整套骨架完全共用（见 _begin_session / _finish_session），
所以不存在「两份必须长期同步的实现」。

定位方式与「标签」弹窗保持一致：贴着输入框的左下角弹出，下方空间不够时
翻到上方。调用方通过 anchor 把输入框交进来；不传（或输入框已经不存在）时退回
屏幕居中，见 _anchor_to_input。

实现上刻意不引入 tkcalendar 等新依赖，纯 CustomTkinter 拼装，
以便和项目内其他弹窗共用同一套配色、圆角与字体。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from time import perf_counter
from types import SimpleNamespace
from typing import TypedDict
import calendar
import tkinter as tk

import customtkinter as ctk

# config 是叶子模块（不导入任何项目模块），引用它的 RESOURCE_DIR 不会形成循环依赖。
from config import RESOURCE_DIR
from dialog_lifecycle import ManagedToplevel

# 弹窗尺寸：宽度两种模式共用（同一个窗口实例在模式间切换时不改宽度，才不会左右抖动）。
# 高度按模式区分——日期模式 380 刚好容纳星期表头 + 最多 6 行日期格 + 底部按钮；
# 月份模式只有 3 行月份格，沿用 380 会在网格下方留出一大片空白。
_DIALOG_WIDTH = 320
_DIALOG_HEIGHT_DATE = 380
_DIALOG_HEIGHT_MONTH = 300
# 模式 → 逻辑高度。集中成一张表，将来再加模式只需在这里补一项。
_DIALOG_HEIGHT_BY_MODE = {"date": _DIALOG_HEIGHT_DATE, "month": _DIALOG_HEIGHT_MONTH}

# 贴输入框弹出的两个几何常量。取值与 tag_picker 的 _GAP_ABOVE / _SCREEN_MARGIN
# 严格一致：两个 ▼ 的弹窗相邻出现时，缝隙与贴边距离看起来才是同一套规则。
# 刻意不 import tag_picker 的同名常量——tag_picker 反过来不依赖本模块，
# 但为两个整数建一条交叉依赖不划算，宁可各自留一份并在这里注明同源。
_GAP_ABOVE = 6  # 弹窗与输入框之间的竖向缝隙（物理像素）
_SCREEN_MARGIN = 8  # 贴边保护，避免弹窗压在屏幕边缘上（物理像素）

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


def _apply_app_icon(window: tk.Tk | tk.Toplevel) -> None:
    """为弹窗设置应用图标。

    这里没有复用 dialogs._apply_app_icon：dialogs.py 需要导入本模块调用 ask_date，
    若再反向导入会形成循环导入，所以保留一份极小的私有实现。
    路径来源则统一走 config.RESOURCE_DIR（issues #17），不再各自用 __file__ 拼。
    """
    try:
        # 路径统一由 config.RESOURCE_DIR 提供：它带 sys._MEIPASS 兜底，打包后也能定位到应用图标。
        icon_path = RESOURCE_DIR / "image" / "app_icon.ico"
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        # 图标只是装饰，缺失或平台不支持时静默忽略，绝不能因此让日历打不开。
        pass


def _logical_size(mode: str) -> tuple[int, int]:
    """按模式给出弹窗的**逻辑**尺寸（尚未乘 DPI 缩放）。

    宽度两种模式相同：同一个窗口实例在模式间切换时不改宽度，才不会左右抖动。
    高度按模式区分；模式名意外拼错时退回日期模式的高度——宁可尺寸略有偏差，
    也不能因为一个拼错的字符串让弹窗直接打不开。
    """
    return (_DIALOG_WIDTH, _DIALOG_HEIGHT_BY_MODE.get(mode, _DIALOG_HEIGHT_DATE))


def _physical_size(window: tk.Misc, mode: str) -> tuple[int, int]:
    """把弹窗的**逻辑**尺寸换算成当前 DPI 下的物理像素。

    需要它是因为「摆位」这条路径上只有两个尺寸来源，且两种都不能读 winfo_width()：
      - 复用打开：该模式上一次真正显示时量到的尺寸（见 _close 里记的 _win.last_size）；
      - 首次打开：窗口还没映射，winfo_width() 只会谎报 200（CTkToplevel 的初始值），
        只能由「逻辑常量 × 窗口缩放」算。
    """
    scale = ctk.ScalingTracker.get_window_scaling(window)
    width, height = _logical_size(mode)
    return (int(width * scale), int(height * scale))


def _center_window(window: tk.Tk | tk.Toplevel, size: tuple[int, int] | None = None) -> None:
    """按窗口的真实尺寸，把它摆到屏幕水平居中、垂直略偏上的位置。

    **这是没有 anchor 时的兜底**：正常路径走 _anchor_to_input（贴输入框）。
    保留它的三条理由：anchor 是可选形参；anchor 可能在打开期间被连带销毁
    （编辑记录弹窗关掉的时候）；_ensure_window 建窗那一刻也还没有 anchor。

    这里用原生 wm_geometry 而不是 CTkToplevel.geometry()：后者会把宽高**和坐标**
    一起按 DPI 缩放（见 ctk_toplevel.geometry 的 _apply_geometry_scaling），
    而 winfo_* 系列返回的都是物理像素，两者混用会让窗口整体偏几十像素。

    size 传入「预置坐标要用的尺寸」：复用打开用上次量到的真实尺寸，首次打开用
    「逻辑常量 × DPI 缩放」换算出的物理尺寸。两种情况都不能读 winfo_width()。
    """
    width, height = size if size is not None else (window.winfo_width(), window.winfo_height())
    target_x = max((window.winfo_screenwidth() - width) // 2, 0)
    # 垂直方向不用严格二分：略偏上更符合视觉重心，也不会被任务栏压住。
    target_y = max((window.winfo_screenheight() - height) // 3, 0)
    # 仅在位置确实需要变化时才移动：移动本身会再次触发 <Configure>，
    # 少了这个判断就会无限自我触发（死循环）。
    if (window.winfo_x(), window.winfo_y()) != (target_x, target_y):
        window.wm_geometry(f"+{target_x}+{target_y}")


def _anchor_to_input(
    window: tk.Tk | tk.Toplevel,
    anchor: tk.Misc | None,
    size: tuple[int, int] | None = None,
) -> None:
    """把弹窗贴到 anchor（日期输入框）的左下角，下方放不下时翻到它上方。

    算式逐条照抄 tag_picker._reanchor（横向钳制、竖向翻转、同一组常量），
    两个 ▼ 的行为才会一致。四处刻意偏离它，都是「日历不是下拉列表」带来的：

    1. **宽度不跟 anchor**。tag_picker 是列表、宽度与输入框等宽；日历是 7 列
       日期网格，宽度是内容决定的（_DIALOG_WIDTH），跟输入框走会被压窄、日期列
       挤成一团。所以这里只借 anchor 的**位置**，不借它的宽度。
    2. **不挂 anchor 的 <Configure>、也不挂 50ms 轮询**。tag_picker 需要它们，
       是因为它的列表可能在编辑弹窗**还没完成布局**时就弹出来了（它自己的注释里
       记着实测旧宽偏 52px、旧 y 偏 92px）。日历没有这个问题：用户必须先在屏幕上
       点中 ▼ 按钮，而按钮能被点中就意味着 anchor 早已布局完毕，读数可信。
    3. **恰恰因为不挂 anchor 的事件**，用户把日历拖走后不会有任何回调把它拽回来——
       这是需要的：日历是模态窗（grab_set），用户很可能想把它挪开去看主窗口里的
    数字。tag_picker 是不可拖动的下拉列表，没有这个诉求，两者该有差异。
    4. **anchor 读不出有效高度时退回屏幕居中**，而不是像 tag_picker 那样直接
       return 等下一轮；日历没有等待窗口，退回兜底才能保证任何情况下都有位置。

    坐标口径：winfo_* 与 wm_geometry 都是物理像素，不经过 CTk 的缩放换算。
    """
    if anchor is not None:
        try:
            anchor_exists = bool(anchor.winfo_exists())
        except tk.TclError:
            anchor_exists = False
        if anchor_exists:
            anchor_height = anchor.winfo_height()
            # <=1 是「控件尚未完成布局」的谎报值：此时 rooty 指向的还不是最终位置。
            if anchor_height > 1:
                if size is None:
                    size = (window.winfo_width(), window.winfo_height())
                width, height = size
                screen_width = window.winfo_screenwidth()
                screen_height = window.winfo_screenheight()
                anchor_x = anchor.winfo_rootx()
                anchor_y = anchor.winfo_rooty()
                # 左边缘与输入框左边缘对齐；越界时往回收，保证整条弹窗都留在屏幕内。
                target_x = max(min(anchor_x, screen_width - width - _SCREEN_MARGIN), _SCREEN_MARGIN)
                target_y = anchor_y + anchor_height + _GAP_ABOVE
                if target_y + height > screen_height - _SCREEN_MARGIN:
                    # 下方空间不够就翻到输入框上方；上方也不够时贴着屏幕底边放。
                    above_y = anchor_y - height - _GAP_ABOVE
                    target_y = (
                        above_y
                        if above_y >= _SCREEN_MARGIN
                        else max(screen_height - height - _SCREEN_MARGIN, _SCREEN_MARGIN)
                    )
                # 只在位置真的变了才发 wm_geometry：本函数会在「预置坐标」和「尺寸
                # 变化后的校正」两条路径上被连续调用，少了这个判断就会多一次无谓的
                # 移动（移动又各自触发 <Configure>）。
                if (window.winfo_x(), window.winfo_y()) != (target_x, target_y):
                    window.wm_geometry(f"+{target_x}+{target_y}")
                return
    # 走到这里说明没有可用的锚点：交回屏幕居中。
    _center_window(window, size)


def _parse_iso_date(value: str) -> date | None:
    """尽力解析 YYYY-MM-DD 字符串，失败一律返回 None，绝不抛异常。"""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (AttributeError, ValueError):
        return None


def _parse_iso_month(value: str | None) -> tuple[int, int] | None:
    """尽力解析 YYYY-MM 字符串，返回 (year, month)；失败一律返回 None，绝不抛异常。

    只用来解析**调用方**传进来的初始月份（例如将来 ui.py 记住的上次选择），
    用户界面上已经没有手输入口，所以不需要区分「格式错误」和「月份越界」，
    统一退回 None、由调用方改用今天所在月份即可。
    """
    if not value:
        return None
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m")
    except (AttributeError, ValueError):
        return None
    return (parsed.year, parsed.month)


# ==================== 方案 3：弹窗实例复用 ====================
# 状态拆成两个模块级容器，把「窗口级」和「打开级」严格分开：
#   _win —— 窗口级：整个进程只建一次窗，只有主窗口销毁时才真正 destroy。
#   _ses —— 打开级：每次「打开 → 关闭」是一个完整周期，关闭时收尾、打开时整体重建。
# 所有回调都从这两个容器「现读」，不靠闭包捕获，因此复用后不会读到上一次的旧状态
# 格子本身跨会话复用，但每次渲染更新命令，避免回填上一月或上一年的值。
_win: SimpleNamespace | None = None
_ses: SimpleNamespace | None = None
_prewarm: SimpleNamespace | None = None


class _CalendarWindow(ManagedToplevel):
    """先建立可接管的缓存，再允许CTk标题栏处理进入事件循环。"""

    def __init__(self, parent: tk.Tk | tk.Toplevel) -> None:
        self._calendar_built = False
        self._calendar_hidden_titlebar = False
        super().__init__(parent)

    def _windows_set_titlebar_color(self, color_mode: str) -> None:
        if self._calendar_built:
            self._calendar_hidden_titlebar = not self.winfo_ismapped()
            try:
                super()._windows_set_titlebar_color(color_mode)
            finally:
                self._calendar_hidden_titlebar = False
        # CTk构造期间的update会让点击重入尚未完成的建窗，留到外壳/缓存建立后再做。

    def after(self, ms, func=None, *args):
        if self._calendar_hidden_titlebar and getattr(func, "__name__", "") in ("focus", "focus_set"):
            return None  # 隐藏建窗未占用焦点，不安排CTk的父控件焦点恢复，避免覆盖新的用户焦点。
        return super().after(ms, func, *args)  # type: ignore[reportArgumentType] # Tk运行时支持func=None，保留父类转发语义。

    def finish_calendar_setup(self) -> None:
        self._calendar_built = True
        try:
            self._windows_set_titlebar_color(self._get_appearance_mode())
            self.withdraw()  # 标题栏的延迟恢复须保留隐藏意图，不能显示预创建外窗。
        finally:
            self.finish_setup()  # 兑现构建期间的关闭请求，复用已有ManagedToplevel保护。

    def destroy(self) -> None:
        if _win is not None and _win.dialog is self and _ses is not None and _ses.open:
            _close()  # 构建中的销毁会被延期；先唤醒重入的选择，外层构建才能继续收尾。
        super().destroy()


@dataclass
class _GridCell:
    button: ctk.CTkButton
    text: str = ""
    selected: bool = False
    visible: bool = False


class _GridChanges(TypedDict, total=False):
    # 明确配置键和值类型，避免宽泛的**dict类型把bool参数require_redraw也视作object。
    text: str
    fg_color: str
    hover_color: str
    text_color: str
    state: str


def _append_grid_cell(grid: ctk.CTkFrame, cells: list[_GridCell], font: tuple[str, int]) -> None:
    # 预创建与正常渲染共用同一构造入口，提前点击可以直接补齐已创建的控件池。
    cells.append(_GridCell(ctk.CTkButton(
        grid, text="", state=tk.DISABLED, width=36, height=30,
        corner_radius=8, font=font, fg_color="#FFFFFF",
        hover_color=_SUBTLE_HOVER_COLOR, text_color=_TEXT_COLOR,
    )))


def _update_grid_cell(
    cell: _GridCell, index: int, text: str, command: Callable[[], None] | None,
    selected: bool, columns: int,
) -> None:
    # 隐藏预创建与正式翻页共用更新逻辑，避免预创建布局和实际显示的控件状态漂移。
    visible = command is not None
    changes: _GridChanges = {}
    if cell.text != text:
        changes["text"] = text  # 复用已有文字，首次点击也不必重新建立同一格子的Label。
    if cell.selected != selected:
        changes.update(
            fg_color=_ACCENT_COLOR if selected else "#FFFFFF",
            hover_color=_ACCENT_HOVER_COLOR if selected else _SUBTLE_HOVER_COLOR,
            text_color="#FFFFFF" if selected else _TEXT_COLOR,
        )
    if cell.visible != visible:
        changes["state"] = tk.NORMAL if visible else tk.DISABLED
    if visible or changes:
        cell.button.configure(command=command, **changes)  # 每次更新本次命令，清空隐藏格子的旧命令。
    if cell.visible != visible:
        if visible:
            row, column = divmod(index, columns)
            cell.button.grid(row=row, column=column, padx=1, pady=1, sticky="nsew")
        else:
            cell.button.grid_forget()  # 清除CTk缩放的布局记录，空格不会随缩放重新出现。
    cell.text, cell.selected, cell.visible = text, selected, visible


def _render_grid(
    grid: ctk.CTkFrame,
    cells: list[_GridCell],
    items: list[tuple[str, Callable[[], None] | None, bool]],
    columns: int,
    font: tuple[str, int],
) -> None:
    """日期与月份共用格子复用逻辑，只重绘有变化的内容。"""
    for index, (text, command, selected) in enumerate(items):
        if index == len(cells):
            # 控件属于外窗，首次使用才创建；翻页及重新打开只更新这份引用。
            _append_grid_cell(grid, cells, font)
        _update_grid_cell(cells[index], index, text, command, selected, columns)


def _render_month() -> None:
    """按本次年月更新日期格，固定保留最多六周的控件。"""
    win = _win
    ses = _ses
    assert win is not None and ses is not None  # 固定本次调用的容器引用，供静态检查收窄类型。
    state = ses.state
    year = int(state["year"])
    month = int(state["month"])
    selected = state["selected"]
    _update_header_title()

    # 注意：monthcalendar 是 calendar 模块的**函数**，Calendar **实例**上并没有它，
    # 实例对应的方法是 monthdayscalendar。若误调 monthcalendar 会直接抛 AttributeError，
    # 而异常发生在按钮回调里不会关窗口，表现就是「弹窗在、日期全空白」。
    # 用 monthdayscalendar 还顺便避开了模块级函数依赖全局 firstweekday 的问题：
    # 全局状态一旦被其他代码改过，下面的星期表头就会和日期列对不上。
    # 返回值为按周分行的二维列表，本月之外的日子用 0 补齐。
    weeks = _CALENDAR.monthdayscalendar(year, month)
    days = [day for week in weeks for day in week]
    days.extend([0] * (42 - len(days)))  # 四/五周月份也清空隐藏第六周，防止旧日期残留。
    items: list[tuple[str, Callable[[], None] | None, bool]] = []
    for day in days:
        if day == 0:
            items.append(("", None, False))
        else:
            # 钉住格子显示的日号，年月由_choose读取当前会话；高亮仍比较完整日期。
            items.append((str(day), lambda picked=day: _choose(picked),
                          selected == date(year, month, day)))
    _render_grid(win.day_grid, win.day_cells, items, 7, win.font)


def _shift_month(delta: int) -> None:
    """上一月 / 下一月：跨年时自动借位（1 月减一月 → 上一年 12 月）。"""
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    state = ses.state
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
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    state = ses.state
    ses.result[0] = date(int(state["year"]), int(state["month"]), day).isoformat()
    _close()


def _goto_today() -> None:
    """「今日」按钮：跳回今天所在月份并高亮今天（不关闭弹窗，仍需点日期确认）。"""
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    today = ses.today
    ses.state["year"], ses.state["month"], ses.state["selected"] = (
        today.year,
        today.month,
        today,
    )
    _render_month()


def _update_header_title() -> None:
    """按当前模式刷新顶部标题文字（日期模式「2026 年 9 月」/ 月份模式「2026 年」）。

    header 两种模式共用，所以文字必须跟着模式走。两个 render 各调一次是主路径；
    _apply_mode_chrome 和 render 共用它；隐藏准备与后续翻页均更新本次年月，
    显现时不会挂着上一个模式的文字。
    """
    win = _win
    ses = _ses
    assert win is not None and ses is not None  # 固定本次调用的容器引用，供静态检查收窄类型。
    if ses.mode == "month":
        text = f"{int(ses.month_state['year'])} 年"
    else:
        state = ses.state
        text = f"{int(state['year'])} 年 {int(state['month'])} 月"
    if win.month_label.cget("text") != text:
        win.month_label.configure(text=text)  # 重复打开同一年月时不重画未变的标题。


def _shift_cursor(delta: int) -> None:
    """◀ / ▶ 两个箭头按钮的统一入口：日期模式翻月，月份模式翻年。

    箭头在 _ensure_window 里创建时就把 command 绑到本函数（不按模式各绑一份），
    切换模式只需改这里的判断。若改为「按模式重绑 command」，就得在每一个切换
    路径上都记得重绑，漏掉一条就会在特定切换顺序下翻错单位，而那种 bug 只在
    用户按特定顺序来回切时才会冒出来。
    """
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    if ses.mode == "month":
        _shift_year(delta)
    else:
        _shift_month(delta)


def _shift_year(delta: int) -> None:
    """上一年 / 下一年：年份直接加减即可。

    这里**没有** _shift_month 那套跨年借位（1 月减一个月 → 上一年 12 月）：
    月份模式翻的是整年，12 月/1 月根本不参与计算。
    """
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    ses.month_state["year"] = int(ses.month_state["year"]) + delta
    _render_months()


def _render_months() -> None:
    """按 _ses.month_state 里的年份重绘 12 个月份格（4 列 × 3 行）。"""
    win = _win
    ses = _ses
    assert win is not None and ses is not None  # 固定本次调用的容器引用，供静态检查收窄类型。
    month_state = ses.month_state
    year = int(month_state["year"])
    selected = month_state["selected"]
    _update_header_title()
    items: list[tuple[str, Callable[[], None] | None, bool]] = []
    for month in range(1, 13):
        # 「这一格代表哪个月」连同年份一起用默认参数钉死在闭包里，回调拿到的是
        # 完整 "YYYY-MM"：窗口上显示的年份与写回的结果因此不可能不一致
        # （重绘与点击之间年份被改过的情况也不怕）。
        iso = f"{year:04d}-{month:02d}"
        # 只有「年 + 月」都与选中项一致才高亮。selected 存的是 "YYYY-MM" 字符串
        # 而不是月份数字，正是为了这一句：只比月份的话，翻到 2025 年时 9 月那格
        # 还会亮着，看起来像「2025-09 已被选中」。
        items.append((f"{month} 月", lambda picked=iso: _choose_month(picked), selected == iso))
    _render_grid(win.month_grid, win.month_cells, items, 4, win.font)


def _choose_month(iso: str) -> None:
    """点击某个月份：写回结果并关闭弹窗（关闭 = 收尾 + 隐藏 + 唤醒等待方）。"""
    # 直接写闭包里带进来的完整值，不再从 month_state 现算：格子上显示的年份与
    # 写回的结果因此不可能不一致。返回值仍是严格的 YYYY-MM（补零）。
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    ses.result[0] = iso
    _close()


def _goto_this_month() -> None:
    """「本月」按钮：跳到今天所在年份并高亮本月（不关闭弹窗，仍需点一格确认）。

    刻意**不动** _ses.state：两种模式共用一个窗口实例，顺手改掉日期模式的年月会让
    用户切回日期模式时莫名被跳到今天那一月。月份模式只改自己那份 month_state。
    """
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    today = ses.today
    ses.month_state["year"] = today.year
    ses.month_state["selected"] = f"{today.year:04d}-{today.month:02d}"
    _render_months()


def _ensure_month_body() -> ctk.CTkFrame:
    """懒创建月份模式主体（只有 12 格网格；顶部标题与箭头沿用 header 那一份）。

    首次预创建或切到月份模式时建一次，之后复用。返回容器本身，方便调用方直接
    链式 pack(...)。本函数只建窗口级控件树、不读 _ses，所以不存在「复用后撞上
    上一次打开的状态」这类问题。
    """
    win = _win
    assert win is not None  # 固定本次调用的窗口引用，供静态检查收窄类型。
    if win.month_body is not None:
        return win.month_body
    body = ctk.CTkFrame(win.dialog, fg_color="transparent")
    month_grid = ctk.CTkFrame(body, fg_color="transparent")
    # 内边距与日期模式的 day_grid 对齐（左右 16、上方 6）：两种模式的网格左右边缘
    # 才会重合，来回切换时不会左右挪一下。
    month_grid.pack(fill="both", expand=True, padx=16, pady=(6, 0))
    # 4 列 × 3 行：列宽用 uniform 拉平；行高交给 expand 均分剩余空间，于是多出来的
    # 高度会变成更高的按钮（与日期模式是同一套机制，不用额外算高度）。
    for column in range(4):
        month_grid.grid_columnconfigure(column, weight=1, uniform="month")
    for row in range(3):
        month_grid.grid_rowconfigure(row, weight=1)
    win.month_body = body
    win.month_grid = month_grid
    return body


def _cancel() -> None:
    """取消：保持 result[0] 为 None，调用方据此判断用户放弃选择。"""
    _close()


def _apply_mode_chrome(mode: str) -> None:
    """按模式调整「窗口外壳」：主体显隐、底部左键语义、顶部标题文字。

    外壳 = 两种模式共用、但语义要跟着模式变的那几件东西。标题栏文字与窗口尺寸
    不在这里（它们在 _begin_session，要和预置坐标的先后顺序放在一起看），
    高亮与网格内容也不在这里（由 _render_month / _render_months 负责）。
    """
    win = _win
    assert win is not None  # 固定本次调用的窗口引用，供静态检查收窄类型。
    is_month = mode == "month"
    # 【主体显隐】pack_forget() 会把 packing 选项整个丢掉，所以重新 pack 时必须
    # **写全所有选项**（fill / expand），并且带上 before=_win.footer——否则新主体会
    # 追加到按钮后面去，画面上就是「网格跑到两个按钮下面」。
    if is_month:
        if win.date_body.winfo_manager():
            win.date_body.pack_forget()
        body = _ensure_month_body()
        if body.winfo_manager() != "pack":
            body.pack(fill="both", expand=True, before=win.footer)
    else:
        # 月份主体可能还没建过（用户从没切到过月份模式），所以先判空再 forget。
        if win.month_body is not None and win.month_body.winfo_manager():
            win.month_body.pack_forget()
        if win.date_body.winfo_manager() != "pack":
            win.date_body.pack(fill="both", expand=True, before=win.footer)
    # 【底部左键】按钮是窗口级、只建一次，语义却按模式变（今日 / 本月），
    # 所以每次打开都要重设文字与命令；漏一次就会带着上一个模式的语义开着。
    text = "本月" if is_month else "今日"
    if win.footer_left.cget("text") != text:
        # 同模式复用保留现有命令与布局，避免重绘底部并触发无效Configure。
        win.footer_left.configure(text=text, command=_goto_this_month if is_month else _goto_today)
    # 标题和网格都在隐藏期间更新，显现时只露出本次模式的内容。
    _update_header_title()


def _recenter(_event: tk.Event | None = None) -> None:
    """按「真实尺寸」把窗口重新贴到输入框下方，且只在尺寸变化时动手。

    不能用 winfo_reqwidth() 预算：CustomTkinter 会按 DPI 缩放控件，Tk 随后还会把
    窗口撑到内容所需大小，一次算出的坐标必然偏斜；而窗口未映射时 winfo_width()
    又会谎报 1。所以监听 <Configure>，用当时的真实宽高重算，自我校正。

    但 <Configure> 在「尺寸变化」和「位置变化」时**都会**触发，所以不能每次都重摆：
    用户拖动标题栏 → 坐标改变 → 若此时重新贴位，窗口会被立刻拽回输入框旁边，
    手感上就是「窗口拖不动」。因此只在尺寸变化时重摆，纯位移直接忽略。

    尺寸变化时也仍然按**锚点**（而不是屏幕）重算，这样换月导致行数从 6 行变 5 行、
    弹窗变矮时，它会自己重新贴回输入框下方，而不是跑到屏幕中央去。
    """
    win = _win
    ses = _ses
    assert win is not None and ses is not None  # 固定本次调用的容器引用，供静态检查收窄类型。
    dialog = win.dialog
    width, height = dialog.winfo_width(), dialog.winfo_height()
    # 窗口尚未映射时 winfo_width() 只返回 1，用它算出的坐标毫无意义，
    # 重摆反而会让窗口先闪一下再归位，所以跳过，等真正的尺寸事件。
    if width <= 1 or height <= 1:
        return
    size = (width, height)
    # last_size 存的是元组本身；若存成 [元组] 的列表，下面 `size == last_size`
    # 会变成「元组 == 列表」，恒为 False，守卫就失效了。
    if size == ses.last_size and ses.ready:
        return  # 尺寸没变却收到事件 → 是拖动产生的位移，尊重用户摆的位置
    ses.last_size = size
    _anchor_to_input(dialog, ses.anchor, size)


def _deferred_setup() -> None:
    """首次空闲时设置图标；关闭与销毁绑定已在建窗时安装。"""
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
    _apply_app_icon(_win.dialog)


def _schedule_reveal(win: SimpleNamespace, session: SimpleNamespace) -> None:
    """映射后等布局与绘制空闲，再一次显现；新的几何事件会重新排队。"""
    if _win is not win or _ses is not session or not session.open or session.ready:
        return
    dialog = win.dialog
    if session.reveal_idle[0] is not None:
        dialog.after_cancel(session.reveal_idle[0])

    def geometry() -> tuple[int, int, int, int]:
        return dialog.winfo_width(), dialog.winfo_height(), dialog.winfo_x(), dialog.winfo_y()

    def alive() -> bool:
        # 旧会话的便条即使已出队，也不能显现复用窗口中的新会话。
        return _win is win and _ses is session and session.open and not session.ready

    def grid_mapped() -> bool:
        # Windows会分批映射子窗口：外窗尺寸稳定并不代表格子/文字已能显示。
        # 等待当前有效格子的绘图Canvas；空格/另一模式及布局裁掉的文字不应阻挡就绪。
        cells = win.month_cells if session.mode == "month" else win.day_cells
        return all(
            cell.button.winfo_ismapped() and all(
                child.winfo_ismapped() for child in cell.button.winfo_children()
                if isinstance(child, tk.Canvas)
            )
            for cell in cells if cell.visible
        )

    def settle() -> None:
        if not alive():
            return
        session.reveal_idle[0] = None
        if not dialog.winfo_ismapped() or not grid_mapped():
            return  # 等本窗/子控件的Map事件，不能对未完成的映射猜就绪。
        _recenter()
        settled = geometry()

        def reveal() -> None:
            if not alive():
                return
            session.reveal_idle[0] = None
            if not dialog.winfo_ismapped() or not grid_mapped():
                return
            if geometry() != settled:
                _schedule_reveal(win, session)
                return  # 定位/尺寸仍在改变，继续保持透明，等下一轮布局完成。
            # 第二轮idle让定位产生的Configure及子控件绘制先完成，不使用固定延时或update重入。
            dialog.grab_set()
            session.ready = True
            dialog.attributes("-alpha", 1.0)

        session.reveal_idle[0] = dialog.after_idle(reveal)

    session.reveal_idle[0] = dialog.after_idle(settle)


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
    for slot in (_ses.setup_idle, _ses.reveal_idle):
        if slot[0] is not None:
            try:
                dialog.after_cancel(slot[0])
            except (tk.TclError, ValueError):
                pass  # 销毁期间任务可能已被Tk取消，仍需清空本次句柄。
            slot[0] = None
    # ② 解绑 <Configure>：当初用 tk.Misc.bind 就是为了拿到 funcid，这里按 funcid 精确摘除。
    #    不能用 dialog.unbind(...)：CTk 的覆写收到非 None 的 funcid 会抛 ValueError，
    #    而 CTkEntry/CTkFrame 的 bind 又根本不返回 funcid。
    for widget, event, slot in (
        (dialog, "<Configure>", _ses.configure_funcid),
        (dialog, "<Map>", _ses.map_funcid),
        (_ses.parent, "<Destroy>", _ses.parent_funcid),
    ):
        if slot[0] is not None:
            try:
                tk.Misc.unbind(widget, event, slot[0])
            except (tk.TclError, ValueError):
                pass  # 父窗销毁时绑定可能已消失，不妨碍释放grab和唤醒等待。
            slot[0] = None
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
    # 按模式分别记录：日期弹窗记 320x380、月份弹窗记 320x300，各记各的，
    # 否则下一次打开会拿到另一个模式的高度，第一帧就错位（#3.1 修掉的 A1 同类病）。
    if _ses.ready and _win.dialog.winfo_width() > 1:
        _win.last_size[_ses.mode] = (
            _win.dialog.winfo_width(),
            _win.dialog.winfo_height(),
        )
    try:
        # 保留CTk的隐藏意图标记，防止标题栏after(5)恢复把已取消窗口再次显示。
        _win.dialog.withdraw()
    except tk.TclError:
        pass
    _ses.open = False
    _wake()


def _on_destroy(event: tk.Event) -> None:
    """父窗退出或外部销毁时唤醒等待方，并释放已失效的窗口缓存。"""
    global _win
    if _win is None or event.widget is not _win.dialog:
        return
    _cancel_prewarm()  # 外部销毁预创建窗后不能继续往失效控件池追加格子。
    _cleanup()  # 真正销毁也要摘除本次绑定/idle句柄，不能留下旧会话任务。
    if _ses is not None:
        _ses.open = False
    _wake()
    _win = None  # 编辑父窗销毁后仍可从主窗再打开，避免复用已经失效的格子。


def _ensure_window(parent: tk.Tk | tk.Toplevel, mode: str) -> None:
    if _win is not None:
        return
    root = parent._root()  # type: ignore[reportAttributeAccessIssue] # Tk运行时提供_root，类型声明遗漏；保留取得真正根窗的行为。
    begin_build = getattr(root, "_begin_calendar_build", None)
    end_build = getattr(root, "_end_calendar_build", None)
    if begin_build is not None:
        begin_build()  # 主窗退出保护也覆盖预创建尚未启动时的直接点击，不只覆盖后台入口。
    try:
        _build_window(parent, mode)
    except Exception:
        # 外壳构造失败时尚未写入_win，也须释放半成品及ManagedToplevel的构建保护。
        for widget in tuple(parent.children.values()):
            if isinstance(widget, _CalendarWindow):
                widget.finish_setup()
                widget.destroy()
        raise
    finally:
        if end_build is not None:
            end_build()  # 经UI的成对回调兑现退出，避免calendar_picker反向import主窗模块。


def _build_window(parent: tk.Tk | tk.Toplevel, mode: str) -> None:
    """首次预创建或打开时建立外壳，之后一直复用同一个实例。

    窗口级的东西（控件树、字体）只在这里建一次；打开级状态一律不进这里，
    全部由 ask_date / ask_month 每次打开时重建。回调都是模块级函数、一律现读
    _win / _ses，所以不存在「闭包捕获到上一次状态」的问题。

    mode 只影响**建窗那一刻**的尺寸提示：建窗可能由日期模式（ask_date）触发，
    也可能由月份模式（ask_month）触发，两种模式高度不同（见 _logical_size）。
    复用打开由_ensure_window直接返回，尺寸由每次打开的公共入口另行设置。
    """
    global _win
    dialog = _CalendarWindow(parent)
    # 构造期间暂缓标题栏update；先明确保持隐藏，后续标题栏延迟恢复也不得自动显示。
    # 透明度覆盖后续为取得真实尺寸而进行的映射，用户只看见准备完的最终布局。
    dialog.attributes("-alpha", 0.0)
    dialog.withdraw()
    dialog.title("选择日期")
    # 【Step 2.1】同 tag_picker：tk.Wm.resizable 绕开 CTkToplevel.resizable 的覆写，
    # 避免它在 Windows 上额外安排 after(10, _windows_set_titlebar_color)（实测 sync 12.5ms /
    # visible 34.8ms）。代价可忽略：_last_resizable_args 仅被写入、CTk 内部无读取点，且本弹窗尺寸固定。
    tk.Wm.resizable(dialog, False, False)
    dialog.transient(parent)
    dialog.configure(fg_color=_BG_COLOR)
    # 图标读盘留到隐藏准备阶段的idle，生命周期绑定则必须在等待前安装。
    # 先给一个尺寸提示：CTk 会把逻辑像素按 DPI 放大；随后 Tk 还可能按内容再撑大，
    # 所以最终位置交给下面的 <Configure> 回调按真实尺寸校正。
    dialog_width, dialog_height = _logical_size(mode)
    dialog.geometry(f"{dialog_width}x{dialog_height}")
    # 【A1】坐标必须就在这里预置：上面那句只给尺寸、不给坐标，坐标会被 Win32 按
    # CW_USEDEFAULT 随机分配（实测 32/96/192/224，跨进程都不一致），窗口会先在错误
    # 位置露一帧再被 _recenter 拉回（错位帧约 85ms）。这里没有 anchor（建窗时还
    # 没进入 ask_date / ask_month），所以 _anchor_to_input 会退回屏幕居中；紧接着
    # ask_date / ask_month 会用真的 anchor 再摆一次并覆盖它。两次都在 deiconify
    # 之前完成，所以仍然只露一帧。
    _anchor_to_input(dialog, None, _physical_size(dialog, mode))

    dialog_font = ("Microsoft YaHei UI", 11)

    # ---------- 顶部：◀ / 标题 / ▶ （**两种模式共用同一份标题栏**） ----------
    # 【为什么不把它放进 date_body】标题栏两种模式都要用：日期模式显示「2026 年 9 月」，
    # 月份模式显示「2026 年」，翻页/翻年也都靠它。如果塞进 date_body，切到月份模式时
    # pack_forget(date_body) 会连它一起藏掉，月份模式就得再建一份标题栏——那等于把
    # 「一个标签 + 一对箭头」复制成两套，并让「今日/本月」这类语义要在两个地方同步。
    # 所以它留在两个主体之外，切换模式只改文字与箭头语义：
    # 箭头统一走 _shift_cursor（按 _ses.mode 决定翻月还是翻年），文字走 _update_header_title。
    header = ctk.CTkFrame(dialog, fg_color="transparent")
    header.pack(fill="x", padx=16, pady=(16, 8))
    # 让中间标题占据全部剩余宽度，两侧箭头按钮保持固定大小。
    header.grid_columnconfigure(1, weight=1)

    ctk.CTkButton(
        header,
        text="◀",
        command=lambda: _shift_cursor(-1),
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
        command=lambda: _shift_cursor(1),
        width=36,
        height=32,
        corner_radius=8,
        fg_color=_SUBTLE_COLOR,
        hover_color=_SUBTLE_HOVER_COLOR,
        text_color=_TEXT_COLOR,
        font=dialog_font,
    ).grid(row=0, column=2)

    # ---------- 中部：日期模式主体（星期表头 + 日期网格） ----------
    # 用一个容器把日期模式的两块内容圈起来：切到月份模式时要能整块 pack_forget()，
    # 否则日期格子和月份网格会叠在同一个位置上。
    # 容器自身不留内边距，横向 16px 仍由里面两个子框各自保留，视觉与改造前一致。
    date_body = ctk.CTkFrame(dialog, fg_color="transparent")
    date_body.pack(fill="both", expand=True)

    # 星期表头（灰底）
    weekday_bar = ctk.CTkFrame(date_body, fg_color=_SUBTLE_COLOR, corner_radius=8)
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

    # 7 列日期网格
    day_grid = ctk.CTkFrame(date_body, fg_color="transparent")
    day_grid.pack(fill="both", expand=True, padx=16, pady=(6, 0))
    for column in range(7):
        day_grid.grid_columnconfigure(column, weight=1, uniform="day")

    # ---------- 底部：今日 / 本月 / 取消 ----------
    footer = ctk.CTkFrame(dialog, fg_color="transparent")
    footer.pack(fill="x", padx=16, pady=(8, 16))
    # 左右两列等宽，让两个按钮宽度一致、整体对称（沿用 dialogs.py 的按钮风格）。
    footer.grid_columnconfigure((0, 1), weight=1)

    # 左键留引用：文字与命令是按模式变的（日期模式「今日」/ 月份模式「本月」），
    # 由 _apply_mode_chrome 每次打开重设；不留引用就只能把它拆成两个按钮。
    footer_left = ctk.CTkButton(
        footer,
        text="今日",
        command=_goto_today,
        height=34,
        corner_radius=9,
        fg_color=_SUBTLE_COLOR,
        hover_color=_SUBTLE_HOVER_COLOR,
        text_color=_TEXT_COLOR,
        font=dialog_font,
    )
    footer_left.grid(row=0, column=0, sticky="ew", padx=(0, 6))
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
        day_cells=[],  # 格子与外窗同寿命，会话关闭后保留；选择状态由每次渲染覆盖。
        month_cells=[],
        # 日期模式的主体容器：切到月份模式时靠它 pack_forget()。
        date_body=date_body,
        # 底部按钮栏的引用：月份主体是后建的，pack 时必须写成 pack(before=footer)
        # 才能排在按钮上方，否则会掉到按钮下面去。
        footer=footer,
        # 底部左键（今日 / 本月）：文字与命令按模式变，见 _apply_mode_chrome。
        # 不留引用的话就只剩「把它拆成两个按钮」这一条路。
        footer_left=footer_left,
        # 月份模式主体：懒创建（第一次切到月份模式才建），先在 _win 里占位 None；
        # 就绪后由 _ensure_month_body 填进来，_render_months 直接读 month_grid。
        month_body=None,
        month_grid=None,
        setup_done=False,
        # 上一次真正显示时量到的尺寸，**按模式分开记**；值为 None 表示该模式还没
        # 成功显示过（首次打开）。不分开记的话，「日期 → 月份」的下一次打开会拿
        # 日期模式的 380 去预置月份模式的 300，窗口第一帧就会错位。
        last_size={"date": None, "month": None},
    )
    # 未显现时也允许取消/父窗销毁；不能把这些兜底绑定推迟到首次显示之后。
    dialog.protocol("WM_DELETE_WINDOW", _cancel)
    dialog.bind("<Escape>", lambda _event: _cancel())
    dialog.bind("<Destroy>", _on_destroy, add="+")
    dialog.finish_calendar_setup()  # 此时重入的点击已有完整外壳可接管，不会重复创建外窗。


def _cancel_prewarm() -> None:
    """取消后台准备的便条；已经创建的格子留给正常打开复用。"""
    global _prewarm
    warm, _prewarm = _prewarm, None
    if warm is None:
        return
    for slot in (warm.idle, warm.timer):
        if slot[0] is not None:
            try:
                warm.parent.after_cancel(slot[0])
            except (tk.TclError, ValueError):
                pass  # 父窗销毁时任务可能已被取消，仍要清空状态。
            slot[0] = None
    if warm.destroy_binding is not None:
        try:
            tk.Misc.unbind(warm.parent, "<Destroy>", warm.destroy_binding)
        except (tk.TclError, ValueError):
            pass  # 销毁路径中父窗绑定可能已失效。


def shutdown(parent: tk.Tk | tk.Toplevel) -> None:
    """父窗退出时取消准备并关闭其拥有的缓存，亦可唤醒建窗期间重入的选择。"""
    if _prewarm is not None and _prewarm.parent is parent:
        _cancel_prewarm()
    win = _win
    if win is None:
        return
    owner = win.dialog.master
    while owner is not None and owner is not parent:
        owner = owner.master
    if owner is parent:
        win.dialog.destroy()  # ManagedToplevel在标题栏处理完成前仅记关闭意图。


def prewarm(parent: tk.Tk | tk.Toplevel) -> None:
    """主窗显示后调用：隐藏建立外壳，再在主线程分批准备日期/月控件。"""
    global _prewarm
    if _win is not None or _prewarm is not None:
        return  # 用户已先打开或已有准备任务，不能重建缓存/改写当前选择。
    warm = SimpleNamespace(parent=parent, idle=[None], timer=[None], destroy_binding=None)
    _prewarm = warm

    def on_destroy(event: tk.Event) -> None:
        if event.widget is parent and _prewarm is warm:
            _cancel_prewarm()

    warm.destroy_binding = tk.Misc.bind(parent, "<Destroy>", on_destroy, add="+")
    try:
        _ensure_window(parent, "date")
    except Exception as exc:
        # 预创建失败不能中断主界面；回收半成品，正常点击仍可走原来的懒创建路径。
        shutdown(parent)
        _cancel_prewarm()
        print(f"警告：日历预创建失败，将在点击时重新创建（{exc}）。")
        return
    win = _win
    if _prewarm is not warm or win is None:
        return  # CTk标题栏update期间可能已被点击接管或收到退出请求。
    _deferred_setup()  # 图标也在隐藏阶段准备，避免首次点击重复读盘。
    today = date.today()
    days = [day for week in _CALENDAR.monthdayscalendar(today.year, today.month) for day in week]
    days.extend([0] * (42 - len(days)))  # 只准备当前月外观，不建立_ses或持有用户选择。

    def placeholder() -> None:
        pass  # 预创建窗口始终隐藏；正常打开时渲染必须替换所有有效格子的选择命令。

    def queue_batch() -> None:
        if _prewarm is not warm:
            return
        warm.idle[0] = None
        # idle后再排一个短timer，让输入与这一轮绘制有机会完成，不连续占用空闲队列。
        warm.timer[0] = parent.after(1, batch)

    def batch() -> None:
        if _prewarm is not warm or _win is not win:
            return
        warm.timer[0] = None
        try:
            deadline = perf_counter() + 0.004
            for _ in range(6):
                if len(win.day_cells) < 42:
                    grid, cells = win.day_grid, win.day_cells
                    day = days[len(cells)]
                    text, command, selected, columns = (
                        str(day) if day else "", placeholder if day else None, day == today.day, 7,
                    )
                elif len(win.month_cells) < 12:
                    _ensure_month_body()
                    grid, cells = win.month_grid, win.month_cells
                    month = len(cells) + 1
                    text, command, selected, columns = f"{month} 月", placeholder, month == today.month, 4
                else:
                    _cancel_prewarm()
                    return
                _append_grid_cell(grid, cells, win.font)
                _update_grid_cell(cells[-1], len(cells) - 1, text, command, selected, columns)
                if perf_counter() >= deadline:
                    break  # 至少创建一个，单个控件/外壳构造不可拆分，预算是让出循环的阈值。
            warm.idle[0] = parent.after_idle(queue_batch)
        except Exception as exc:
            _cancel_prewarm()  # 部分已创建控件可由正常渲染补齐，不影响用户继续操作。
            print(f"警告：日历分批预创建已停止，剩余控件将在点击时创建（{exc}）。")

    warm.idle[0] = parent.after_idle(queue_batch)


def _begin_session(
    parent: tk.Tk | tk.Toplevel,
    mode: str,
    title: str,
    anchor: tk.Misc | None,
    year: int,
    month: int,
) -> bool:
    """打开一次会话：收尾上一次 → 隐藏建窗 → 按模式铺好外壳 → 预置位置。

    日期模式与月份模式的开窗流程逐字相同，只有「模式 / 标题 / 初始年月」不同，
    所以整段抽到这里，两个对外入口各自只留「自己独有的那一两行」（高亮的是哪一天、
    哪一月），见 ask_date / ask_month。

    year / month 是**初始展示**的年月，两种模式共用：日期模式来自 initial_date
    解析出的年月，月份模式来自 initial_month。高亮值不进本函数——两种模式的高亮
    语义不同（某一天 vs 某个 "YYYY-MM"），让调用方各写各的更直白。
    """
    global _ses

    _cancel_prewarm()  # 点击优先：取消剩余批次，并在同一缓存上补齐本次需要的控件。
    if getattr(parent, "_calendar_closing", False) or (
        isinstance(parent, ManagedToplevel) and parent.closing
    ):
        return False  # 退出期间不再接受重入的点击请求。

    # 【打开前】先把上一次遗留的会话收干净：单例复用要求任何时刻最多只有一个日历在等，
    # 否则会出现「两个弹窗同时可见」和「两次 wait_variable 同时挂起」。
    if _ses is not None and _ses.open:
        _close()

    # 首次创建及复用都先隐藏更新；真实尺寸必须映射后量，显现留给_finish_session。
    _ensure_window(parent, mode)
    win = _win
    if win is None or win.dialog.closing:
        return False  # 建窗期间退出已兑现，调用方直接返回取消而非等待失效窗口。
    dialog = win.dialog
    dialog.attributes("-alpha", 0.0)
    dialog.withdraw()
    # parent 每次可能不同（主窗口 / 编辑记录弹窗），transient 必须每次重设：
    # 认错父窗口会导致弹窗跑到主窗口下面，或跟着父窗口一起最小化消失。
    if str(dialog.transient()) != str(parent):
        dialog.transient(parent)  # 仅父窗变化时重设，重复wm transient也会触发显示/布局。

    today = date.today()
    # 【打开级状态】每次打开整体重建，所有字段都是「本次打开」的新值。用模块级 _ses
    # 承载、回调一律现读，因此 state / result / signal 天然是「跨打开长期存活且始终
    # 指向本次」的那一份，日期/月份格子的闭包不会串到上一次打开。
    _ses = SimpleNamespace(
        open=True,
        ready=False,
        # 必须清 None：不清的话上一次的选择会变成这一次的返回值。
        result=[None],
        # 每次新建：wait_variable 等的是「这一次」的信号。
        signal=tk.IntVar(master=dialog),
        today=today,
        # 日期模式的展示状态（当前年月 + 高亮日期）。selected 先摆 None，由 ask_date
        # 回填具体日期：只有年月日全等才高亮，所以必须回填完整日期、不能只回填年月。
        state={"year": year, "month": month, "selected": None},
        # 本次打开的弹窗模式：ask_date 传 "date"、ask_month 传 "month"。它决定
        # _win.last_size 用哪个键取尺寸、窗口尺寸，以及 ◀ ▶ 翻的是月还是年。
        mode=mode,
        # 【月份模式专用】当前展示年份 + 被选中的月份。刻意与上面的 state 分开放：
        # 两份 dict 里都有「月份」，但语义不同（state["month"] 是「当前展示的月」，
        # month_state["selected"] 是「被选中的是哪个月」），合用一个 dict 必然互相污染。
        # selected 存 **"YYYY-MM" 字符串**而不是月份数字：只存数字（如 9）时，用户把
        # 年份翻到 2025，9 月那格仍会亮着，看起来像「2025-09 已被选中」。
        month_state={"year": year, "selected": None},
        # 必须复位：不复位时复用打开「尺寸没变」，_recenter 会把重摆逻辑整个拦掉，
        # 弹窗就会停在用户上次拖动到的位置。
        last_size=None,
        # 本次打开的锚点（日期输入框）。_recenter 在尺寸变化时要靠它重算位置，
        # 所以必须跟着「本次打开」每回重建，不能留在窗口级的 _win 里。
        anchor=anchor,
        # 单元素列表当句柄槽，回调内部可原地改写，关闭时统一取消/解绑。
        setup_idle=[None],
        reveal_idle=[None],
        configure_funcid=[None],
        map_funcid=[None],
        parent_funcid=[None],
        parent=parent,
    )
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。

    # 【每次打开都要重设标题栏】建窗时只设过一次；复用打开若不重设，月份弹窗会顶着
    # 「选择日期」的标题栏（反之亦然）——标题是窗口级的，不会随模式自己变。
    if dialog.title() != title:
        dialog.title(title)  # 复用同一模式时省去未变化的原生标题更新。
    # 【每次打开都要重设尺寸】同理：复用后窗口还留着上一个模式的高度（380 / 300）。
    # 逻辑尺寸由 _logical_size 统一给出，CTk 的 geometry() 会按 DPI 把宽高放大。
    dialog_width, dialog_height = _logical_size(mode)
    size = f"{dialog_width}x{dialog_height}"
    current_size = dialog.geometry().split("+", 1)[0].split("-", 1)[0]
    if current_size != size:
        dialog.geometry(size)  # 保留跨模式/缩放尺寸校正，跳过同尺寸重布局。

    # 【模式外壳】必须早于下面的 deiconify：主体显隐与底部左键语义要在窗口显示前就位，
    # 否则会先按上一个模式的样子闪一帧（比如月份模式下先闪出一片日期网格 + 「今日」）。
    _apply_mode_chrome(mode)

    # 应用图标只设置一次。若首次打开的idle还没跑就被关掉，_cleanup会取消它，
    # 此时 setup_done 仍为 False，下一次打开会重新安排，不会永久丢失。
    if not win.setup_done and ses.setup_idle[0] is None:
        ses.setup_idle[0] = dialog.after_idle(_deferred_setup)

    # 【预置坐标】deiconify 之前先摆好，窗口第一帧就落在正确位置，不会先在
    # 用户上次拖到的位置露一帧再跳回来（实测那段错位帧 29~46ms）。
    # 尺寸优先用「该模式上一次显示量到的真实尺寸」；该模式首次打开时没有可信尺寸，
    # 用逻辑常量换算（不能读 winfo_width()，未映射时它只会谎报 200）。
    # 两条路径都走 _anchor_to_input：anchor 可用就贴输入框，不可用才退回屏幕居中。
    _anchor_to_input(dialog, anchor, win.last_size[mode] or _physical_size(dialog, mode))

    def on_geometry(event: tk.Event) -> None:
        if _ses is not ses or not ses.open:
            return  # 旧会话事件不参与本次就绪判断。
        if event.widget is dialog:
            _recenter()
        elif ses.ready:
            return  # 显现后子控件布局不再触发外窗定位/显现任务。
        _schedule_reveal(win, ses)

    def on_parent_destroy(event: tk.Event) -> None:
        if event.widget is parent and _ses is ses and ses.open:
            _close()  # 缓存窗的创建父窗可能不同于本次transient父窗，后者退出也必须取消。

    # add保留CTk自身的尺寸跟踪；关闭时按funcid解绑，避免重复打开叠加回调。
    ses.configure_funcid[0] = tk.Misc.bind(dialog, "<Configure>", on_geometry, add="+")
    ses.map_funcid[0] = tk.Misc.bind(dialog, "<Map>", on_geometry, add="+")
    ses.parent_funcid[0] = tk.Misc.bind(parent, "<Destroy>", on_parent_destroy, add="+")
    return True


def _finish_session(render: Callable[[], None], what: str) -> str | None:
    """隐藏渲染 → 透明映射/校位 → 就绪显现 → 等待本次选择。

    两种模式的后半段逐字相同，只有「渲染什么」和告警里的名词不同，所以也抽到这里。
    render 传的是**函数对象**（_render_month / _render_months）而不是调用结果：它必须
    在 try 里面跑，渲染失败才能走「隐藏 + 返回 None」，而不是留下一个关不掉的空壳。
    """
    win = _win
    assert win is not None  # 固定本次调用的窗口引用，供静态检查收窄类型。
    dialog = win.dialog

    # 首次绘制必须在控件创建之后。这里额外包一层容错，但复用后**不能再 destroy**：
    # 渲染失败与用户取消走同一条路（隐藏 + 返回 None），否则会留下一个关不掉的
    # 空壳窗口，用户以为程序卡死了。
    try:
        render()
    except Exception as exc:  # noqa: BLE001 - 选择器是纯辅助功能，任何意外都不应影响主界面
        print(f"警告：{what}渲染失败，已取消本次选择（{exc}）。")
        _close()
        return None

    # 锁住焦点并阻塞等待。这里不能用 wait_window：复用后关闭只是 withdraw，窗口永不
    # 销毁，wait_window 会一直不返回。改为等一个每次打开都新建的变量，由 _close()
    # （用户选中/取消）与 _on_destroy（主窗口退出）负责唤醒。
    session = _ses
    assert session is not None  # 保留等待前的取值时机，并收窄已有会话别名。
    if session.open:
        tk.Wm.deiconify(dialog)  # alpha仍为0，映射事件取得真实尺寸后再显现。
        _schedule_reveal(win, session)
        dialog.wait_variable(session.signal)
    return session.result[0]


def ask_date(parent: tk.Tk | tk.Toplevel, initial_date: str, anchor: tk.Misc | None = None) -> str | None:
    """弹出日历选择器。

    Args:
        parent: 父窗口，弹窗会以 transient 方式挂在它上面。
        initial_date: 预选日期（YYYY-MM-DD），同时决定初始展示的月份。
        anchor: 锚点控件（日期输入框）：弹窗左边缘与它左边缘对齐、上边缘贴在它
            下边缘再往下 _GAP_ABOVE 像素，下方放不下时翻到它上方。不传，或传进来
            的控件已经不存在时，退回屏幕居中（见 _anchor_to_input）。

    Returns:
        选中日期（YYYY-MM-DD 字符串）；用户点「取消」、按 ESC 或点右上角关闭时返回 None。
    """
    # 初始选中的日期跟随 initial_date；传入非法/空字符串时退回今天，保证弹窗始终可用。
    initial = _parse_iso_date(initial_date) or date.today()
    if not _begin_session(parent, "date", "选择日期", anchor, initial.year, initial.month):
        return None
    # 日期模式独有的回填：_begin_session 只保证「展示的年月」对得上，具体高亮哪一天
    # 由这里写进去（_render_month 要求年月日全等才高亮，所以必须回填完整日期）。
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    ses.state["selected"] = initial
    return _finish_session(_render_month, "日历")


def ask_month(
    parent: tk.Tk | tk.Toplevel,
    initial_month: str | None = None,
    anchor: tk.Misc | None = None,
    title: str = "选择月份",
) -> str | None:
    """弹出月历选择器（issues #3.2：月份字段从「手敲」改为「点选」）。

    与 ask_date 共用同一个复用弹窗：主体换成 4 列 × 3 行的月份格，顶部 ◀ ▶ 由
    「翻月」改为「翻年」，底部左键由「今日」改为「本月」。

    Args:
        parent: 父窗口，弹窗会以 transient 方式挂在它上面。
        initial_month: 预选月份（YYYY-MM），同时决定初始展示的年份；不传或字符串
            不合法时退回今天所在月份。
        anchor: 锚点控件（月份触发按钮）：摆位规则与 ask_date 完全一致。
        title: 窗口标题栏文字。调用方带语境（如「导出账单」「月度统计」「查看图表」），
            默认值只在本函数被直接调用时用得上。

    Returns:
        选中月份（严格的 YYYY-MM 字符串）；用户点「取消」、按 ESC 或点右上角关闭时返回 None。
    """
    initial = _parse_iso_month(initial_month)
    today = date.today()
    year, month = initial if initial is not None else (today.year, today.month)
    if not _begin_session(parent, "month", title, anchor, year, month):
        return None
    # 月份模式独有的回填：与日期模式同一口径（完整值全等才高亮），所以这里写的是
    # "YYYY-MM" 字符串。initial_month 不合法时一格都不亮，但「本月」按钮仍然可用。
    ses = _ses
    assert ses is not None  # 固定本次调用的会话引用，供静态检查收窄类型。
    if initial is not None:
        ses.month_state["selected"] = f"{year:04d}-{month:02d}"
    return _finish_session(_render_months, "月份选择器")
