"""主页面的月份入口、编辑表单与删除确认；只返回决定，不执行账本写入。

编辑采用一个缓存 _EditWindow 加每次独立 _EditSession，普通取消/确认只隐藏并唤醒 wait_variable。
编辑会话拒绝重入，公共调用退栈后才释放登记；构建或父窗退出交给 ManagedToplevel 真正销毁。
日期/标签子选择器可以处理嵌套事件，返回后必须验证原编辑会话身份再回填或恢复 grab。
删除确认每次新建并销毁，等待 wait_window；不能把编辑的隐藏复用收尾照搬到此流程。
金额大小与方向在编辑确认入口合成一次，完整标签原样返回给 ui，再由 store 持久化。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
import tkinter as tk
from typing import Callable

import customtkinter as ctk
import calendar_picker
import tag_picker

# 月份选择也走 calendar_picker：本模块原来那份自绘的 CTkEntry 输入框已删除，
# ask_month 只做一层薄封装。导入时改别名 picker_ask_month，避免与本模块下面
# 同名的 ask_month 包装函数互相覆盖（后者会遮蔽前者的名字）。
from calendar_picker import ask_date, ask_month as picker_ask_month
from config import RESOURCE_DIR
from dialog_lifecycle import ManagedToplevel
from store import Account
from tag_prefs import split_tag_input
from widgets import TagChipsFrame


def _native_window_rect(window: tk.Misc) -> tuple[int, wintypes.RECT]:
    """取得包含标题栏和边框的桌面绝对矩形。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # winfo_id 是客户区句柄；64 位返回类型必须显式声明，取 GA_ROOT 包装窗才能连标题栏居中。
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    hwnd = user32.GetAncestor(window.winfo_id(), 2)
    rect = wintypes.RECT()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise OSError("窗口矩形尚未就绪")
    return hwnd, rect


