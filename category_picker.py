"""类别选择弹窗（需求 3.13）。

对外只暴露 ask_category()：给「添加记录」输入区和「编辑记录」弹窗的类别字段
提供一个可点击的候选列表，降低手打类别的成本，也避免同一个开销被记成
「餐饮 / 吃饭 / 午饭」多种写法，让 3.4 统计和 3.10 图表里的类别维度能真正聚合。

实现上刻意不用 CTkComboBox（上一版的做法），原因有两个：

1. 视觉不统一。CTkComboBox 的箭头是画在控件内部右上角的一个小折角，
   而日期字段用的是「输入框 + 独立 ▼ 按钮」；两者并排放在同一张卡片里风格对不上。
2. 下拉列表位置压不住（用户反馈：左边缘比输入框左边缘还偏左）。
   它的下拉是 Tk 原生 tkinter.Menu（见 customtkinter 的 dropdown_menu.py）：
   弹出位置由 Tk 的菜单定位逻辑决定，还在 y 方向额外叠加了一段固定偏移，
   Windows 下这个菜单窗口自带一圈 borderwidth，会向请求坐标之外再扩出去；
   更麻烦的是 post() 之后窗口尚未映射，此时查询 winfo_rootx()/winfo_width()
   只会拿到 0/1（实测 viewable=0），代码里根本没有可校正的时机。

本模块改用 CTkToplevel 自己画一列类别项：位置、宽度、配色全部由我们控制，
既不经过 Tk 原生菜单，也就不需要去改 CustomTkinter 的下拉实现。
布局与提交流程对齐 calendar_picker.py，保证两个弹窗看起来是一家人。

交互上本弹窗刻意**不** grab_set，这一点与 calendar_picker 相反，原因是需求要求
「再点一次同一个 ▼ 就把列表关掉」和「点弹窗外的别处也关掉」：只要 grab 住，
弹窗外的一切鼠标事件都会被 Tk 直接丢掉，第二次点 ▼ 根本进不来，toggle 永远做不到。
放弃 grab 的代价是弹窗不再模态——关掉它的那一次外部点击会同时作用到被点的控件上
（点「添加」就真的会添一条记录）。这一点无法两全，只能选 toggle。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from typing import Callable

import customtkinter as ctk

# 与 calendar_picker.py / dialogs.py 的弹窗保持同一套浅色主题配色。
_BG_COLOR = "#F0F4F8"
_TEXT_COLOR = "#455A64"
_ACCENT_COLOR = "#2F80ED"
_ACCENT_HOVER_COLOR = "#256AC4"
_SUBTLE_HOVER_COLOR = "#D2DEE9"
_CANCEL_COLOR = "#90A4AE"
_CANCEL_HOVER_COLOR = "#78909C"
_SCROLLBAR_COLOR = "#C6D4DF"
_SCROLLBAR_HOVER_COLOR = "#90A4AE"

# 单个类别项的高度，以及列表最多露出几项（超出的部分靠滚动条看）。
_ITEM_HEIGHT = 32
_MAX_VISIBLE_ITEMS = 7

_LIST_CORNER_RADIUS = 8
_PAD = 10  # 弹窗四周内边距
_FOOTER_HEIGHT = 34  # 「取消」按钮高度
_BUTTON_GAP = 6  # 列表与「取消」按钮之间的竖向间距
_GAP_ABOVE = 6  # 弹窗与输入框之间的竖向缝隙
_MIN_WIDTH = 180  # 输入框窄到离谱时的兜底宽度（逻辑像素）
_SCREEN_MARGIN = 8  # 贴边保护，避免弹窗压在屏幕边缘上
_POLL_INTERVAL_MS = 50  # 复查锚点位置的轮询间隔（Windows 下窗口移动是异步的）


@dataclass
class _ActivePicker:
    """当前打开的类别弹窗（同一时刻只允许一个，见下方 _active_picker）。"""

    dialog: ctk.CTkToplevel
    anchor: tk.Misc  # 锚点输入框：用来判断用户是不是又点了同一个 ▼
    toggle: tk.Misc | None  # 调用方的 ▼ 按钮：点它算 toggle，不算「点了外面」
    close: Callable[[], None]  # 关闭函数，由 ask_category 内部的闭包提供


_active_picker: _ActivePicker | None = None

# 全局 <Button-1> 监视器是否已安装。这里刻意「只装一次、之后永不摘」：
# CustomTkinter 自己在每个 CTk / CTkToplevel 上都会绑一个全局 <Button-1>
# （ctk_tk.py:86、ctk_toplevel.py:82，用来让 CTkEntry/CTkTextbox 在点别处时失焦），
# 也就是说 "all" 绑定标签上的 <Button-1> 是别人也在用的公共资源——
# 一旦 unbind_all("<Button-1>")，CTk 那套「点空白处失焦」会跟着一起失效；
# 而 tk.Misc.unbind 只能按 funcid 精确摘 self._w 上的绑定，摘不到 "all" 标签，
# 想「用完就摘」其实无路可走。所以只剩常驻一个空转回调这一种写法：
# 没有弹窗时它第一句就 return，开销可以忽略，换来的是绝不去动别人的全局绑定。
_click_monitor_installed = False


def _apply_logo_icon(window: tk.Misc) -> None:
    """为弹窗设置项目 logo 图标。

    这里没有复用 dialogs._apply_logo_icon：dialogs.py 需要导入本模块调用
    ask_category，若再反向导入会形成循环导入，所以保留一份极小的私有实现。
    """
    try:
        icon_path = Path(__file__).resolve().parent / "image" / "logo.ico"
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        # 图标只是装饰，缺失或平台不支持时静默忽略，绝不能因此让类别列表打不开。
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


def _register_picker(item: _ActivePicker) -> None:
    """登记「当前打开的类别弹窗」。"""
    global _active_picker
    _active_picker = item


def _clear_picker(dialog: tk.Misc) -> None:
    """撤销登记；只有登记的还是自己时才清，避免误清掉后开的新弹窗。"""
    global _active_picker
    if _active_picker is not None and _active_picker.dialog is dialog:
        _active_picker = None


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
    """安装全局 <Button-1> 监视器（进程内只装一次，之后一直留着）。"""
    global _click_monitor_installed
    if _click_monitor_installed:
        return
    try:
        # 必须 add="+"：CTk 已经在这个标签上挂了它自己的 set_focus 回调，
        # 不加 "+" 会把那一条覆盖掉，CTkEntry/CTkTextbox 点别处失焦就废了。
        tk.Misc.bind_all(widget, "<Button-1>", _on_any_click, add="+")
    except tk.TclError:
        # 装不上只影响「点外面关闭」这一个便利功能，弹窗本身照常可用。
        return
    _click_monitor_installed = True


def ask_category(
    parent: tk.Misc,
    anchor: tk.Misc,
    values: list[str],
    current: str = "",
    toggle_button: tk.Misc | None = None,
) -> str | None:
    """在 anchor 控件正下方弹出类别列表。

    Args:
        parent: 父窗口，弹窗以 transient 方式挂在它上面（主窗口或编辑记录弹窗）。
        anchor: 锚点控件（类别输入框）：弹窗左边缘与它左边缘对齐、宽度与它相同、
            上边缘贴在它下边缘再往下 _GAP_ABOVE 像素。
        values: 候选类别（预置 + 历史，调用方已去重）。
        current: 输入框里的当前内容，用于在列表里高亮当前项。
        toggle_button: 调用方那个 ▼ 按钮。点它属于「再点一次关闭」而不是「点外面」，
            所以全局点击监视器要把它排除掉；不传只是少这一个白名单，功能不受影响。

    Returns:
        选中的类别；用户点「取消」、按 ESC、点右上角关闭、再点一次同一个 ▼，
        或者点弹窗外的别处时返回 None。
    """
    # 【toggle / 防叠加】先处理「已经有一个类别弹窗开着」的情况，必须早于建窗：
    # 弹窗的 wait_window 是嵌套事件循环，上一次调用还停在那一行没返回，
    # 但它的窗口是活的，只能由这新一次调用负责关掉。
    #   - 同一个锚点（用户又点了一次同一个 ▼）：关掉旧的，直接返回 None。
    #     调用方拿到 None 就不会去 set 输入框，所以内容一个字符都不会变。
    #   - 别的锚点（主窗口开着时又点了编辑弹窗的 ▼）：关掉旧的再开新的，防连点叠加。
    existing = _active_picker
    if existing is not None:
        same_anchor = existing.anchor is anchor
        existing.close()
        if same_anchor:
            return None

    # 【grab 借还】本弹窗不 grab_set，但调用方自己可能正握着 grab——编辑记录弹窗就是，
    # 它会挡住本弹窗的点击。所以这里把它的 grab 临时借走，_cleanup 里原样还回去。
    # 只认「parent 自己握着 grab」这一种：Tk 的本地 grab 会把发给同应用其他窗口的
    # 鼠标事件全部改投给 grab 窗口，所以别处握着 grab 时本函数压根不会被调用到。
    previous_grab: tk.Misc | None = None
    try:
        current_grab = parent.grab_current()
        holds_grab = current_grab is not None and current_grab.winfo_toplevel() is parent
    except tk.TclError:
        holds_grab = False
    if holds_grab:
        previous_grab = current_grab
        try:
            current_grab.grab_release()
        except tk.TclError:
            previous_grab = None

    dialog = ctk.CTkToplevel(parent)
    dialog.title("选择类别")
    dialog.resizable(False, False)
    dialog.transient(parent)
    dialog.configure(fg_color=_BG_COLOR)
    _apply_logo_icon(dialog)

    dialog_font = ("Microsoft YaHei UI", 11)
    # 与 calendar_picker 同一个闭包捕获写法：Tk 的回调没有返回值可用，
    # 结果只能写进一个可变容器，等 wait_window 结束再由函数返回出去。
    result: list[str | None] = [None]
    current_text = (current or "").strip()

    # 【宽度】与输入框严格等宽，视觉上像从输入框里「掉下来」的一条列表。
    # winfo_width() 是物理像素，交回给 geometry() 前必须先换算成逻辑像素。
    anchor_width = anchor.winfo_width()
    if anchor_width > 1:  # 1 是窗口尚未映射时 winfo_* 的谎报值
        width = max(int(round(anchor_width / _window_scaling(anchor))), _MIN_WIDTH)
    else:
        width = _MIN_WIDTH

    # 【高度】最多露出 _MAX_VISIBLE_ITEMS 项，其余靠滚动条看：候选 = 预置 + 历史，
    # 数量只增不减，不限高的话弹窗迟早会长到屏幕外面去。
    visible_items = max(min(len(values), _MAX_VISIBLE_ITEMS), 1)
    list_height = visible_items * _ITEM_HEIGHT
    height = _PAD + list_height + _BUTTON_GAP + _FOOTER_HEIGHT + _PAD

    dialog.geometry(f"{width}x{height}")

    # 需要在关闭时摘掉的东西，先建好容器再挂绑定。
    # bound 存 bind() 返回的 funcid，poll_id 存轮询定时器 id，两者都在下方「把弹窗
    # 钉到输入框」那一段里填值——所以这里有值之前先摆空容器，_cleanup 定义顺序
    # 才能和 _choose/_cancel 挨在一起（闭包在调用时才取值，不受定义先后影响）。
    bound: list[tuple[tk.Misc, str, str | None]] = []
    poll_id: list[str | None] = [None]
    _cleaned: list[bool] = [False]

    def _cleanup() -> None:
        """关闭弹窗时统一收尾：摘掉 <Configure> 回调和轮询定时器。

        为什么必须摘：_reanchor 同时挂在「锚点」和「锚点所属顶层窗口」上，
        而锚点所属顶层窗口在主窗口场景下就是主窗口本身——主窗口每拖动/缩放一次
        就发一次 <Configure>。不摘的话，每开合一次列表就在主窗口和锚点上多积一个
        死回调，回调里还各要跑一次 Tcl 调用（winfo_exists），越用越漏。

        用 _cleaned 标记保证只跑一次：点选、ESC、取消、右上角关闭、父窗口被销毁
        把它连带销毁、以及函数返回前兜底，这六条路径最终都汇到这里。
        """
        if _cleaned[0]:
            return
        _cleaned[0] = True
        # 定时器先停：否则清理完之后还可能再回调一次 _reanchor。
        if poll_id[0] is not None:
            try:
                dialog.after_cancel(poll_id[0])
            except Exception:
                # 弹窗已销毁时定时器可能已被 Tk 回收，取消不到也不算错。
                pass
            poll_id[0] = None
        for widget, sequence, funcid in bound:
            if funcid is None:
                # 走不到：下面三处都用 tk.Misc.bind 绑定，一定有 id 返回。
                # 留着这道闸门，是为了万一将来换成别的控件也不至于退化成
                # 「清空整条序列」那种会误伤别人的写法。
                continue
            try:
                # winfo_exists() 判断是必须的：用户点编辑记录弹窗的关闭按钮时，
                # 弹窗和锚点会被一起连带销毁，对死窗口 unbind 会抛 TclError。
                # 这种窗口的绑定由 Tk 随窗口一起回收，本来也不需要我们摘。
                #
                # 必须调 tk.Misc.unbind，不能调 widget.unbind：
                #   1) 锚点是 CTkEntry，而 CTkEntry.unbind(sequence, funcid) 只要
                #      收到非 None 的 funcid 就直接抛 ValueError（见 ctk_entry.py:296，
                #      CTkFrame/CTkButton 等同理），它只认「清空整条序列」这一种用法；
                #   2) 也不能退化成 unbind(sequence)：那会把挂在同一个窗口上的
                #      **所有**同名事件一并清掉，误伤面太大——同工程里
                #      CTkScrollableFrame 就在自己的窗口上绑了 <Configure>
                #      （ctk_scrollable_frame.py:78）。
                # tk.Misc.unbind 作用在控件真实的 Tk 窗口（_w）上，Python 3.13 的
                # 实现是按 funcid 逐行剔除绑定脚本（见 tkinter.Misc._unbind），
                # 所以既不碰 CTk 的封装语义，也不会波及别人的回调。
                if widget.winfo_exists():
                    tk.Misc.unbind(widget, sequence, funcid)
            except tk.TclError:
                pass
        # 撤销「当前弹窗」登记：不清的话下一次 ask_category 会对一个已销毁的弹窗
        # 调 close()，toggle 判断也跟着错乱。
        _clear_picker(dialog)
        # 把开头借走的 grab 还回调用方（编辑记录弹窗的模态性不能因为开了个类别列表
        # 就永久丢掉）。调用方可能已被连带销毁，所以要先判断窗口还在不在。
        if previous_grab is not None:
            try:
                if previous_grab.winfo_exists():
                    previous_grab.grab_set()
            except tk.TclError:
                pass
        # 焦点还给锚点：弹窗一销毁焦点就落到一个不存在的窗口上，键盘操作会短暂失灵。
        try:
            if anchor.winfo_exists():
                anchor.focus_set()
        except tk.TclError:
            pass

    def _close_dialog() -> None:
        """统一的关闭出口：先收尾再销毁，保证不存在「销毁了但没摘回调」的窗口期。"""
        _cleanup()
        try:
            dialog.destroy()
        except tk.TclError:
            # 弹窗已经先一步被连带销毁（父窗口被关掉、或 toggle 时旧弹窗正在关闭中）：
            # 这里只是重复销毁一次，忽略即可。
            pass

    def _choose(value: str) -> None:
        """选中某一项：记下结果并关闭弹窗，让 wait_window 返回。"""
        result[0] = value
        _close_dialog()

    def _cancel() -> None:
        """取消：result 保持 None，关闭弹窗。"""
        _close_dialog()

    # ---------- 列表区 ----------
    # 用 CTkScrollableFrame 而不是普通 CTkFrame：它的 height 是「视口」高度，
    # 内容超出后自动出现滚动条（候选里既有预置 10 项，又有用户历史）。
    list_frame = ctk.CTkScrollableFrame(
        dialog,
        width=0,
        height=list_height,
        corner_radius=_LIST_CORNER_RADIUS,
        fg_color="#FFFFFF",
        scrollbar_button_color=_SCROLLBAR_COLOR,
        scrollbar_button_hover_color=_SCROLLBAR_HOVER_COLOR,
    )
    list_frame.pack(fill="both", expand=True, padx=_PAD, pady=(_PAD, _BUTTON_GAP))

    if values:
        for value in values:
            # 当前输入框里已经有的那个类别高亮成主色，用户一眼能看出「现在用的是哪个」。
            is_current = value == current_text
            ctk.CTkButton(
                list_frame,
                text=value,
                # 默认参数绑定 value：闭包直接引用循环变量的话，所有项都会变成最后一项。
                command=lambda picked=value: _choose(picked),
                height=_ITEM_HEIGHT,
                corner_radius=6,
                anchor="w",
                fg_color=_ACCENT_COLOR if is_current else "transparent",
                hover_color=(
                    _ACCENT_HOVER_COLOR if is_current else _SUBTLE_HOVER_COLOR
                ),
                text_color="#FFFFFF" if is_current else _TEXT_COLOR,
                font=dialog_font,
            ).pack(fill="x", padx=4, pady=1)
    else:
        # 理论上不会走到这里（预置类别永远不为空），保底给一句话而不是弹一个空白框。
        ctk.CTkLabel(
            list_frame,
            text="暂无可选类别",
            font=dialog_font,
            text_color=_TEXT_COLOR,
        ).pack(pady=12)

    # ---------- 底部「取消」 ----------
    footer = ctk.CTkFrame(dialog, fg_color="transparent", corner_radius=0)
    footer.pack(fill="x", padx=_PAD, pady=(0, _PAD))
    ctk.CTkButton(
        footer,
        text="取消",
        command=_cancel,
        height=_FOOTER_HEIGHT,
        corner_radius=9,
        fg_color=_CANCEL_COLOR,
        hover_color=_CANCEL_HOVER_COLOR,
        font=dialog_font,
    ).pack(fill="x")

    # ---------- 把弹窗钉到输入框左下方 ----------
    # 位置与宽度都用原生 wm_geometry（物理像素），不用 CTkToplevel.geometry()：
    # 后者会把宽高**和坐标**一起按 DPI 缩放（见 ctk_toplevel.geometry 的
    # _apply_geometry_scaling），而 winfo_* 返回的是物理像素，两者混用会偏几十像素。
    min_width_phys = int(round(_MIN_WIDTH * _window_scaling(anchor)))
    # 存的是 (宽, 高, x, y) 四个值，别少写一个。
    last_target: tuple[int, int, int, int] | None = None

    def _reanchor(_event: tk.Event | None = None) -> None:
        """按输入框的当前位置/宽度重新摆放弹窗：默认在下方，下方放不下时翻到上方。"""
        nonlocal last_target
        if not dialog.winfo_exists():
            # 列表已经关掉、但挂在锚点和它所在窗口上的 <Configure> 还活着，
            # 下次布局一变动就会回调到这里，不挡掉会直接抛 bad window path name。
            return
        anchor_width = anchor.winfo_width()
        anchor_height = anchor.winfo_height()
        if anchor_width <= 1 or anchor_height <= 1:
            return  # 锚点还没完成布局，winfo_* 会谎报 1，等下一次 <Configure>
        height_now = dialog.winfo_height()
        if height_now <= 1:
            return  # 弹窗还没真正映射，同样会谎报 1

        screen_width = dialog.winfo_screenwidth()
        screen_height = dialog.winfo_screenheight()

        # 宽度每次都以锚点的**当前**宽度为准，不能只用创建时算好的那个值：
        # 编辑记录弹窗是先建弹窗、后完成布局的，等它把宽度定下来时，我们早就
        # 按当时的旧宽度画完了（实测旧宽 515px、最终 463px，列表会多出 52px）。
        width_now = max(anchor_width, min_width_phys)

        # 左边缘与输入框左边缘对齐；越界时往回收，保证整条列表都留在屏幕内。
        target_x = min(anchor.winfo_rootx(), screen_width - width_now - _SCREEN_MARGIN)
        target_x = max(target_x, _SCREEN_MARGIN)

        target_y = anchor.winfo_rooty() + anchor_height + _GAP_ABOVE
        if target_y + height_now > screen_height - _SCREEN_MARGIN:
            # 下方空间不够就翻到输入框上方；上方也不够时贴着屏幕底边放。
            above_y = anchor.winfo_rooty() - height_now - _GAP_ABOVE
            target_y = (
                above_y
                if above_y >= _SCREEN_MARGIN
                else max(screen_height - height_now - _SCREEN_MARGIN, _SCREEN_MARGIN)
            )

        # 只比较我们真正要设的这几个量，且拿「上次设过的目标值」比：
        # ① 没有任何变化就不必再移动，直接断掉「移动 -> Configure -> 再移动」的自我循环；
        # ② winfo_* 在 wm_geometry 之后要等一轮事件才更新，拿它比会来回抖。
        target = (width_now, height_now, target_x, target_y)
        if target == last_target:
            return
        last_target = target
        # 宽高和坐标必须一次写完：宽度已经变了，只挪坐标会让列表和输入框对不齐。
        dialog.wm_geometry(f"{width_now}x{height_now}+{target_x}+{target_y}")

    def _poll() -> None:
        """定时复查一次锚点位置。

        光靠 <Configure> 不够：Windows 的 wm_geometry() 是异步生效的，
        弹窗第一次定位时读到的 anchor.winfo_rooty()/winfo_width() 还停在旧窗口上
        （实测旧 y 偏 92px、旧宽偏 52px），而窗口真正移动到位时并不会再给弹窗补发
        <Configure>，纯事件驱动就会一直卡在旧位置。50ms 轮询只在目标值真的变了
        才调用 wm_geometry，稳定后每轮只是一次纯算术比较。
        """
        _reanchor()
        if dialog.winfo_exists():
            poll_id[0] = dialog.after(_POLL_INTERVAL_MS, _poll)

    # 三处绑定一律走 tk.Misc.bind，而不是 widget.bind，原因只有一个：要拿到 funcid。
    # CustomTkinter 的组合控件把 bind() 转发给内部真实的 Tk 控件后**不返回任何东西**
    # （ctk_entry.py:290 / ctk_frame.py:185 都没有 return），拿到 None 就永远没法精确解绑；
    # tk.Misc.bind 作用在控件真实的 Tk 窗口（self._w）上并如实返回 funcid。
    # 对锚点（CTkEntry）而言副作用是绑定落在它自己的外层 Frame 上——CTkEntry 本身就
    # 是 tkinter.Frame（见其 MRO），而锚点宽度/位置读数本来也取自这个外层 Frame，
    # 触发时机一致，实测对齐结果不变。
    # 注意必须写成「先 bind 再登记」，不能反过来：id 只有 bind 的返回值给得出。
    bound.append(
        (dialog, "<Configure>",
         tk.Misc.bind(dialog, "<Configure>", _reanchor, add="+"))
    )
    # 弹窗自己不会因为「锚点变宽/变窄」或「锚点所在窗口移动」而收到 <Configure>，
    # 而编辑记录弹窗恰恰会在弹出列表之后才把宽度和位置定下来，
    # 所以这两类事件也必须挂上，用来做「轮询周期之间」的即时响应。
    # 锚点所在窗口取出来存一份，_cleanup 里才解绑得到同一个对象。
    anchor_window = anchor.winfo_toplevel()
    bound.append(
        (anchor, "<Configure>",
         tk.Misc.bind(anchor, "<Configure>", _reanchor, add="+"))
    )
    bound.append(
        (anchor_window, "<Configure>",
         tk.Misc.bind(anchor_window, "<Configure>", _reanchor, add="+"))
    )
    poll_id[0] = dialog.after(_POLL_INTERVAL_MS, _poll)

    # ESC 与右上角关闭都等同于「取消」，返回 None 时调用方保持输入框原值不动。
    dialog.protocol("WM_DELETE_WINDOW", _cancel)
    dialog.bind("<Escape>", lambda _event: _cancel())

    def _on_destroy(event: tk.Event) -> None:
        """兜底：弹窗被外部销毁（如父窗口关闭把它连带销毁）时同样要摘回调。"""
        # <Destroy> 会沿绑定标签传播，弹窗内部子控件被销毁时也会回调到这里，
        # 用 event.widget is dialog 把范围收窄成「弹窗本身没了」这一种情况。
        if event.widget is dialog:
            _cleanup()

    # 这个绑定只挂在弹窗自己身上，弹窗一销毁就随 Tk 一起回收，不用登记进 bound。
    dialog.bind("<Destroy>", _on_destroy, add="+")

    def _on_map(event: tk.Event) -> None:
        """弹窗真正显示出来时把焦点抢过来，ESC 才可能生效。

        以前靠 grab_set 收键盘，现在没有 grab 了，键盘事件只会送给「拥有焦点的窗口」，
        而点完 ▼ 焦点其实留在主窗口（CTk 那个全局 <Button-1> 回调会把焦点交给刚被点中的
        画布），不主动抢一次的话按 ESC 会毫无反应。
        只认 event.widget is dialog：<Map> 是沿绑定标签传播的，弹窗里每个子控件映射时都会
        回调到这里，那些要滤掉。真正的窗口映射有两次：初次显示，以及 CTkToplevel 在
        Windows 上那次 withdraw() → after(5) → deiconify()（ctk_toplevel.py:280）。
        所以这里不能加「只抢一次」的开关，否则会被第一次那个还没 withdraw 的假显示骗过去。
        """
        if event.widget is not dialog:
            return
        try:
            if not dialog.winfo_exists():
                return
            focused = dialog.focus_get()
            if focused is not None and _is_inside(focused, dialog):
                return  # 焦点已经在弹窗里（例如用户刚点了某一项），不抢
            dialog.focus_force()
        except (tk.TclError, KeyError):
            pass

    dialog.bind("<Map>", _on_map, add="+")

    # 「点弹窗外面就关」的全局监视器：装一次，之后一直留着（原因见
    # _click_monitor_installed 处的注释）。
    _ensure_click_monitor(dialog)

    # 登记为「当前打开的类别弹窗」：下一次 ask_category 靠它判断是 toggle 关闭
    # 还是「换一个锚点重开」，见函数开头那一段。
    _register_picker(
        _ActivePicker(
            dialog=dialog,
            anchor=anchor,
            toggle=toggle_button,
            close=_close_dialog,
        )
    )

    # 这里刻意不 grab_set（与 calendar_picker 相反）：一旦 grab，弹窗外的一切点击都被
    # Tk 拒绝，「再点一次同一个 ▼ 关闭」和「点外面关闭」这两条需求就永远实现不了。
    # 调用方原本握着的 grab 已在函数开头借走，_cleanup 里会原样还回去。
    parent.wait_window(dialog)
    # 再兜一次：不管弹窗走的是哪条关闭路径，函数返回前保证回调和定时器都已摘掉。
    _cleanup()
    return result[0]
