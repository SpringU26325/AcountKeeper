"""标签多选弹窗（需求 3.13）：一个缓存窗口，每次调用有独立选择与结束信号。

ask_tags 同步等待 wait_variable，但不持 grab；应用内外点可关闭并继续作用于所点控件。
同锚点再次调用是 toggle 取消，不同锚点先结束旧会话再接管；旧栈只返回自己的 result。
_win 缓存控件/候选行，_active_picker 持本次 parent、anchor、选择、任务及绑定；不能串用。
创建 master 不变，transient 可换本次父窗；创建父窗退出会毁窗，本次父窗退出须结束会话。
普通结束取消本次任务、精确解绑、隐藏；真正销毁交给 ManagedToplevel，并清窗口缓存。
「完成」返回完整选择元组，取消返回 None；自定义已选标签可不在常用候选列表中。
保存/删除/置顶通过 tag_prefs 立即改常用列表，取消本次选择不会回滚这些偏好，也不改历史账本。
footer 是本窗独立输入：current 仅预填保存框，selected_tags 才预填已选集合，二者不能混为一谈。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import tkinter as tk
from typing import Callable

import customtkinter as ctk

# 候选与增删都交给 tag_prefs（标签列表就是唯一真源，那份规则只有一份实现）。
from tag_prefs import build_tag_candidates, delete_tag, move_tag_to_top, save_tag
# config 是叶子模块（不导入任何项目模块），引用它的 RESOURCE_DIR 不会形成循环依赖。
from config import RESOURCE_DIR
from dialog_lifecycle import ManagedToplevel
from popup_common import anchored_position

# 与 calendar_picker.py / dialogs.py 的弹窗保持同一套浅色主题配色。
_BG_COLOR = "#F0F4F8"
_TEXT_COLOR = "#455A64"
_ACCENT_COLOR = "#2F80ED"
_ACCENT_HOVER_COLOR = "#256AC4"
_SUBTLE_HOVER_COLOR = "#D2DEE9"
_SCROLLBAR_COLOR = "#C6D4DF"
_SCROLLBAR_HOVER_COLOR = "#90A4AE"

# 单个标签项的高度，以及列表最多露出几项（超出的部分靠滚动条看）。
_ITEM_HEIGHT = 32
_MAX_VISIBLE_ITEMS = 7

# ---------- 底部操作区（footer） ----------
# 结构：第一行 = 新标签输入框 + 「+ 保存」按钮；第二行 = 一行提示语。
# 高度拆成常量是为了让「弹窗总高度」能一条式子算出来：
#   50 = 28（输入框/按钮行）+ 4（行距）+ 18（一行提示语）
_FOOTER_GAP = 6  # 列表与 footer 之间的竖向缝隙
_BUTTON_ROW_HEIGHT = 28
# 提示语那一行的高度。**必须**在建 hint_label 时原样传给它：CTkLabel 的 height 默认
# 是 28（与字号无关），不传就等于「预算 18、实花 28」，footer 会凭空多要 10px，
# 那 10px 只能从别处扣，扣到的就是这条提示语自己。
_HINT_ROW_HEIGHT = 18
_FOOTER_HEIGHT = _BUTTON_ROW_HEIGHT + 4 + _HINT_ROW_HEIGHT

# 提示语只留一句，说明「footer 这个输入框怎么用」。必须点明「要在这里输入」：
# 弹窗自带输入框之后，在主窗口那个框里打字并不会进候选，沿用旧文案
# 「可直接输入新标签名」会让人以为自己打的字已经被收下了。
# 「×」不再另做图例：它是各 App 里删掉一项的通用符号，不需要解释；而弹窗最窄时
# （锚点 180px）一行里也塞不下第二句。
_HINT_LINE_1 = "输入新标签后点 + 保存"
_HINT_COLOR = "#90A4AE"
_ERROR_COLOR = "#D64545"  # 写盘失败时把提示语染成警示色

# 动作按钮的文字。× (U+00D7) 是 Microsoft YaHei UI **自带**字形，不依赖字体回退，
# 各 App 里「删除/移除这一项」都用它（关标签页、删块）。
# 刻意不用 ✕(U+2715)：它在 YaHei UI 里没有字形，靠系统字体回退才显示得出来，
# 跨环境一致性不如上面这个原生字符。
_ACTION_REMOVE_TEXT = "×"
# 置顶按钮用汉字「顶」。备选是 ↑(U+2191)：它同样是 YaHei UI **自带**字形
# （实测 glyph 0x0251，不靠字体回退），但箭头太容易读成「上移一位」这个相邻操作，
# 而「顶」是常用汉字、YaHei UI 必然收录（实测 glyph 0x0bbd），单字宽 20 逻辑像素
# （字号 12），28px 的按钮装得下，语义也比箭头直白。
# 用两个字「置顶」则实测要 40px，按钮得加宽到 44px，会挤掉标签名的位置，不用。
_ACTION_PIN_TEXT = "顶"
_SAVE_TEXT = "+ 保存"

_SAVE_BUTTON_WIDTH = 64
_ACTION_BUTTON_WIDTH = 28

# 调用方那个 ▼ 按钮的文字：弹窗开着时翻成 ▲，关掉时翻回 ▼。
# ▲/▼ 是 U+25B2 / U+25BC，是 Microsoft YaHei UI **自带**字形（不靠字体回退），
# 所以翻转既不会缺字、也不受系统字体环境差异影响。
# 「关态」刻意与调用方创建按钮时写的初始文字保持一致——本模块拿到的是别人建好的
# 控件，只能保证「关掉之后回到 ▼」；一旦有人改了 widgets.py / dialogs.py 里建按钮处
# 的文字，这两处必须同步改，否则会出现「关掉了但图标没回原样」的错觉。
_TOGGLE_TEXT_CLOSED = "▼"
_TOGGLE_TEXT_OPEN = "▲"

# 列表区的圆角半径。它同时会**撑高**列表区：CTkScrollableFrame 真正被 pack 进父容器
# 的是它内部那层 _parent_frame，而该层在上下各留了一个圆角半径的间距，所以列表区
# 向父容器申请的高度是「视口高 + 2 × 本值」。下面两条弹窗高度公式都必须带上这个
# 增量，否则总高会比内容实际需要的矮 16px，缺的那截由 pack 转嫁给列表区自己，
# 表现成「最后一行露一半、要滚动才看得全」。
_LIST_CORNER_RADIUS = 8
_PAD = 10  # 弹窗四周内边距
_MIN_WIDTH = 180  # 输入框窄到离谱时的兜底宽度（逻辑像素）
_POLL_INTERVAL_MS = 50  # 复查锚点位置的轮询间隔（Windows 下窗口移动是异步的）


@dataclass
class _Session:
    """本次调用的独立状态；缓存窗可复用，信号、结果和任务清单不可跨次复用。"""
    dialog: _TagWindow
    parent: tk.Tk | tk.Toplevel
    anchor: tk.Misc
    toggle: ctk.CTkButton | None
    signal: tk.BooleanVar
    selected_order: list[str]  # 返回值保留选择顺序，不能用 set 的遍历顺序提交。
    selected_set: set[str]  # 与 order 同步增删，只负责成员判断和行高亮。
    candidates: list[str]
    previous_grab: tk.Misc | None = None
    result: tuple[str, ...] | None = None
    open: bool = True
    ready: bool = False
    height: int = 1
    last_target: tuple[int, int, int, int] | None = None
    tasks: dict[str, str] = field(default_factory=dict)  # 功能键 → dialog 注册的 after/idle，结束时逐个取消。
    bindings: list[tuple[tk.Misc, str, str, str]] = field(default_factory=list)  # 原控件/事件/命令 ID/守卫脚本必须成套解绑。

    def close(self) -> None:
        _end_session(self)  # 活动登记保存会话身份，旧调用的收尾不得误关新会话。


@dataclass
class _Row:
    """窗口级候选行及上次绘制状态；selected 仅是高亮缓存，本次选择以 Session 为准。"""
    frame: ctk.CTkFrame
    name_button: ctk.CTkButton
    pin_button: ctk.CTkButton
    selected: bool = False
    first: bool = False


_win: _TagWindow | None = None
_active_picker: _Session | None = None
# all标签同时被CTk使用，不能unbind_all；根窗只安装一条监视器，无会话时空转。
_click_monitor_root: tk.Misc | None = None


class _TagWindow(ManagedToplevel):
    """缓存 footer 和按名称复用的候选行；永久退出仍遵守 #63 的构建保护。"""
    def __init__(self, parent: tk.Tk | tk.Toplevel) -> None:
        self._tag_built = False
        self._hidden_titlebar = False
        super().__init__(parent)  # 仍以真实parent注册构建中的子窗，保留#63延期销毁保护。
        self.withdraw()
        self.title("选择标签")
        tk.Wm.resizable(self, False, False)
        self.configure(fg_color=_BG_COLOR)
        self.rows: dict[str, _Row] = {}
        self.order: list[str] = []
        self.list_height = _ITEM_HEIGHT
        self.geometry_target: tuple[int, int, int, int] | None = None
        self.list_frame = ctk.CTkScrollableFrame(
            self, width=0, height=_ITEM_HEIGHT, corner_radius=_LIST_CORNER_RADIUS,
            fg_color="#FFFFFF", scrollbar_button_color=_SCROLLBAR_COLOR,
            scrollbar_button_hover_color=_SCROLLBAR_HOVER_COLOR,
        )
        footer = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        # footer先占底部，列表最后分配剩余空间，避免提示语被pack压扁。
        footer.pack(side="bottom", fill="x", padx=_PAD, pady=(_FOOTER_GAP, _PAD))
        self.list_frame.pack(side="top", fill="both", expand=True, padx=_PAD, pady=(_PAD, 0))
        button_row = ctk.CTkFrame(footer, fg_color="transparent", corner_radius=0, height=_BUTTON_ROW_HEIGHT)
        button_row.pack(fill="x")
        self.save_entry = ctk.CTkEntry(
            button_row, height=_BUTTON_ROW_HEIGHT, corner_radius=6, border_width=1,
            border_color=_SCROLLBAR_COLOR, fg_color="#FFFFFF",
            font=("Microsoft YaHei UI", 11), text_color=_TEXT_COLOR,
        )
        for text, command in (("完成", _complete), (_SAVE_TEXT, _save_current)):
            ctk.CTkButton(
                button_row, text=text, command=command, width=_SAVE_BUTTON_WIDTH,
                height=_BUTTON_ROW_HEIGHT, corner_radius=6, fg_color=_ACCENT_COLOR,
                hover_color=_ACCENT_HOVER_COLOR, text_color="#FFFFFF",
                font=("Microsoft YaHei UI", 11),
            ).pack(side="right")
        self.save_entry.pack(side="left", fill="x", expand=True)
        self.save_entry.bind("<Return>", _on_return)
        self.hint_label = ctk.CTkLabel(
            footer, text=_HINT_LINE_1, height=_HINT_ROW_HEIGHT,
            font=("Microsoft YaHei UI", 10), text_color=_HINT_COLOR, justify="left", anchor="w",
        )
        self.hint_label.pack(fill="x", pady=(4, 0))
        self.empty_label: ctk.CTkLabel | None = None
        # 窗口级回调只查当前会话，缓存后不持有第一次ask_tags的闭包。
        self.protocol("WM_DELETE_WINDOW", _cancel)
        self.bind("<Escape>", _on_escape)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            _on_window_destroy(event)  # 先清缓存/通知会话，再让父类删除Tcl命令，避免第二个失效Destroy脚本。
        super()._on_destroy(event)  # 保留#63所属任务、Python控件树及缩放订阅清理。

    def _windows_set_titlebar_color(self, color_mode: str) -> None:
        if not self._tag_built or self.closing:
            return  # CTk构造中的update必须延后到缓存和会话可收尾时，避免半成品重入。
        self._hidden_titlebar = not self.winfo_ismapped()
        try:
            super()._windows_set_titlebar_color(color_mode)
        finally:
            self._hidden_titlebar = False

    def after(self, ms, func=None, *args):
        if self.closing:
            return None  # 外部Destroy已清任务后不得重新向死窗口注册CTk便条。
        if callable(func) and getattr(func, "__name__", "") in ("focus", "focus_set"):
            session = _active_picker
            if self._hidden_titlebar or session is None or session.dialog is not self:
                return None  # 隐藏标题栏更新未夺取焦点，不向旧父控件安排延迟焦点恢复。
            def restore_focus() -> None:
                target = getattr(func, "__self__", None)
                if not isinstance(target, tk.Misc) or _alive(target):
                    func(*args)
            return _schedule(session, "titlebar_focus", restore_focus, ms)  # 可见时焦点恢复也归本次会话，关闭/接管须取消。
        return super().after(ms, func, *args)  # type: ignore[reportArgumentType] # Tk运行时支持func=None，保留父类转发。

    def finish_tag_setup(self) -> None:
        if self._tag_built:
            return
        self._tag_built = True
        try:
            self._windows_set_titlebar_color(self._get_appearance_mode())
            if _alive(self):
                self.withdraw()  # 设置CTk隐藏意图，阻止after(5)把已取消或缓存中的窗重新显示。
        finally:
            self.finish_setup()  # 父窗构建期关闭请求必须在控件/绑定就绪后兑现。

    def destroy(self) -> None:
        session = _active_picker
        if session is not None and session.dialog is self:
            _end_session(session)  # 即使真正销毁被#63延期，也先唤醒内层等待让外层构建继续。
        super().destroy()


