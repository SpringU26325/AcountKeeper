"""标签选择弹窗（需求 3.13）。

对外只暴露 ask_tags()：给「添加记录」输入区和「编辑记录」弹窗的标签字段
提供一个可点击的候选列表，降低手打标签的成本，也避免同一个开销被记成
「餐饮 / 吃饭 / 午饭」多种写法，让 3.4 统计和 3.10 图表里的标签维度能真正聚合。

实现上刻意不用 CTkComboBox（上一版的做法），原因有两个：

1. 视觉不统一。CTkComboBox 的箭头是画在控件内部右上角的一个小折角，
   而日期字段用的是「输入框 + 独立 ▼ 按钮」；两者并排放在同一张卡片里风格对不上。
2. 下拉列表位置压不住（用户反馈：左边缘比输入框左边缘还偏左）。
   它的下拉是 Tk 原生 tkinter.Menu（见 customtkinter 的 dropdown_menu.py）：
   弹出位置由 Tk 的菜单定位逻辑决定，还在 y 方向额外叠加了一段固定偏移，
   Windows 下这个菜单窗口自带一圈 borderwidth，会向请求坐标之外再扩出去；
   更麻烦的是 post() 之后窗口尚未映射，此时查询 winfo_rootx()/winfo_width()
   只会拿到 0/1（实测 viewable=0），代码里根本没有可校正的时机。

本模块改用 CTkToplevel 自己画一列标签项：位置、宽度、配色全部由我们控制，
既不经过 Tk 原生菜单，也就不需要去改 CustomTkinter 的下拉实现。
布局与提交流程对齐 calendar_picker.py，保证两个弹窗看起来是一家人。

交互上本弹窗刻意**不** grab_set，这一点与 calendar_picker 相反，原因是需求要求
「再点一次同一个 ▼ 就把列表关掉」和「点弹窗外的别处也关掉」：只要 grab 住，
弹窗外的一切鼠标事件都会被 Tk 直接丢掉，第二次点 ▼ 根本进不来，toggle 永远做不到。
放弃 grab 的代价是弹窗不再模态——关掉它的那一次外部点击会同时作用到被点的控件上
（点「添加」就真的会添一条记录）。这一点无法两全，只能选 toggle。

弹窗底部是一条操作区（footer，见下方 _FOOTER_* 常量与「底部操作区」那一段）：
第一行是「新标签输入框 + 「+ 保存」按钮」，第二行是一行提示语；列表里每一项右侧
有「顶」和「×」两个按钮：「顶」把该标签移到候选列表最前（tag_prefs.move_tag_to_top），
「×」把它从候选里**真删掉**（tag_prefs.delete_tag）。
这些动作全部交给 tag_prefs 落盘，本模块通过 _render_list 同步候选，保留未变化的行。

footer 里那个输入框是**弹窗自己的**，不绑主窗口的 tag_input_var：存什么完全以它
里面的文字为准，调用方只在「完成」时通过返回值收取本次有序选择。
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
_GAP_ABOVE = 6  # 弹窗与输入框之间的竖向缝隙
_MIN_WIDTH = 180  # 输入框窄到离谱时的兜底宽度（逻辑像素）
_SCREEN_MARGIN = 8  # 贴边保护，避免弹窗压在屏幕边缘上
_POLL_INTERVAL_MS = 50  # 复查锚点位置的轮询间隔（Windows 下窗口移动是异步的）


@dataclass
class _Session:
    dialog: _TagWindow
    parent: tk.Tk | tk.Toplevel
    anchor: tk.Misc
    toggle: ctk.CTkButton | None
    signal: tk.BooleanVar
    selected_order: list[str]
    selected_set: set[str]
    candidates: list[str]
    previous_grab: tk.Misc | None = None
    result: tuple[str, ...] | None = None
    open: bool = True
    ready: bool = False
    height: int = 1
    last_target: tuple[int, int, int, int] | None = None
    tasks: dict[str, str] = field(default_factory=dict)
    bindings: list[tuple[tk.Misc, str, str, str]] = field(default_factory=list)

    def close(self) -> None:
        _end_session(self)  # 活动登记保存会话身份，旧调用的收尾不得误关新会话。


@dataclass
class _Row:
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
    """取控件所在窗口的 DPI 缩放系数（1.0 表示 96 DPI）。

    需要它是因为两套坐标口径不一致：CTkToplevel.geometry()、控件的 width/height
    都按「逻辑像素」解释、由 CTk 乘上缩放系数变成物理像素，而 winfo_* 系列
    （winfo_width / winfo_rootx）返回的全是物理像素。本模块要把输入框的物理宽度
    换算回逻辑宽度交给 geometry()，否则在 125% 缩放下弹窗会宽出四分之一。

    ScalingTracker 是 customtkinter 包顶层公开导出的类（ctk.ScalingTracker），
    不是内部私有实现；万一取不到（例如控件还没挂到已注册的窗口上），退回 1.0，
    最坏结果只是弹窗宽度不合身，功能不受影响。
    """
    try:
        scaling = float(ctk.ScalingTracker.get_window_scaling(widget))
    except Exception:
        return 1.0
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
    return session.open and _active_picker is session and _win is session.dialog


def _end_session(session: _Session) -> None:
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
    session, window = _active_picker, _win
    if session is None or window is None:
        return
    session.candidates = build_tag_candidates()
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
    x = max(_SCREEN_MARGIN, min(anchor.winfo_rootx(), dialog.winfo_screenwidth() - width - _SCREEN_MARGIN))
    y = anchor.winfo_rooty() + anchor.winfo_height() + _GAP_ABOVE
    if y + height > dialog.winfo_screenheight() - _SCREEN_MARGIN:
        above = anchor.winfo_rooty() - height - _GAP_ABOVE
        y = above if above >= _SCREEN_MARGIN else max(
            dialog.winfo_screenheight() - height - _SCREEN_MARGIN, _SCREEN_MARGIN
        )
    target = (width, height, x, y)
    if target != session.last_target:
        session.last_target = target  # 与上次目标比较，避免异步wm几何查询造成Configure循环。
        if target != dialog.geometry_target:
            dialog.wm_geometry(f"{width}x{height}+{x}+{y}")
            dialog.geometry_target = target  # 同锚点同尺寸复用不再重复向窗口管理器提交几何。


def _poll() -> None:
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
    """在锚点下打开多选候选，完成返回选中顺序元组，其他关闭返回None。"""
    global _active_picker
    existing = _active_picker
    if existing is not None:
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
                window.wait_variable(session.signal)
    except BaseException:
        _end_session(session)
        if _win is window and _active_picker is None:
            window.destroy()
            window.finish_setup()  # 初始化/渲染失败也必须释放#63构建登记并销毁半成品缓存。
        raise
    finally:
        _end_session(session)  # 只收尾本次身份；旧调用返回时不得隐藏后来接管的会话。
    return session.result