def _parent_work_area(parent: tk.Misc) -> tuple[wintypes.RECT, wintypes.RECT, bool]:
    """选父窗占据面积最大的屏幕，并判定是否必须退回工作区居中。"""
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("screen", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd, parent_rect = _native_window_rect(parent)
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                         callback_type, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    # 最小化时此 API 使用最小化前的矩形；完全离屏时 2 表示选择最近显示器。
    monitor = user32.MonitorFromWindow(hwnd, 2)
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    visible = False

    @callback_type
    def inspect_monitor(handle, _dc, _rect, _data):
        nonlocal visible
        other = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not user32.GetMonitorInfoW(handle, ctypes.byref(other)):
            return False  # 不在 ctypes 回调中抛异常；枚举失败由外层统一回退。
        work = other.work
        visible |= (min(parent_rect.right, work.right) > max(parent_rect.left, work.left)
                    and min(parent_rect.bottom, work.bottom) > max(parent_rect.top, work.top))
        return True

    if not user32.EnumDisplayMonitors(None, None, inspect_monitor, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return parent_rect, info.work, bool(user32.IsIconic(hwnd)) or not visible


def center_dialog_on_parent(parent: tk.Misc, dialog: ManagedToplevel) -> bool:
    """初始布局完成后只定位一次；返回 False 表示等待布局期间已经关闭。"""
    try:
        if dialog.closing:
            return False
        # 三个调用点先透明建窗；映射及 idle 布局取得真实尺寸，不按 CTk 初始 200px 猜位置。
        tk.Wm.deiconify(dialog)
        dialog.update_idletasks()
        if dialog.closing or not dialog.winfo_exists() or not parent.winfo_exists():
            return False  # idle 可触发关闭，不能再查询死窗口或取得 grab。
        try:
            parent_rect, work, fallback = _parent_work_area(parent)
            hwnd, rect = _native_window_rect(dialog)
            width, height = rect.right - rect.left, rect.bottom - rect.top
            # 超高弹窗不强塞父窗；最小化或与所有工作区无交集也用所选屏幕工作区中心。
            base = work if fallback or height > parent_rect.bottom - parent_rect.top else parent_rect
            x = base.left + (base.right - base.left - width) // 2
            y = base.top + (base.bottom - base.top - height) // 2
            x = max(work.left, min(x, work.right - width))
            y = max(work.top, min(y, work.bottom - height))
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            # 绝对物理坐标支持左/上副屏负值；NOSIZE|NOZORDER|NOACTIVATE 不改尺寸或抢激活。
            if not user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015):
                raise ctypes.WinError(ctypes.get_last_error())
        except (AttributeError, OSError) as error:
            # 原生定位失败只降级到 Tk 屏幕居中，不能阻断编辑或危险操作的取消流程。
            print(f"警告：弹窗原生定位失败，使用屏幕居中（{error}）。")
            x = max((dialog.winfo_screenwidth() - dialog.winfo_width()) // 2, 0)
            y = max((dialog.winfo_screenheight() - dialog.winfo_height()) // 2, 0)
            dialog.wm_geometry(f"+{x}+{y}")
        dialog.attributes("-alpha", 1.0)  # 定位后才显现；不绑定后续移动/缩放，尊重用户拖动。
        return True
    except tk.TclError:
        return False  # 父窗退出可连带销毁子窗，调用方保持原有取消返回值。


def _apply_app_icon(window: tk.Tk | tk.Toplevel) -> None:
    """为窗口设置应用图标。"""
    try:
        # 路径统一由 config.RESOURCE_DIR 提供：它带 sys._MEIPASS 兜底，
        # 打包成 exe 后资源被解压到临时目录，也能正确定位到应用图标。
        icon_path = RESOURCE_DIR / "image" / "app_icon.ico"
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        # 图标只是装饰，Linux/macOS 下 iconbitmap 也可能不可用，失败时静默忽略。
        pass


def _edit_dialog_max_height(dialog: ctk.CTkToplevel) -> int:
    """构建期按主屏工作区给初始逻辑高度上限；会话布局另按当前父窗屏幕重新钳制。"""
    try:
        work_area = wintypes.RECT()
        # SPI_GETWORKAREA 排除任务栏；Windows API 返回物理像素，需换成 CTk 逻辑尺寸。
        if not ctypes.windll.user32.SystemParametersInfoW(
            0x0030, 0, ctypes.byref(work_area), 0
        ):
            return 700
        scaling = ctk.ScalingTracker.get_widget_scaling(dialog)
        work_height = work_area.bottom - work_area.top
        if scaling <= 0 or work_height <= 0:
            return 700
        return max(360, int(work_height / scaling) - 100)
    except Exception:
        # 非 Windows 环境或工作区 API 不可用时使用有界回退，避免创建弹窗失败。
        return 700


def ask_month(
    parent: ctk.CTk,
    title: str,
    initial_month: str | None = None,
    anchor: tk.Misc | None = None,
) -> str | None:
    """选择月份（严格返回 YYYY-MM），用户取消时返回 None。

    本函数只是 calendar_picker.ask_month 的一层薄封装（issues #3.2）：既统一了
    「月份选择」这一件事的入口，也让上层（ui.py）不必直接依赖 calendar_picker。
    改造前这里是自绘的 CTkEntry 输入框：一个 420x240 的独立窗口，靠正则 +
    strptime 校验用户手敲的内容，与「日期选择器」是两套实现、两套观感。
    现在两者共用同一个复用弹窗，选择行为、配色、定位规则完全一致。

    **形参顺序**：parent 之后只有 title 是必需的（它带语境，调用方必须给），
    initial_month / anchor 都可省略——只写 dialogs.ask_month(self, "导出账单")
    也是合法调用，此时定位到今天所在月份、弹窗退回屏幕居中。
    prompt 形参已在 issues #3.2 改造中删除：弹窗里不再有说明文字。

    Args:
        title: 窗口标题栏文字，调用方带语境（如「导出账单」「选择统计月份」「查看图表」）。
        initial_month: 预选月份（YYYY-MM），同时决定初始展示的年份；不传、或字符串
            不合法时定位到今天所在月份。
        anchor: 锚点控件（触发月份选择的按钮）：弹窗贴它的左下角弹出，不传则屏幕居中。
    """
    return picker_ask_month(parent, initial_month=initial_month, anchor=anchor, title=title)


@dataclass
class _EditSession:
    """单次编辑的记录快照、结束信号和清理清单；不随缓存窗口一起跨次保留。"""
    parent: tk.Misc
    record: Account
    dialog: _EditWindow | None = None
    signal: tk.BooleanVar | None = None  # 挂在本次 parent 上；隐藏缓存窗不会使等待结束，须显式设置信号。
    result: tuple[str, Decimal, tuple[str, ...], str] | None = None  # 仅确认写结果；取消保持 None，不从控件临时值返回。
    previous_grab: tk.Misc | None = None
    open: bool = True
    ready: bool = False
    tasks: dict[str, str] = field(default_factory=dict)  # 功能键 → dialog 注册的 after/idle，同键重排只保留一份。
    bindings: list[tuple[tk.Misc, str, str, str]] = field(default_factory=list)  # 注册控件/事件/命令 ID/守卫脚本均需收回。
    child_context: dict[str, object] = field(default_factory=dict)  # 本次子调用结果；同会话 chips 重排不清它。
    layout_sample: tuple[int, int, int, int] | None = None


_edit_window: _EditWindow | None = None  # 控件树及用户拖动位置可复用，真正 Destroy 后清缓存。
_edit_session: _EditSession | None = None  # 即使已 close，也等 ask_edit_record 的 finally 退栈才释放重入守卫。


def _edit_alive(widget: tk.Misc | None) -> bool:
    try:
        return widget is not None and bool(widget.winfo_exists()) and not getattr(widget, "closing", False)
    except (tk.TclError, AttributeError):
        return False  # 构建半途或解释器退出时不得再恢复焦点、grab或复用缓存。


def _edit_current(session: _EditSession) -> bool:
    # 窗口存在不等于旧调用仍有回填权，必须同时匹配活动会话、open 和窗口寿命。
    return _edit_session is session and session.open and _edit_alive(session.dialog)


class _EditWindow(ManagedToplevel):
    """缓存表单及其窗口级回调；每次打开都由 _prefill_edit 完整覆盖输入状态。"""
    date_var: tk.StringVar
    amount_type_var: tk.StringVar
    amount_var: tk.StringVar
    note_var: tk.StringVar
    error_var: tk.StringVar
    content: ctk.CTkScrollableFrame
    tag_chips: _EditChips
    entries: list[ctk.CTkEntry]
    buttons: ctk.CTkFrame
    pick_date: Callable[[], None]
    cancel: Callable[[], None]
    confirm: Callable[[], None]

    def __init__(self, parent: tk.Misc, session: _EditSession) -> None:
        self._edit_built = False
        self.centered = False
        session.dialog = self  # 先登记半成品；CTk构造中的update也必须能收到本次取消。
        super().__init__(parent)
        self.withdraw()
        _build_edit_controls(self)
        self._edit_built = True
        try:
            self._windows_set_titlebar_color(self._get_appearance_mode())
            self.withdraw()  # 标题栏设置可能映射窗口，完整表单预填前仍须隐藏。
        finally:
            self.finish_setup()  # 保留#63：构建期关闭先记意图，控件齐备后才真实销毁。

    def _windows_set_titlebar_color(self, color_mode: str) -> None:
        if self._edit_built and not self.closing:
            super()._windows_set_titlebar_color(color_mode)  # 参数名沿用父类以兼容关键字调用；延后同步flush，避免半张表单先参与布局。

    def after(self, ms, func=None, *args):
        if callable(func) and getattr(func, "__name__", "") in ("focus", "focus_set"):
            session = _edit_session
            if session is None or session.dialog is not self or not session.ready:
                return None  # 隐藏标题栏更新不应向旧字段安排焦点恢复。
            def restore_focus() -> None:
                func(*args)  # 调度器只负责执行，丢弃Tk回调返回值以保持会话任务的None契约。
            return _edit_schedule(session, "focus", restore_focus, ms)
        return super().after(ms, func, *args)  # type: ignore[reportArgumentType] # Tk支持可选回调，转发父类运行时签名。

    def destroy(self) -> None:
        session = _edit_session
        if session is not None and session.dialog is self:
            _end_edit_session(session)  # 即使#63延后真实销毁，也先唤醒独立的会话等待。
        super().destroy()

    def _on_destroy(self, event: tk.Event) -> None:
        global _edit_window
        if event.widget is self:
            if _edit_window is self:
                _edit_window = None  # 原生Destroy/创建父窗退出后，下次必须建新缓存。
            session = _edit_session
            if session is not None and session.dialog is self:
                _end_edit_session(session)
        super()._on_destroy(event)


def _edit_bind(session: _EditSession, widget: tk.Misc, sequence: str, callback: Callable[[tk.Event], None]) -> None:
    # Python 校验会话身份，Tcl 校验命令存活；两层防护分别挡住串会话和解绑后仍在分发的旧脚本。
    def dispatch(event: tk.Event) -> None:
        if _edit_current(session):
            callback(event)  # 复用时已排队的旧事件不能改写新记录。
    funcid = tk.Misc.bind(widget, sequence, dispatch, add="+")
    if funcid:
        script = tk.Misc.bind(widget, sequence)
        own = next(line for line in script.splitlines() if line.startswith('if {"[' + funcid + ' '))
        guarded = "if {[llength [info commands " + funcid + "]]} { " + own + " }"
        widget.tk.call("bind", str(widget), sequence, script.replace(own, guarded))
        session.bindings.append((widget, sequence, funcid, guarded))  # Tcl也检查命令存活，防嵌套事件在解绑后调用死命令。


def _edit_schedule(session: _EditSession, key: str, callback: Callable[[], None], ms: int | None = None) -> str | None:
    """合并本次会话的同键任务；ms=None 表示 idle，返回句柄只用于当前会话清理。"""
    if not _edit_current(session):
        return None
    dialog = session.dialog
    assert dialog is not None
    old = session.tasks.pop(key, None)
    if old is not None:
        dialog.after_cancel(old)
    def run() -> None:
        session.tasks.pop(key, None)
        if _edit_current(session):
            callback()  # 缓存可跨会话，任务只能归当前会话，结束后不得迟到操作下一条。
    task = dialog.after_idle(run) if ms is None else dialog.after(ms, run)
    if task is not None:
        session.tasks[key] = task
    return task


def _close_edit_children(dialog: _EditWindow) -> None:
    """先结束属于此编辑窗的内层等待，保留已建好的共享选择器缓存。"""
    calendar = calendar_picker._ses
    if calendar is not None and calendar.open and calendar.parent is dialog:
        calendar_picker._close()  # 只结束本编辑窗的子会话，不清主窗共享的日历缓存。
    tags = tag_picker._active_picker
    if tags is not None and tags.parent is dialog:
        tags.close()  # 必须唤醒内层wait_variable，外层才能退出并开放下一次编辑。
    for child in tuple(getattr(dialog, "_building_children", ())):
        child.destroy()  # 子窗构造尚未登记会话时也保留关闭意图，由其finish_setup兑现。


def _end_edit_session(session: _EditSession) -> None:
    """幂等关闭当前编辑，撤任务/绑定、隐藏、按条件归还 grab，最后通知调用方。"""
    if not session.open:
        return
    session.open = False  # 先封住迟到回填；重入守卫到公共入口finally才释放。
    dialog = session.dialog
    if dialog is not None:
        _close_edit_children(dialog)
        for task in session.tasks.values():
            try:
                dialog.after_cancel(task)
            except tk.TclError:
                pass  # 原生销毁可能已经取消任务，仍要继续唤醒等待。
        session.tasks.clear()
    for widget, sequence, funcid, own in session.bindings:
        try:
            script = tk.Misc.bind(widget, sequence)
            widget.tk.call("bind", str(widget), sequence, "\n".join(line for line in script.splitlines() if line != own))
            widget.deletecommand(funcid)  # 精确删除自己的命令，保留父窗和CTk已有绑定。
        except tk.TclError:
            pass  # 父窗销毁后绑定已不存在，不能因此阻塞结束。
    session.bindings.clear()
    if dialog is not None and _edit_alive(dialog):
        if dialog.grab_current() is dialog:
            dialog.grab_release()  # 只释放自己的grab，不能抢走其它新模态窗的grab。
        dialog.withdraw()
    if session.previous_grab is not None and _edit_alive(session.previous_grab):
        try:
            if session.previous_grab.winfo_viewable() and session.previous_grab.grab_current() is None:
                session.previous_grab.grab_set()  # 原持有者仍合法且没人新占用时才归还。
        except tk.TclError:
            pass  # 原持有者退出或不可见时不强行归还。
    if session.signal is not None:
        try:
            session.signal.set(True)  # 结果与唤醒信号独立，旧调用只返回自己的结果。
        except tk.TclError:
            pass  # 整个Tk解释器关闭时不再有可唤醒的等待。


def _prefill_edit(dialog: _EditWindow, record: Account) -> None:
    # 复用必须连空字段、错误、光标和滚动一起复位，不能只覆盖上次与本次不同的文本。
    dialog.date_var.set(record.record_date)  # 日期从本条记录覆盖，取消后的旧输入也不能留存。
    valid = record.amount.is_finite() and record.amount != 0
    dialog.amount_type_var.set("支出" if not valid or record.amount < 0 else "收入")  # 方向独立复位，不借主窗或上次编辑值。
    dialog.amount_var.set(f"{abs(record.amount):.2f}" if valid else str(record.amount))  # 非有限历史值保留修正入口，不比较NaN。
    dialog.tag_chips.set_tags(record.tags)  # 同时清手输残留和旧chips，再按本条记录顺序重建。
    dialog.note_var.set(record.note)  # 空备注也必须覆盖，不能用“非空才填”造成串记录。
    dialog.error_var.set("")  # 校验提示属于上次输入，新的完整会话没有旧错误。
    for entry in dialog.entries:
        entry._entry.selection_clear()
        entry._entry.icursor(0)  # 复用输入框还保留选择/光标，须与首次打开一致。
        entry._entry.xview_moveto(0)
    dialog.tag_chips.chips_view._parent_canvas.yview_moveto(0)  # 短列表也归顶；布局结束后再归顶一次防旧滚动区域影响。
    dialog.content._parent_canvas.yview_moveto(0)  # 大缩放的整表视口也从日期开始，不沿用旧滚动位置。


class _EditChips(TagChipsFrame):
    """把通用 chips 的重排任务纳入编辑会话，子选择结果也仅交还仍有效的原会话。"""
    def _schedule_relayout(self) -> None:
        self.chips_view.grid()
        self.chips_frame.grid()
        session = _edit_session
        if session is not None:
            _edit_schedule(session, "chips", self._relayout_tags)  # 合并批量chips任务，关闭即取消，旧便条不跨会话。

    def _pick_tags(self) -> None:
        session = _edit_session
        if session is None or not _edit_current(session):
            return
        dialog = session.dialog
        assert dialog is not None
        try:
            picked = tag_picker.ask_tags(dialog, self.tag_entry, self.tag_entry.get(),
                                         toggle_button=self.tag_button, selected_tags=self.get_tags())
        finally:
            if _edit_current(session) and dialog.grab_current() is None:
                dialog.grab_set()  # 标签子会话归还后再确认编辑会话有效，不抢别人的新grab。
        if picked is not None and _edit_current(session):
            session.child_context["tags"] = picked
            final = tuple(dict.fromkeys((*picked, *split_tag_input(self.tag_entry.get()))))
            self.set_tags(final)  # 只有原编辑会话仍有效才回填，关闭后的子结果必须丢弃。


def _resize_edit(session: _EditSession) -> None:
    # 请求高度/窗口边框是物理像素，geometry 接收逻辑尺寸；只在此换算一次，避免重复缩放。
    dialog = session.dialog
    assert dialog is not None
    scale = ctk.ScalingTracker.get_window_scaling(dialog)
    padding = 40 * ctk.ScalingTracker.get_widget_scaling(dialog.content)
    height = int((dialog.content.winfo_reqheight() + padding) / scale + .5)
    _parent, work, _fallback = _parent_work_area(session.parent)
    _hwnd, outer = _native_window_rect(dialog)
    decoration = outer.bottom - outer.top - dialog.winfo_height()
    maximum = max(360, int((work.bottom - work.top - decoration) / scale))
    height = max(360, min(height, maximum))  # 内容高度每次重算并按本父窗所在屏幕钳制，不沿用旧chips高度。
    if dialog.content.winfo_reqheight() + padding > height * scale:
        dialog.content._scrollbar.grid()  # 工作区容不下整表时允许滚动，不能把备注/确定按钮永久裁掉。
    else:
        dialog.content._scrollbar.grid_remove()  # 正常高度不占滚动条宽度，保持#24金额行和原表单布局。
    dialog.maxsize(460, maximum)
    if abs(dialog.winfo_height() - round(height * scale)) > 1 or abs(dialog.winfo_width() - round(460 * scale)) > 1:
        dialog.geometry(f"460x{height}")  # 已有显式WM尺寸不能靠pack自然撑开；只改高，不重新居中。


def _clamp_edit(dialog: _EditWindow, parent: tk.Misc) -> None:
    _parent, work, _fallback = _parent_work_area(parent)
    hwnd, rect = _native_window_rect(dialog)
    x = min(max(rect.left, work.left), max(work.left, work.right - (rect.right - rect.left)))
    y = min(max(rect.top, work.top), max(work.top, work.bottom - (rect.bottom - rect.top)))
    if (x, y) != (rect.left, rect.top):
        ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), None, x, y, 0, 0, 0x0015)  # 增高越界只作最小位移，保留用户拖动位置。


def _layout_edit(session: _EditSession) -> None:
    if not _edit_current(session):
        return
    dialog = session.dialog
    assert dialog is not None
    _resize_edit(session)
    sample = (dialog.winfo_width(), dialog.winfo_height(), dialog.content.winfo_reqheight(), dialog.tag_chips._layout_rows)
    if session.layout_sample != sample or not all(entry.winfo_ismapped() for entry in dialog.entries):
        session.layout_sample = sample
        _edit_schedule(session, "layout", lambda: _layout_edit(session))
        return  # 两次idle几何一致后才露出，alpha=0覆盖旧尺寸和新尺寸之间的整段变化。
    if session.ready:
        _clamp_edit(dialog, session.parent)
        return  # 同会话chips变化也等请求尺寸传播完，不重置焦点/滚动，更不重新居中。
    if not dialog.centered:
        if not center_dialog_on_parent(session.parent, dialog) or not _edit_current(session):
            _end_edit_session(session)
            return
        dialog.centered = True  # 首次完整布局才居中；之后复开和chips变化都保留原位置。
    else:
        _clamp_edit(dialog, session.parent)
        dialog.attributes("-alpha", 1.0)
    dialog.tag_chips.chips_view._parent_canvas.yview_moveto(0)
    dialog.content._parent_canvas.yview_moveto(0)
    dialog.entries[0].focus_set()  # 每次会话回到日期；不会继承上次备注或子窗焦点。
    dialog.grab_set()
    session.ready = True


def _chips_edit_height() -> None:
    session = _edit_session
    if session is not None and session.ready:
        session.layout_sample = None
        _edit_schedule(session, "layout", lambda: _layout_edit(session))  # 同会话增删合并重算，等待整表请求高度传播，不清子窗上下文。


def ask_edit_record(parent: ctk.CTk, record: Account) -> tuple[str, Decimal, tuple[str, ...], str] | None:
    """返回规范化日期、带符号金额、完整标签、备注；取消、父窗失效或拒绝重入返回 None。"""
    global _edit_session, _edit_window
    if _edit_session is not None or not _edit_alive(parent):
        return None  # 模态编辑拒绝重入；不接管、不弹第二层提示、不丢未保存输入。
    session = _EditSession(parent, replace(record))  # 浅复制完整字段；tags 为不可变 tuple，无须从表格摘要重建。
    _edit_session = session  # 必须早于变量创建、建窗及所有Tk update登记守卫。
    try:
        session.signal = tk.BooleanVar(master=parent)
        held = parent.grab_current()
        if held is not None and _edit_alive(held) and held.winfo_viewable() and held.winfo_toplevel() is parent.winfo_toplevel():
            session.previous_grab = held  # 只借当前父窗控件树的合法grab。
        if _edit_window is None or not _edit_alive(_edit_window) or not _edit_alive(_edit_window.master):
            if _edit_window is not None:
                _edit_window.destroy()
            _edit_window = _EditWindow(parent, session)
        dialog = _edit_window
        assert dialog is not None
        session.dialog = dialog
        if not _edit_current(session):
            return None
        _edit_bind(session, parent, "<Destroy>", lambda event: _end_edit_session(session) if event.widget is parent else None)
        dialog.attributes("-alpha", 0.0)
        dialog.withdraw()
        if str(dialog.transient()) != str(parent):
            # transient 可随本次调用更新，创建 master 仍属原父窗；缓存寿命不能脱离创建父窗。
            dialog.transient(parent)
        _close_edit_children(dialog)
        session.child_context.clear()  # 只在编辑会话开始清一次；同会话反复点▼不清上下文，也不销毁共享选择器缓存。
        _prefill_edit(dialog, session.record)
        dialog.deiconify()
        _edit_schedule(session, "layout", lambda: _layout_edit(session))
        signal = session.signal
        assert signal is not None
        if not signal.get():
            parent.wait_variable(signal)  # 缓存窗口不销毁，等待的是本次独立结束信号。
        return session.result
    except BaseException:
        # 构建失败走永久销毁并重抛原异常，不能留一个已登记但永远不完成 setup 的半张表单。
        if session.dialog is not None and _edit_alive(session.dialog):
            session.dialog.finish_setup()
            session.dialog.destroy()  # 构建异常也释放#63半成品登记，不能永久占住父窗与缓存。
        raise
    finally:
        _end_edit_session(session)
        _edit_session = None  # 子窗wait和公共调用完全退栈后，才接受下一条记录。


def _build_edit_controls(dialog: _EditWindow) -> None:
    # 仅首次建控件；闭包可捕获缓存字段，但确认/取消必须在执行时现读 _edit_session。
    dialog.attributes("-alpha", 0.0)  # 隐藏初始布局和定位过程，避免先在系统默认位置闪现。
    dialog.title("编辑记录")
    dialog.minsize(460, 360)
    dialog.maxsize(460, _edit_dialog_max_height(dialog))
    # 直接设 Tk 的可调整状态，避免 CTk 覆写额外安排异步标题栏重绘回调。
    tk.Wm.resizable(dialog, False, True)
    dialog.configure(fg_color="#F0F4F8")
    _apply_app_icon(dialog)

    dialog_font = ("Microsoft YaHei UI", 11)
    title_font = ("Microsoft YaHei UI", 13, "bold")
    # 变量只属缓存控件；每次会话覆盖其值，既不抓旧record，也不借主窗下一笔录入状态。
    date_var = dialog.date_var = tk.StringVar(master=dialog)
    amount_type_var = dialog.amount_type_var = tk.StringVar(master=dialog, value="支出")
    amount_var = dialog.amount_var = tk.StringVar(master=dialog)
    note_var = dialog.note_var = tk.StringVar(master=dialog)

    content = ctk.CTkScrollableFrame(dialog, fg_color="transparent", corner_radius=0, height=1)
    content._scrollbar.configure(height=0)
    content._scrollbar.grid_remove()  # 仅高度受工作区限制时启用整表滚动，通常场景仍是完整静态表单。
    content.pack(fill="both", expand=True, padx=24, pady=20)
    ctk.CTkLabel(
        content,
        text="编辑记录",
        font=title_font,
        text_color="#243447",
    ).pack(anchor="w")

    dialog.content = content
    tag_chips = dialog.tag_chips = _EditChips(content, on_layout_change=_chips_edit_height)  # type: ignore[reportArgumentType] # TagChipsFrame把master限为CTkBaseClass，但可滚动Frame是合法Tk容器，运行时支持此父窗。

    fields: tuple[tuple[str, tk.StringVar | TagChipsFrame], ...] = (
        ("日期", date_var),
        ("金额", amount_var),
        ("标签", tag_chips),
        ("备注", note_var),
    )

    def _pick_date() -> None:
        """打开日历选择器，把选中的日期回填到日期输入框（需求 3.12）。"""
        session = _edit_session
        if session is None or not _edit_current(session):
            return
        try:
            picked = ask_date(dialog, date_var.get().strip(), anchor=entries[0])  # 调用时取日期框锚点，保留当前输入和贴靠位置。
        finally:
            if _edit_current(session) and dialog.grab_current() is None:
                dialog.grab_set()  # 日历退出释放grab后，归还给仍有效的本次编辑会话。
        # 返回 None 表示用户取消/按 ESC，此时保持输入框原值不变。
        if picked and _edit_current(session):
            session.child_context["date"] = picked  # 保存的是本次子调用结果，旧会话返回不许覆盖新日期。
            date_var.set(picked)

    # 按顺序收集输入框，用于最后把焦点落到第一个字段上。
    # 只有日期、金额、备注是 CTkEntry；标签 chips 不进列表，首项仍是日期框。
    entries: list[ctk.CTkEntry] = []
    dialog.entries = entries
    dialog.pick_date = _pick_date
    for label, value in fields:
        ctk.CTkLabel(
            content,
            text=label,
            font=dialog_font,
            text_color="#455A64",
        ).pack(anchor="w", pady=(10, 3))

        # chips 不是 StringVar，也不是单行输入框；按具体控件类型分流后直接占满字段行。
        if isinstance(value, TagChipsFrame):
            value.pack(fill="x")
            continue

        # 日期行采用「输入框 + ▼」组合：两者放进同一个横向容器，
        # 这样 ▼ 始终贴在输入框右侧，且行高与其它字段完全一致（分开 pack 会多占一行）。
        if label == "日期":
            date_row = ctk.CTkFrame(content, fg_color="transparent")
            date_row.pack(fill="x")
            entry = ctk.CTkEntry(
                date_row,
                textvariable=value,
                font=dialog_font,
                height=34,
                corner_radius=9,
                border_width=1,
                border_color="#C6D4DF",
                fg_color="#FFFFFF",
            )
            # 与 InputFrame 一致：按钮文字用「▼」（Microsoft YaHei UI 自带字形，
            # 不靠字体回退）而不是彩色 emoji 📅。📅 在该字体里没有字形，实际是靠
            # Windows 的字体回退显示出来的（主流环境实测正常，但多绕了一层，
            # 跨环境一致性差一些），而且它的彩色在灰蓝线框按钮里也显得跳。
            # 按钮先 pack 且 side="right" 钉在右端，宽度 36；
            # 输入框再 pack 且 expand=True 占满剩余空间，高度 34 与按钮齐平。
            ctk.CTkButton(
                date_row,
                text="▼",
                command=_pick_date,
                width=36,
                height=34,
                corner_radius=9,
                fg_color="#E3EAF2",
                hover_color="#D2DEE9",
                text_color="#243447",
                font=("Microsoft YaHei UI", 11),
            ).pack(side="right", padx=(6, 0))
            entry.pack(side="left", fill="x", expand=True)
            entries.append(entry)
            continue

        if label == "金额":
            # 标签仍在上方；412px 行宽扣除120px按钮和12px间距，金额外框获得280px。
            amount_row = ctk.CTkFrame(content, fg_color="transparent", corner_radius=0)
            amount_row.pack(fill="x")
            amount_row.columnconfigure(0, weight=1)
            # 40px外框包住38px输入框，与主窗尺寸一致，不增加一行挤压标签/备注。
            amount_surface = ctk.CTkFrame(
                amount_row, height=40, corner_radius=8, border_width=1,
                border_color="#D8E1EA", fg_color="#F8FAFC",
            )
            amount_surface.grid(row=0, column=0, sticky="ew", padx=(0, 12))
            amount_surface.columnconfigure(0, weight=1)
            entry = ctk.CTkEntry(
                amount_surface, textvariable=value, width=1, height=38,
                corner_radius=0, border_width=0, fg_color="#F8FAFC",
                text_color="#243447", font=dialog_font,
            )
            entry.grid(row=0, column=0, sticky="ew", padx=(9, 6), pady=1)
            entries.append(entry)
            # 切换只表达本窗意图，不改金额文本；符号统一在确认时转换。
            # 中间圆角填色须与本行实际背景一致，照搬主窗白色会在灰底编辑窗露出白缝。
            ctk.CTkSegmentedButton(
                amount_row, values=["支出", "收入"], variable=amount_type_var,
                width=120, height=40, dynamic_resizing=False, corner_radius=8,
                border_width=0, fg_color=amount_row.cget("bg_color"), font=dialog_font,
                selected_color="#CFE0F8", selected_hover_color="#BFD6F5",
                unselected_color="#F0F3F7", unselected_hover_color="#E2E8F0",
                text_color="#243447",
            ).grid(row=0, column=1)
            continue

        # 这里的输入框不需要 placeholder_text，因此可以放心使用 textvariable 双向绑定。
        entry = ctk.CTkEntry(
            content,
            textvariable=value,
            font=dialog_font,
            height=34,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#FFFFFF",
        )
        entry.pack(fill="x")
        entries.append(entry)

    error_var = dialog.error_var = tk.StringVar(master=dialog)
    ctk.CTkLabel(
        content,
        textvariable=error_var,
        text_color="#C62828",
        font=("Microsoft YaHei UI", 10),
    ).pack(anchor="w", pady=(5, 0))

    buttons = ctk.CTkFrame(content, fg_color="transparent")
    dialog.buttons = buttons
    buttons.pack(fill="x", pady=(10, 0))
    # 两列等宽，保证两个按钮左右对称。
    buttons.grid_columnconfigure((0, 1), weight=1)

    def close_dialog() -> None:
        session = _edit_session
        if session is not None and session.dialog is dialog:
            _end_edit_session(session)  # 正常结束只隐藏，真正销毁才交给ManagedToplevel。

    def cancel() -> None:
        if dialog.closing:
            return  # 构建期间已收到关闭请求，不再重复确认或取消。
        # 返回 None 表示取消，调用方据此不做任何更新。
        close_dialog()

    def confirm() -> None:
        session = _edit_session
        if session is None or session.dialog is not dialog or not _edit_current(session):
            return  # 取消先到时，后续回车不能再改写返回结果。
        try:
            # 日期格式与金额合法性同时校验，任一失败都走统一的错误提示。
            parsed_date = datetime.strptime(
                date_var.get().strip(), "%Y-%m-%d"
            ).date()
            # 与添加区同链：手输负号也只表示金额大小，最终方向由本窗按钮决定。
            parsed_amount = abs(Decimal(amount_var.get().strip()))
            # NaN/Infinity 可被 Decimal 解析，但 NaN 比较可能抛异常、Infinity 会污染汇总；
            # 与新增入口同样拒收非有限数，历史脏值也须先改成有限金额才允许确认。
            if not parsed_amount.is_finite():
                raise ValueError
        except (ValueError, InvalidOperation):
            error_var.set("日期格式应为 YYYY-MM-DD，金额必须是数字。")
            return
        # 仅在提交逻辑赋符号，保持调用方和数据层只接收统一的带符号 Decimal。
        if amount_type_var.get() == "支出":
            parsed_amount = -parsed_amount
        # §3.14.4 已确认 0 标签合法，collect_tags 可返回空元组；金额非零是独立约束。
        if parsed_amount == 0:
            error_var.set("金额不能为0，收支由按钮决定")
            return
        parsed_tags = tag_chips.collect_tags()
        # 返回结构不变，原调用方直接 update；按钮切换本身不写库。
        session.result = (
            parsed_date.isoformat(),
            parsed_amount,
            parsed_tags,
            note_var.get().strip(),
        )
        close_dialog()

    ctk.CTkButton(
        buttons,
        text="取消",
        command=cancel,
        width=120,
        height=34,
        corner_radius=9,
        fg_color="#90A4AE",
        hover_color="#78909C",
        font=dialog_font,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ctk.CTkButton(
        buttons,
        text="确定",
        command=confirm,
        width=120,
        height=34,
        corner_radius=9,
        fg_color="#2F80ED",
        hover_color="#256AC4",
        font=dialog_font,
    ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
    # 关闭按钮等同取消；回车确定、Esc 取消，与月份对话框保持一致的操作习惯。
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.bind("<Return>", lambda _event: confirm())
    dialog.bind("<Escape>", lambda _event: cancel())
    dialog.cancel = cancel
    dialog.confirm = confirm


def confirm_undo_import(parent: tk.Misc, batch) -> bool:
    """批次撤销危险确认；只返回决定，不连接数据库。"""
    dialog = ManagedToplevel(parent, fg_color="#F0F4F8")
    dialog.title("确认撤销导入")
    dialog.geometry("520x245")
    dialog.attributes("-alpha", 0)
    dialog.transient(parent)
    _apply_app_icon(dialog)
    result = [False]
    # 导入笔数是原始快照，不承诺被单笔删除过的记录仍全部存在。
    ctk.CTkLabel(dialog, text=f"撤销 {batch.file_name} 的导入？", wraplength=465,
                 font=("Microsoft YaHei UI", 14, "bold"), text_color="#243447").pack(padx=22, pady=(20, 8))
    ctk.CTkLabel(dialog, text=f"该批次原导入 {batch.imported_count} 笔。\n将删除其全部现存记录，包括已编辑或添加标签的记录。\n手动记录、其他批次和标签配置不受影响。",
                 wraplength=465, justify="left", font=("Microsoft YaHei UI", 12),
                 text_color="#475569").pack(padx=22, pady=8)
    actions = ctk.CTkFrame(dialog, fg_color="transparent")
    actions.pack(pady=12)

    def finish(confirmed):
        if not dialog.closing:
            result[0] = confirmed
            dialog.destroy()

    # 红色操作需明确点击；回车不默认执行整批删除，关闭/Esc均为取消。
    ctk.CTkButton(actions, text="取消", command=lambda: finish(False), width=140,
                 fg_color="#E8EEF5", hover_color="#DDE7F2", text_color="#475569",
                 corner_radius=8, font=("Microsoft YaHei UI", 12)).pack(side="left", padx=8)
    ctk.CTkButton(actions, text="确认撤销", command=lambda: finish(True), width=140,
                 fg_color="#B84035", hover_color="#A3342B", corner_radius=8,
                 font=("Microsoft YaHei UI", 12)).pack(side="left", padx=8)
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    dialog.bind("<Escape>", lambda _event: finish(False))
    dialog.finish_setup()
    if dialog.closing or not center_dialog_on_parent(parent, dialog):
        return False
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0]


def confirm_delete(parent: ctk.CTk) -> bool:
    """返回是否确认删除；本窗只收决定，调用方据此执行删除，关闭即销毁而不缓存。"""
    # 不用 messagebox.askyesno，是因为系统弹窗无法定制文字与配色，
    # 也无法明确哪个按钮是"危险"操作。
    dialog = ManagedToplevel(parent)
    dialog.attributes("-alpha", 0.0)  # 与编辑框共用透明布局、一次定位后显现的流程。
    dialog.title("确认删除")
    dialog.geometry("360x180")
    tk.Wm.resizable(dialog, False, False)  # 避免 CTk 额外标题栏重绘在定位后再次进入 update。
    dialog.transient(parent)
    dialog.configure(fg_color="#F0F4F8")
    _apply_app_icon(dialog)
    # 默认返回 False（未确认），只有点「确认」才会置为 True。
    result = [False]

    content = ctk.CTkFrame(dialog, fg_color="transparent")
    content.pack(fill="both", expand=True, padx=24, pady=20)
    # 文案逐字对齐需求 §3.5 的问句，不自拟「确定要删除这条记录吗？」这种近似说法，
    # 避免文档与界面两处措辞各自漂移（#27）。
    ctk.CTkLabel(
        content,
        text="确认删除该记录吗？",
        font=("Microsoft YaHei UI", 12),
        text_color="#243447",
    ).pack(anchor="w", pady=(8, 18))

    buttons = ctk.CTkFrame(content, fg_color="transparent")
    buttons.pack(fill="x")
    # 两列等宽，让「取消」与「确认」按钮尺寸完全对称。
    buttons.grid_columnconfigure((0, 1), weight=1)

    def cancel() -> None:
        if dialog.closing:
            return
        # 保持 result[0] 为 False，调用方据此放弃删除。
        dialog.destroy()

    def confirm() -> None:
        if dialog.closing:
            return
        result[0] = True
        dialog.destroy()

    ctk.CTkButton(
        buttons,
        text="取消",
        command=cancel,
        width=120,
        height=34,
        corner_radius=9,
        fg_color="#90A4AE",
        hover_color="#78909C",
        font=("Microsoft YaHei UI", 11),
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ctk.CTkButton(
        buttons,
        text="确认",
        command=confirm,
        width=120,
        height=34,
        corner_radius=9,
        fg_color="#E76F51",
        hover_color="#C9573D",
        font=("Microsoft YaHei UI", 11),
    ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
    # 关闭按钮视为取消；回车/确认键确认、Esc 取消，与其它对话框保持一致。
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.bind("<Return>", lambda _event: confirm())
    dialog.bind("<Escape>", lambda _event: cancel())
    dialog.finish_setup()
    if dialog.closing or not center_dialog_on_parent(parent, dialog):
        return False  # 关闭请求优先，保持删除确认的取消语义。
    # grab_set 阻止用户在删除确认期间操作主窗口；
    # wait_window 暂停本次调用但仍处理 Tk 事件，故按钮须检查 closing，不能假设等待期间无重入。
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0]