def _apply_app_icon(window: tk.Tk | tk.Toplevel) -> None:
    """为弹窗设置应用图标。

    这里没有复用 dialogs._apply_app_icon：dialogs.py 需要导入本模块调用
    ask_tags，若再反向导入会形成循环导入，所以保留一份极小的私有实现。
    路径来源则统一走 config.RESOURCE_DIR（issues #17），不再各自用 __file__ 拼。
    """
    try:
        # 路径统一由 config.RESOURCE_DIR 提供：它带 sys._MEIPASS 兜底，打包后也能定位到应用图标。
        icon_path = RESOURCE_DIR / "image" / "app_icon.ico"
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        # 图标只是装饰，缺失或平台不支持时静默忽略，绝不能因此让标签列表打不开。
        pass


def _window_scaling(widget: tk.Misc) -> float:
    """取窗口缩放系数，仅用于把逻辑尺寸预算转为物理像素。

    winfo_* 和 _reanchor 的 wm_geometry 均按物理像素使用，不能把测得的锚点宽度再乘一次。
    未注册或查询失败退回 1.0，不让可选的尺寸优化阻断选择；不等同完整的混合 DPI 支持。
    """
    try:
        scaling = float(ctk.ScalingTracker.get_window_scaling(widget))
    except Exception:
        return 1.0  # 尚未挂到有效 CTk 窗口时仍可继续打开，不把缩放查询失败当取消。
    # 缩放系数理论上不可能为 0，这里只是防止除零。
    return scaling if scaling > 0 else 1.0


def _is_inside(widget: tk.Misc, ancestor: tk.Misc) -> bool:
    """widget 是不是 ancestor 自己或它的后代（沿 master 链往上找）。"""
    # 不能直接比对对象：CTk 是复合控件，Tk 事件里的 event.widget 往往是
    # 内部那块画布 / 内层 tkinter.Entry，而不是 Python 层那个 CTkButton。
    node: tk.Misc | None = widget
    while node is not None:
        if node is ancestor:
            return True
        node = getattr(node, "master", None)
    return False


def _set_toggle_text(toggle: ctk.CTkButton | None, text: str) -> None:
    """把调用方那个 ▼ 按钮的文字改成 text（▲ 或 ▼），失败一律静默。

    只做「换个图标」这一件事，所以任何异常都不值得往外冒：按钮可能早就没了
    （用户关编辑记录弹窗时，那个 ▼ 会和弹窗一起被连带销毁）；形参明确为 CTkButton，
    正常支持 text 选项。图标属于提示性信息——宁可这一次没翻过来，
    也绝不能让关闭流程卡在半路。
    """
    if toggle is None:
        return
    try:
        if not toggle.winfo_exists():
            return
        toggle.configure(text=text)
    except (tk.TclError, AttributeError, ValueError):
        # 按钮销毁或配置过程异常时保留容错，避免图标更新打断关闭流程。
        pass


def _on_any_click(event: tk.Event) -> None:
    """全局 <Button-1> 监视：点到弹窗外的地方就把弹窗关掉。

    弹窗放弃了 grab_set（见模块 docstring），点弹窗外的行为不再被 Tk 拦下，得自己判断。
    判定口径：
      - 点击落在弹窗自己身上 → 不关（否则列表里一项都点不中）；
      - 点击落在调用方那个 ▼ 按钮上 → 不关。这个白名单是必须的：鼠标点击会拆成
        <Button-1> 与 <ButtonRelease-1> 两次事件，如果按下时就把列表关掉，
        紧接着按钮的 command 回调又把列表重新打开，表现出来就是「点了 ▼ 闪一下没关上」；
      - 其余情况一律关掉。
    注意事件只会送到本应用内部（点桌面或别的程序 Tk 根本收不到），
    所以「切出去看个数再切回来」不会把列表弄丢。
    """
    active = _active_picker
    if active is None:
        # 常态：没有弹窗。这一句就是常驻回调的全部日常开销。
        return
    widget = event.widget
    if not isinstance(widget, tk.Misc):
        return
    try:
        if not active.dialog.winfo_exists():
            return
        inside_dialog = widget.winfo_toplevel() is active.dialog
    except tk.TclError:
        return
    if inside_dialog:
        return
    if active.toggle is not None and _is_inside(widget, active.toggle):
        return
    active.close()


def _ensure_click_monitor(widget: tk.Misc) -> None:
    """当前根窗只装一次全局监视器，缓存窗销毁/重建不重复注册。"""
    global _click_monitor_root
    root = widget.winfo_toplevel()._root()  # type: ignore[reportAttributeAccessIssue] # Tk运行时提供_root，绑定归解释器根窗持有。
    if _click_monitor_root is root:
        return
    try:
        # 必须 add="+"：CTk 已经在这个标签上挂了它自己的 set_focus 回调，
        # 不加 "+" 会把那一条覆盖掉，CTkEntry/CTkTextbox 点别处失焦就废了。
        tk.Misc.bind_all(root, "<Button-1>", _on_any_click, add="+")
    except tk.TclError:
        # 装不上只影响「点外面关闭」这一个便利功能，弹窗本身照常可用。
        return
    _click_monitor_root = root



def _alive(widget: tk.Misc | None) -> bool:
    if widget is None:
        return False  # Tk的master类型允许None，无创建父窗的缓存不能继续复用。
    try:
        return bool(widget.winfo_exists()) and not (
            isinstance(widget, ManagedToplevel) and widget.closing
        )
    except tk.TclError:
        return False  # 解释器关闭期间不能恢复grab或对死控件定位。


def _current(session: _Session) -> bool:
    # 窗口可被不同调用接管，存活检查不能替代这三个身份条件。
    return session.open and _active_picker is session and _win is session.dialog


def _end_session(session: _Session) -> None:
    """幂等收尾本次任务/绑定；仅当前拥有者可隐藏共享窗并归还焦点/grab，最后唤醒旧栈。"""
    global _active_picker
    if not session.open:
        return
    session.open = False  # 先关闭身份守卫，清理中的事件重入不再续期旧任务。
    owns_window = _active_picker is session
    if owns_window:
        _active_picker = None
    for task in tuple(session.tasks.values()):
        try:
            session.dialog.after_cancel(task)
        except (tk.TclError, ValueError):
            pass  # 已执行或外部Destroy已取消的句柄仍须从会话清单移除。
    session.tasks.clear()
    for widget, sequence, funcid, script in session.bindings:
        try:
            current = tk.Misc.bind(widget, sequence)
            widget.tk.call("bind", str(widget), sequence, current.replace(script, "").strip())  # Tk默认unbind不识别存活守卫，须精确摘本会话脚本并保留其他订阅。
            widget.deletecommand(funcid)  # 立即回收命令；已进入分发的旧脚本由Tcl守卫拦截。
        except (tk.TclError, ValueError):
            pass  # 父窗销毁时该绑定可能已被Tk收回，其他收尾仍须继续。
    session.bindings.clear()
    _set_toggle_text(session.toggle, _TOGGLE_TEXT_CLOSED)
    if owns_window:
        try:
            session.dialog.withdraw()
        except tk.TclError:
            pass  # 原生Destroy路径也须继续通知等待。
        # 只归还存活且未closing的原grab；不覆盖后来其他窗口取得的新grab。
        if session.previous_grab is not None and _alive(session.parent) and _alive(session.previous_grab):
            try:
                held = session.parent.grab_current()
                if held is None:
                    session.previous_grab.grab_set()
            except tk.TclError:
                pass  # 原生父窗退出和grab查询可能交错，不能阻止信号通知。
        if _alive(session.parent) and _alive(session.anchor):
            try:
                session.anchor.focus_set()
            except tk.TclError:
                pass  # 锚点已退出时不恢复焦点。
    try:
        session.signal.set(True)  # 每次信号独立；旧调用栈只取自己的结果，不读新会话。
    except tk.TclError:
        pass  # 整个解释器退出时变量可能已被回收。


def _on_window_destroy(event: tk.Event) -> None:
    global _win
    window = _win
    if window is None or event.widget is not window:
        return
    session = _active_picker
    if session is not None and session.dialog is window:
        _end_session(session)
    _win = None  # Tk创建父窗销毁会连带销毁缓存；下次必须重建而非使用死窗口。


def _ensure_window(parent: tk.Tk | tk.Toplevel) -> _TagWindow:
    # 活动父窗可变化，创建父窗仍决定缓存寿命；不能复用 master 已退出的窗口。
    global _win
    if _win is not None:
        if _alive(_win) and _alive(_win.master):
            return _win
        stale = _win
        _win = None
        try:
            stale.destroy()  # 创建父窗已closing时也不接管其缓存，避免新会话随旧父窗延期退出而丢失。
        except tk.TclError:
            pass  # 外部原生Destroy已经收回窗口时只清缓存，下次仍正常重建。
    try:
        _win = _TagWindow(parent)
        return _win
    except BaseException:
        # 初始化未赋给_win时也释放ManagedToplevel的构建登记和半成品控件。
        for child in tuple(parent.children.values()):
            if isinstance(child, _TagWindow):
                child.finish_setup()
                child.destroy()
        raise


def _schedule(session: _Session, key: str, callback: Callable[[], None], ms: int | None = None) -> str | None:
    """同会话同键只保留一个任务，回调消费句柄后再次验证身份；ms=None 走 idle。"""
    if not _current(session) or session.dialog.closing:
        return None  # 同步准备期间也可能已取消，不能在结束后重新登记旧会话便条。
    previous = session.tasks.pop(key, None)
    if previous is not None:
        session.dialog.after_cancel(previous)
    def run() -> None:
        session.tasks.pop(key, None)
        if _current(session):
            callback()  # 闭包只携带会话身份；业务回调现读当前会话，旧便条不能操作新窗。
    task = (
        session.dialog.after_idle(run) if ms is None else session.dialog.after(ms, run)
    )
    if task is not None:
        session.tasks[key] = task
    return task


def _bind_session(session: _Session, widget: tk.Misc, sequence: str, callback) -> None:
    def dispatch(event: tk.Event) -> None:
        if _current(session):
            callback(event)  # 同一窗复用时，排队的旧事件不允许处理新会话。
    funcid = tk.Misc.bind(widget, sequence, dispatch, add="+")
    if funcid:
        current = tk.Misc.bind(widget, sequence)
        prefix = 'if {"[' + funcid + ' '
        own_script = next(line for line in current.splitlines() if line.startswith(prefix))
        guarded_script = "if {[llength [info commands " + funcid + "]]} { " + own_script + " }"  # 嵌套事件可能先删命令，必须在进入Python前由Tcl检查存活。
        widget.tk.call("bind", str(widget), sequence, current.replace(own_script, guarded_script))
        session.bindings.append((widget, sequence, funcid, guarded_script))


def _cancel() -> None:
    if _active_picker is not None:
        _end_session(_active_picker)


def _complete() -> None:
    session = _active_picker
    if session is not None:
        session.result = tuple(session.selected_order)
        _end_session(session)


def _on_escape(_event: tk.Event) -> None:
    _cancel()


def _on_return(_event: tk.Event) -> None:
    _save_current()


def _set_hint(text: str, error: bool = False) -> None:
    if _win is not None:
        _win.hint_label.configure(text=text, text_color=_ERROR_COLOR if error else _HINT_COLOR)


def _apply_selection_style(name: str) -> None:
    session, window = _active_picker, _win
    if session is None or window is None or name not in window.rows:
        return
    selected = name in session.selected_set
    row = window.rows[name]
    if row.selected == selected:
        return  # 缓存上次视觉状态，只更新变化的高亮，不触发整列CTk重绘。
    row.name_button.configure(
        fg_color=_ACCENT_COLOR if selected else "transparent",
        hover_color=_ACCENT_HOVER_COLOR if selected else _SUBTLE_HOVER_COLOR,
        text_color="#FFFFFF" if selected else _TEXT_COLOR,
    )
    row.selected = selected


def _toggle_selection(name: str) -> None:
    session = _active_picker
    if session is None or name not in session.candidates:
        return
    if name in session.selected_set:
        session.selected_set.remove(name)
        session.selected_order.remove(name)
    else:
        session.selected_set.add(name)
        session.selected_order.append(name)
    _apply_selection_style(name)


def _scroll_list(fraction: float) -> None:
    session = _active_picker
    if session is None:
        return
    def scroll() -> None:
        window = session.dialog
        try:
            canvas = window.list_frame._parent_canvas
            row_height = int(round(_ITEM_HEIGHT * _window_scaling(window))) + 2
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(),
                                          max(len(window.rows) * row_height, canvas.winfo_height())))
            canvas.yview_moveto(fraction)
        except (tk.TclError, AttributeError):
            pass  # CTk没有公开滚动API，私有画布失效只影响滚动，不影响偏好保存。
    _schedule(session, "scroll_idle", scroll)


def _save_current() -> None:
    # 保存只扩充常用候选，不自动选中；该偏好已落盘，即使稍后取消选择也不能假装回滚。
    session = _active_picker
    if session is None:
        return
    name = session.dialog.save_entry.get().strip()
    if not name:
        _set_hint("先在上面的输入框里写下标签名，再点 + 保存", error=True)
    elif save_tag(name):
        session.dialog.save_entry.delete(0, "end")
        _render_list()
        _scroll_list(1.0)
    else:
        _set_hint(f"保存失败：{name} 没能写入标签文件", error=True)


def _delete_tag(name: str) -> None:
    # 同步移除本次选中项及常用候选，账本中的历史标签不在本窗修改范围内。
    session = _active_picker
    if session is None:
        return
    if not delete_tag(name):
        _set_hint(f"删除失败：{name} 没能写入标签文件", error=True)
        return
    if name in session.selected_set:
        session.selected_set.remove(name)
        session.selected_order.remove(name)
    _render_list()  # pin/delete/save只走同一个候选同步出口，避免状态口径分叉。


def _pin_tag(name: str) -> None:
    if _active_picker is None:
        return
    if move_tag_to_top(name):
        _render_list()
        _scroll_list(0.0)
    else:
        _set_hint(f"置顶失败：{name} 没能写入标签文件", error=True)


def _build_row(window: _TagWindow, name: str, first: bool) -> _Row:
    frame = ctk.CTkFrame(window.list_frame, fg_color="transparent", corner_radius=0)
    frame.pack(fill="x", padx=4, pady=1)
    # 名称是候选身份，闭包只保存身份；动作函数现读会话，不保存旧父窗/选择。
    ctk.CTkButton(
        frame, text=_ACTION_REMOVE_TEXT, command=lambda: _delete_tag(name),
        width=_ACTION_BUTTON_WIDTH, height=_ITEM_HEIGHT - 4, corner_radius=6,
        fg_color="transparent", hover_color=_SUBTLE_HOVER_COLOR, text_color=_HINT_COLOR,
        font=("Microsoft YaHei UI", 12),
    ).pack(side="right")
    pin = ctk.CTkButton(
        frame, text=_ACTION_PIN_TEXT, command=lambda: _pin_tag(name),
        width=_ACTION_BUTTON_WIDTH, height=_ITEM_HEIGHT - 4, corner_radius=6,
        fg_color="transparent", hover_color=_SUBTLE_HOVER_COLOR, text_color=_HINT_COLOR,
        text_color_disabled=_SUBTLE_HOVER_COLOR, state="disabled" if first else "normal",
        font=("Microsoft YaHei UI", 12),
    )
    pin.pack(side="right", padx=(0, 2))
    # 动作按钮先pack保留宽度，名称width=1只占剩余空间，长名称不挤掉删除按钮。
    button = ctk.CTkButton(
        frame, text=name, command=lambda: _toggle_selection(name), width=1, height=_ITEM_HEIGHT,
        corner_radius=6, anchor="w", fg_color="transparent", hover_color=_SUBTLE_HOVER_COLOR,
        text_color=_TEXT_COLOR, font=("Microsoft YaHei UI", 11),
    )
    button.pack(side="left", fill="x", expand=True)
    return _Row(frame, button, pin, first=first)


def _render_list() -> None:
    """现读候选并增量同步窗口行；选择顺序归会话，不随常用列表置顶重排。"""
    session, window = _active_picker, _win
    if session is None or window is None:
        return
    session.candidates = build_tag_candidates()  # 未出现在候选的自定义已选标签仍保留在 selected_order。
    names = set(session.candidates)
    for name in tuple(window.rows):
        if name not in names:
            window.rows.pop(name).frame.destroy()  # 名称是去重后的身份，移除项才销毁控件。
            window.order.remove(name)
    for index, name in enumerate(session.candidates):
        if name not in window.rows:
            window.rows[name] = _build_row(window, name, index == 0)
            window.order.append(name)  # 新行默认pack到末尾，随后统一校正排序。
        if window.order[index] != name:
            before = window.rows[window.order[index]].frame
            window.rows[name].frame.pack_configure(before=before)
            window.order.remove(name)
            window.order.insert(index, name)  # 只移动乱序的行，未变顺序不用重新pack整列。
        row = window.rows[name]
        first = index == 0
        if row.first != first:
            row.pin_button.configure(state="disabled" if first else "normal")
            row.first = first  # 首行禁用状态双向同步，置顶/删除/保存共用这一出口。
        _apply_selection_style(name)
    if session.candidates:
        if window.empty_label is not None:
            window.empty_label.pack_forget()
    else:
        if window.empty_label is None:
            window.empty_label = ctk.CTkLabel(window.list_frame, text="暂无标签",
                                            font=("Microsoft YaHei UI", 11), text_color=_TEXT_COLOR)
        window.empty_label.pack(pady=12)  # 空列表提示也属于窗口，不保留任何会话数据。
    _set_hint(_HINT_LINE_1)
    _resize_dialog(len(session.candidates))


def _resize_dialog(count: int) -> None:
    session = _active_picker
    if session is None:
        return
    height = max(min(count, _MAX_VISIBLE_ITEMS), 1) * _ITEM_HEIGHT
    if height != session.dialog.list_height:
        session.dialog.list_frame.configure(height=height)
        session.dialog.list_height = height  # 同候选数重复打开不再配置视口，避免额外布局。
    session.height = _PAD * 2 + height + _FOOTER_GAP + _FOOTER_HEIGHT + 2 * _LIST_CORNER_RADIUS
    _reanchor()


def _reanchor(_event: tk.Event | None = None) -> None:
    session = _active_picker
    if session is None:
        return
    if not _alive(session.parent) or not _alive(session.anchor):
        _end_session(session)
        return
    dialog, anchor = session.dialog, session.anchor
    scaling = _window_scaling(anchor)
    width = max(anchor.winfo_width(), int(round(_MIN_WIDTH * scaling)))
    height = int(round(session.height * _window_scaling(dialog)))
    # 位置及宽高全部用物理像素，wm_geometry绕过CTk缩放，隐藏时不读取上次可见尺寸。
    # 只抽取重复坐标公式，标签自己的宽度、会话及轮询机制保持原有语义。
    x, y = anchored_position(
        (anchor.winfo_rootx(), anchor.winfo_rooty(), anchor.winfo_height()),
        (width, height), (dialog.winfo_screenwidth(), dialog.winfo_screenheight()),
    )
    target = (width, height, x, y)
    if target != session.last_target:
        session.last_target = target  # 与上次目标比较，避免异步wm几何查询造成Configure循环。
        if target != dialog.geometry_target:
            dialog.wm_geometry(f"{width}x{height}+{x}+{y}")
            dialog.geometry_target = target  # 同锚点同尺寸复用不再重复向窗口管理器提交几何。


def _poll() -> None:
    # 补充异步移动的锚点检查，每次续期仍须属于原会话；结束后不能产生新的 50ms 心跳。
    session = _active_picker
    if session is not None:
        _reanchor()
        if _current(session):
            _schedule(session, "poll", _poll, _POLL_INTERVAL_MS)


def _on_parent_destroy(event: tk.Event) -> None:
    session = _active_picker
    if session is not None and event.widget is session.parent:
        _end_session(session)  # 当前调用父窗可能不是缓存创建父窗，不能只依赖子窗连带销毁。


def _on_map(event: tk.Event) -> None:
    session = _active_picker
    if session is not None and event.widget is session.dialog:
        _reanchor()
        if _current(session) and not session.ready and "reveal_idle" not in session.tasks:
            _schedule_reveal(session)


def _schedule_reveal(session: _Session) -> None:
    def settle() -> None:
        _reanchor()
        if not _current(session):
            return
        window = session.dialog
        if not window.winfo_ismapped() or not all(
            row.name_button.winfo_ismapped() and row.name_button._canvas.winfo_ismapped()
            for row in window.rows.values()
        ):
            _schedule(session, "reveal_idle", settle, 1)
            return  # 首次映射未完成时返回事件循环，不用update重入或显示空白窗口。
        geometry = (window.winfo_width(), window.winfo_height(), window.winfo_x(), window.winfo_y())
        def reveal() -> None:
            _reanchor()
            if not _current(session):
                return
            actual = (window.winfo_width(), window.winfo_height(), window.winfo_x(), window.winfo_y())
            if actual != geometry:
                _schedule(session, "reveal_idle", settle, 1)
                return
            # 下一轮idle让定位/子控件布局先完成；alpha只标Tk显现意图，不保证DWM最终呈现。
            window.attributes("-alpha", 1.0)
            window.focus_force()
            session.ready = True
        _schedule(session, "reveal_idle", reveal)
    _schedule(session, "reveal_idle", settle)


def _setup_session() -> None:
    if _active_picker is not None:
        _ensure_click_monitor(_active_picker.parent)


def ask_tags(
    parent: tk.Tk | tk.Toplevel,
    anchor: tk.Misc,
    current: str = "",
    toggle_button: ctk.CTkButton | None = None,
    *,
    selected_tags: tuple[str, ...] = (),
) -> tuple[str, ...] | None:
    """在锚点下同步等待多选结果；完成可返回空元组，取消/toggle/无效父窗返回 None。"""
    global _active_picker
    existing = _active_picker
    if existing is not None:
        # 与编辑拒绝重入不同：同锚点取消，不同锚点允许接管；旧 finally 只能收自己的会话。
        same_anchor = existing.anchor is anchor
        _end_session(existing)
        if same_anchor:
            return None
    if not _alive(parent) or not _alive(anchor):
        return None
    window = _ensure_window(parent)
    order = list(dict.fromkeys(tag for tag in selected_tags if tag.strip()))
    session = _Session(
        window, parent, anchor, toggle_button, tk.BooleanVar(parent, value=False),
        order, set(order), [],
    )
    _active_picker = session  # 必须在标题栏update及任何嵌套等待前登记，重入才能结束正确会话。
    try:
        window.transient(parent)
        window.attributes("-alpha", 0.0)
        window.withdraw()
        window.save_entry.delete(0, "end")
        window.save_entry.insert(0, (current or "").strip())
        try:
            held = parent.grab_current()
            if held is not None and held.winfo_toplevel() is parent:
                held.grab_release()
                session.previous_grab = held  # 释放成功才记录借用，退出只归还合法的原父窗grab。
        except tk.TclError:
            pass  # 没有可借grab时仍按非模态打开，不自行取得grab。
        for widget in (window, anchor, anchor.winfo_toplevel()):
            _bind_session(session, widget, "<Configure>", _reanchor)
        _bind_session(session, parent, "<Destroy>", _on_parent_destroy)
        _bind_session(session, window, "<Map>", _on_map)
        _render_list()
        _scroll_list(0.0)  # 每次会话回到列表顶部，不能继承上次未确认时的滚动位置。
        _schedule(session, "setup_idle", _setup_session)
        _schedule(session, "poll", _poll, _POLL_INTERVAL_MS)
        if _current(session):
            _set_toggle_text(toggle_button, _TOGGLE_TEXT_OPEN)
        if not window._tag_built:
            _apply_app_icon(window)
            window.finish_tag_setup()
        if _current(session) and _alive(parent) and _alive(window):
            window.deiconify()
            _schedule_reveal(session)  # 隐藏同步完成后透明映射，再在稳定布局后显现。
            if not session.signal.get():
                window.wait_variable(session.signal)  # 窗口隐藏复用，不能 wait_window；等待期间 Tk 仍会处理外点/新调用。
    except BaseException:
        _end_session(session)
        if _win is window and _active_picker is None:
            window.destroy()
            window.finish_setup()  # 初始化/渲染失败也必须释放#63构建登记并销毁半成品缓存。
        raise
    finally:
        _end_session(session)  # 只收尾本次身份；旧调用返回时不得隐藏后来接管的会话。
    return session.result
