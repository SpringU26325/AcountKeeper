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

弹窗底部是一条操作区（footer，见下方 _FOOTER_* 常量与「底部操作区」那一段）：
第一行是「新类别输入框 + 「+ 保存」按钮」，第二行是一行提示语；列表里每一项右侧
各有一个「×」按钮，点它就把该类别从候选里**真删掉**（category_prefs.delete_user）。
这些动作全部交给 category_prefs 落盘，本模块只负责「改完之后把列表重画一遍」
（_render_list）。

footer 里那个输入框是**弹窗自己的**，不绑主窗口的 category_var：存什么完全以它
里面的文字为准，主窗口输入框只在「点选了某一项」时通过返回值回流一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from typing import Callable

import customtkinter as ctk

# 候选与增删都交给 category_prefs（user 列表就是唯一真源，那份规则只有一份实现）。
# 这里只导入函数、不导入 EXPENSE/INCOME：方向由调用方按关键字传进来，
# 本模块不替调用方决定「没传时算哪一个方向」——猜错方向会静默写错一份列表。
from category_prefs import build_candidates, delete_user, save_user

# 与 calendar_picker.py / dialogs.py 的弹窗保持同一套浅色主题配色。
_BG_COLOR = "#F0F4F8"
_TEXT_COLOR = "#455A64"
_ACCENT_COLOR = "#2F80ED"
_ACCENT_HOVER_COLOR = "#256AC4"
_SUBTLE_HOVER_COLOR = "#D2DEE9"
_SCROLLBAR_COLOR = "#C6D4DF"
_SCROLLBAR_HOVER_COLOR = "#90A4AE"

# 单个类别项的高度，以及列表最多露出几项（超出的部分靠滚动条看）。
_ITEM_HEIGHT = 32
_MAX_VISIBLE_ITEMS = 7

# ---------- 底部操作区（footer） ----------
# 结构：第一行 = 新类别输入框 + 「+ 保存」按钮；第二行 = 一行提示语。
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
# 「可直接输入新类别名」会让人以为自己打的字已经被收下了。
# 「×」不再另做图例：它是各 App 里删掉一项的通用符号，不需要解释；而弹窗最窄时
# （锚点 180px）一行里也塞不下第二句。
_HINT_LINE_1 = "输入新类别后点 + 保存"
_HINT_COLOR = "#90A4AE"
_ERROR_COLOR = "#D64545"  # 写盘失败时把提示语染成警示色

# 动作按钮的文字。× (U+00D7) 是 Microsoft YaHei UI **自带**字形，不依赖字体回退，
# 各 App 里「删除/移除这一项」都用它（关标签页、删块）。
# 刻意不用 ✕(U+2715)：它在 YaHei UI 里没有字形，靠系统字体回退才显示得出来，
# 跨环境一致性不如上面这个原生字符。
_ACTION_REMOVE_TEXT = "×"
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


def _set_toggle_text(toggle: tk.Misc | None, text: str) -> None:
    """把调用方那个 ▼ 按钮的文字改成 text（▲ 或 ▼），失败一律静默。

    只做「换个图标」这一件事，所以任何异常都不值得往外冒：按钮可能早就没了
    （用户关编辑记录弹窗时，那个 ▼ 会和弹窗一起被连带销毁），而形参类型也只承诺
    是 tk.Misc、并不保证它有 text 选项。图标属于提示性信息——宁可这一次没翻过来，
    也绝不能让关闭流程卡在半路。
    """
    if toggle is None:
        return
    try:
        if not toggle.winfo_exists():
            return
        toggle.configure(text=text)
    except (tk.TclError, AttributeError, ValueError):
        # TclError：控件已销毁；AttributeError / ValueError：该控件没有 text 选项。
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
    current: str = "",
    toggle_button: tk.Misc | None = None,
    *,
    direction: str,
) -> str | None:
    """在 anchor 控件正下方弹出类别列表。

    Args:
        parent: 父窗口，弹窗以 transient 方式挂在它上面（主窗口或编辑记录弹窗）。
        anchor: 锚点控件（类别输入框）：弹窗左边缘与它左边缘对齐、宽度与它相同、
            上边缘贴在它下边缘再往下 _GAP_ABOVE 像素。
        current: 输入框里的当前内容，用于在列表里高亮当前项，并预填 footer 的输入框。
        toggle_button: 调用方那个 ▼ 按钮。点它属于「再点一次关闭」而不是「点外面」，
            所以全局点击监视器要把它排除掉。弹窗显示期间它的文字会被翻成 ▲，
            关闭时无论走哪条路径都由 _cleanup 翻回 ▼。不传只是少这一个白名单
            和图标切换，功能不受影响。
        direction: 收支方向（category_prefs.EXPENSE / INCOME）。用关键字强制传入，
            不给默认值：存取哪一份 categories.json 必须由调用方说出来，取错方向
            会静默写到另一份列表上去，而那种错很难在界面上看出来。

    Returns:
        选中的类别；用户按 ESC、点右上角关闭、再点一次同一个 ▼，
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

    def _candidates() -> list[str]:
        """现算候选（就是该方向的 user 列表）。

        每次调用都重新读文件、不缓存：弹窗自己就能保存和删除，改完必须立刻
        反映到列表上，拿一份打开时的快照就永远是旧的。
        """
        return build_candidates(direction)

    # 【宽度】与输入框严格等宽，视觉上像从输入框里「掉下来」的一条列表。
    # winfo_width() 是物理像素，交回给 geometry() 前必须先换算成逻辑像素。
    anchor_width = anchor.winfo_width()
    if anchor_width > 1:  # 1 是窗口尚未映射时 winfo_* 的谎报值
        width = max(int(round(anchor_width / _window_scaling(anchor))), _MIN_WIDTH)
    else:
        width = _MIN_WIDTH

    # 【高度】最多露出 _MAX_VISIBLE_ITEMS 项，其余靠滚动条看：候选数量由用户自己
    # 维护，不限高的话弹窗迟早会长到屏幕外面去。
    # 高度 = 上内边距 + 列表视口 + 列表与 footer 的缝隙 + footer + 下内边距。
    # 底部那个「取消」按钮已经删掉（关闭已有 toggle / 点外面 / ESC / 点选项 /
    # 右上角 × 五条路，再摆一个「取消」纯属重复），现在占用那块位置的是
    # 「+ 保存」这个**非关闭**类操作，它不重复，所以 footer 又回来了。
    visible_items = max(min(len(_candidates()), _MAX_VISIBLE_ITEMS), 1)
    list_height = visible_items * _ITEM_HEIGHT
    # 末尾的 + 2 * _LIST_CORNER_RADIUS 不能漏：列表区控件申请的是「视口高 + 两个圆角」
    # （原因见 _LIST_CORNER_RADIUS 处的说明），弹窗总高按视口高算就会矮 16px。
    height = (
        _PAD + list_height + _FOOTER_GAP + _FOOTER_HEIGHT + 2 * _LIST_CORNER_RADIUS + _PAD
    )

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

        用 _cleaned 标记保证只跑一次：点选、ESC 与右上角关闭（后两者都走 _cancel）、
        父窗口被销毁把它连带销毁、以及函数返回前兜底，这几条路径最终都汇到这里。
        """
        if _cleaned[0]:
            return
        _cleaned[0] = True
        # 先把唤起本次弹窗的那个 ▼ 还原成关态图标。放在这个统一出口而不是各条关闭
        # 路径里，是因为关闭路径共六条（点选、ESC、右上角 ×、点外面、再点同一个 ▼、
        # 父窗口销毁把它连带销毁），分散写必漏；_cleaned 标记又保证这里只跑一次。
        # 时序上也不可能误伤新弹窗：即使本次关闭属于「换一个锚点重开」，这里翻回去的
        # 也是**旧**按钮（toggle_button 取自本函数闭包，与调用方新传进来的那个 ▼ 是
        # 两个不同对象），而新按钮的 ▲ 要等本函数返回、新一次 ask_category 建完窗
        # 才设置，两者不会互相覆盖。
        _set_toggle_text(toggle_button, _TOGGLE_TEXT_CLOSED)
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
        """取消：result 保持 None，关闭弹窗。

        现在只剩 ESC 和右上角 × 两个入口（底部 footer 里没有「取消」按钮），
        保留它是因为「不改动输入框内容地关掉弹窗」这条路必须还在。
        """
        _close_dialog()

    # ---------- 列表区 ----------
    # 用 CTkScrollableFrame 而不是普通 CTkFrame：它的 height 是「视口」高度，
    # 内容超出后自动出现滚动条（候选里既有预置 10 项，又有用户保存的项）。
    list_frame = ctk.CTkScrollableFrame(
        dialog,
        width=0,
        height=list_height,
        corner_radius=_LIST_CORNER_RADIUS,
        fg_color="#FFFFFF",
        scrollbar_button_color=_SCROLLBAR_COLOR,
        scrollbar_button_hover_color=_SCROLLBAR_HOVER_COLOR,
    )
    # 这里只建控件、**先不 pack**：pack 顺序决定「空间不够时压谁」，而 footer 必须
    # 先落地才有保障，所以 list_frame 的 pack 挪到了下面「底部操作区」footer 之后。

    # ---------- 弹窗内的动作 ----------
    # 这几个函数只改 category_prefs、然后重画列表，**都不关弹窗**：
    # 保存完通常紧接着就要点刚出现的那一项，删完可能想接着删下一个，
    # 关掉再让用户点一次 ▼ 是完全多余的往返。
    #
    # 定义顺序说明：下面「底部操作区」里建按钮时要用命令参数引用它们，
    # 所以函数必须先于控件出现（闭包里的 list_frame/hint_label 等反而是调用时才
    # 取值，控件建在函数后面没有关系）。
    def _set_hint(text: str, error: bool = False) -> None:
        """改 footer 提示语。error=True 时换成警示色，用来报告写盘失败。"""
        hint_label.configure(text=text, text_color=_ERROR_COLOR if error else _HINT_COLOR)

    def _save_current() -> None:
        """把 footer 输入框里的类别名存进 user 列表。

        值只从 save_entry 里取，**不再回头看主窗口的输入框**：那一个是「这条记录用
        什么类别」，这一个才是「要把哪个写法存进候选」。把两件事并到一个框里，用户
        就会以为在主窗口打了字等于已经存进候选了——这正是需求要改掉的那点绕。

        成功时一声不吭：列表里当场多出一项就是最直接的反馈，再弹一句「保存成功」
        反而要多点一次才能继续。失败必须说清楚——静默失败会让用户以为已经存好了，
        下次点 ▼ 才发现类别不见了，那时已经无从追查。
        """
        # save_entry 建在这几个函数的下面，这里是「调用时才取值」，所以顺序没问题。
        name = save_entry.get().strip()
        if not name:
            _set_hint("先在上面的输入框里写下类别名，再点 + 保存", error=True)
            return
        if save_user(direction, name):
            # 存下之后清空输入框，拿「框空了」当一次「已经收下了」的反馈：
            # 类别原本就在列表里时 save_user 同样不报错、列表看不出任何变化，
            # 不清空的话用户分不清刚才那一下到底生效没有，只会忍不住再点一次。
            save_entry.delete(0, "end")
            _render_list()
        else:
            # 失败时保留框里的内容：用户多半想照着这个名字重试，或者去手工检查文件，
            # 清空等于逼他重新打一遍。
            _set_hint(f"保存失败：{name} 没能写入类别文件", error=True)

    def _delete_category(name: str) -> None:
        """把某一项从候选里真删掉（列表里当场消失，下次不会再出现）。

        没有二次确认：弹窗里不能用 messagebox（它是另一个顶层窗口，会被「点弹窗外
        就关闭」的监视器当成外部点击，反而先把列表关掉），而需求要的就是「删掉就是
        真的没了」这条直白语义；想找回来只能重新用「+ 保存」输入同名类别。
        """
        if delete_user(direction, name):
            _render_list()
        else:
            _set_hint(f"删除失败：{name} 没能写入类别文件", error=True)

    def _resize_dialog(row_count: int) -> None:
        """按行数改弹窗高度，宽度保持不变。

        宽度交给 _reanchor（它按锚点实时算、而且用物理像素），这里若再按创建时
        那个旧宽度调一次 geometry()，会把宽度拽回旧值、紧接着又被 _reanchor 拉回来，
        屏幕上闪一下；所以这里只改高度，「此刻多宽就保持多宽」。
        """
        visible_items = max(min(row_count, _MAX_VISIBLE_ITEMS), 1)
        new_list_height = visible_items * _ITEM_HEIGHT
        # 让滚动视口跟上新的行数。list_frame 是 expand=True，实际高度由弹窗决定，
        # 单独 configure(height=) 改不动画面；真正生效的是下面那句 geometry()。
        # 这里仍然要写，是为了让内部画布的滚动区域与行数一致，否则会留下
        # 「行少了却还留着旧滚动范围」这种把滚动条拖到底也看不到内容的空档。
        list_frame.configure(height=new_list_height)

        scaling = _window_scaling(dialog)
        try:
            current_width_phys = dialog.winfo_width()
        except tk.TclError:
            current_width_phys = 0
        logical_width = (
            max(int(round(current_width_phys / scaling)), _MIN_WIDTH)
            if current_width_phys > 1  # 1 是窗口尚未映射时 winfo_* 的谎报值
            else width  # 理论上走不到：能点按钮说明弹窗早就映射过了
        )
        # 这条式子必须与建窗时那条**完全一致**（含列表区的圆角增量）：两处只要有一处
        # 少算，就会在「打开时 7 项、删到只剩 3 项」这种改行数的操作后把高度算矮，
        # 列表区被 pack 压掉一截、最后一行又看不全。
        logical_height = (
            _PAD + new_list_height + _FOOTER_GAP + _FOOTER_HEIGHT + 2 * _LIST_CORNER_RADIUS + _PAD
        )
        dialog.geometry(f"{logical_width}x{logical_height}")

    # ---------- 底部操作区 ----------
    # 结构：第一行 = 新类别输入框 + 「+ 保存」按钮；第二行 = 一行提示语。
    #
    # footer 抢先 pack、list_frame 随后 pack，这个顺序是**故意**的，也是本区唯一的
    # 职责：pack 在空间不够时压缩的是「最后 pack 的那个」，而 expand=True 只负责
    # 分配多余空间、并不负责压缩。把 footer 排在最前，亏空就整个落在可滚动的列表
    # 上（表现成可视行数变少、滚动条出来），输入框和提示语始终完整。
    #
    # 反过来写（列表先 pack）时，亏空会落在 footer 身上，footer 内部又继续压它最后
    # pack 的孩子——也就是提示语——于是提示语被压成几个像素高，字全没了。这正是
    # 之前「提示语被压扁」的成因，所以这个顺序不能改回去。
    #
    # side="bottom"/side="top" 是配套的：footer 从底部占走它要的高度，列表再从上边
    # 铺满剩下的部分（它自己带 expand，负责吃掉剩余空间）。
    footer = ctk.CTkFrame(dialog, fg_color="transparent", corner_radius=0)
    footer.pack(side="bottom", fill="x", padx=_PAD, pady=(_FOOTER_GAP, _PAD))

    # pady 上边 _PAD、下边 0：列表与 footer 之间那道缝由 footer 自己的上边 pady
    # (_FOOTER_GAP) 提供，两头都写会变成两道缝。
    list_frame.pack(side="top", fill="both", expand=True, padx=_PAD, pady=(_PAD, 0))

    button_row = ctk.CTkFrame(
        footer, fg_color="transparent", corner_radius=0, height=_BUTTON_ROW_HEIGHT
    )
    button_row.pack(fill="x")

    # 输入框刻意不写 placeholder_text：占位符的语义就是「里面没字时才显示」，而这里
    # 一建出来就要预填 current，两者互斥（CustomTkinter 的占位符还会在失焦且为空时
    # 自己回来，和预填的内容打架）。这个框干什么用，改由下面第一行提示语说明。
    save_entry = ctk.CTkEntry(
        button_row,
        height=_BUTTON_ROW_HEIGHT,
        corner_radius=6,
        border_width=1,
        border_color=_SCROLLBAR_COLOR,
        fg_color="#FFFFFF",
        font=("Microsoft YaHei UI", 11),
        text_color=_TEXT_COLOR,
    )
    # 预填打开时的 current：用户十有八九是「主窗口里已经打好字、想把它存进候选」才
    # 点开列表的，先填好能省一次重复输入。只在建窗时读这一次，之后两边彻底独立，
    # 不做双向同步——弹窗里改了又没点 + 保存的话，关掉弹窗不该反过来改动主窗口。
    if current_text:
        save_entry.insert(0, current_text)

    # 先 pack 按钮（side="right"）再 pack 输入框（side="left", expand=True）：与调用方
    # 「输入框 + ▼ 按钮」「日期框 + 📅 按钮」同一套写法，按钮先钉住右端，输入框吃掉
    # 剩下的宽度，于是这个框能占满「弹窗宽度 − 保存按钮 − 内边距」。
    ctk.CTkButton(
        button_row,
        text=_SAVE_TEXT,
        command=_save_current,
        width=_SAVE_BUTTON_WIDTH,
        height=_BUTTON_ROW_HEIGHT,
        corner_radius=6,
        fg_color=_ACCENT_COLOR,
        hover_color=_ACCENT_HOVER_COLOR,
        text_color="#FFFFFF",
        font=("Microsoft YaHei UI", 11),
    ).pack(side="right")
    save_entry.pack(side="left", fill="x", expand=True)

    # 回车等价于点「+ 保存」：这是个「打字→提交」的输入框，回车提交是通用习惯；
    # 弹窗上本来没有别的 <Return> 绑定，不会和谁抢。
    save_entry.bind("<Return>", lambda _event: _save_current())

    # 提示语直接摆在 footer 的下一行，占满整行宽度（pady=(4,0) 与上面那一行隔开）。
    # 它同时承担「告诉大家这个输入框怎么用」和「写盘失败时报错」两件事，所以不能省
    # ——footer 只留一行输入框的话，失败就没地方说了（messagebox 又不能用）。
    hint_label = ctk.CTkLabel(
        footer,
        text=_HINT_LINE_1,
        # height 与 _HINT_ROW_HEIGHT 严格对齐：不传时 CTkLabel 一律按 28 要空间，
        # 而 _FOOTER_HEIGHT 的预算里这一行只算了 18，差出来的 10px 会把 footer 顶成
        # 「实际需求 > 预算」，进而在空间紧张时把提示语自己压扁。
        height=_HINT_ROW_HEIGHT,
        font=("Microsoft YaHei UI", 10),
        text_color=_HINT_COLOR,
        justify="left",
        anchor="w",
    )
    hint_label.pack(fill="x", pady=(4, 0))

    # ---------- 重画列表 ----------
    # 只在「数据真的变了」的动作之后调用：打开弹窗、保存、删除。

    def _render_list() -> None:
        """按当前候选重画列表，并把弹窗高度改到位。

        整块重画而不是增量改动某一项：候选数量本身会变（保存多一项、删除少一项），
        增量维护得自己记住「哪一项在第几行」，一旦算错就是列表和文件对不上；
        而重画的代价只是十几个小控件，在用户的点击频率下完全无感。
        """
        rows = _candidates()

        # 只销毁列表里的旧行，绝不销毁 list_frame 本身：它是 CTkScrollableFrame，
        # 连它一起重建会把滚动条与内部画布都换掉，既闪一下、又要重新 pack 和定位。
        for child in list_frame.winfo_children():
            child.destroy()

        if rows:
            for name in rows:
                _build_row(name)
        else:
            # 空列表也要给一句话，而不是弹一个白色空框让人以为卡住了。
            # 删到一个不剩时走的就是这一支，怎么再添回来由下面那行提示语负责解释。
            ctk.CTkLabel(
                list_frame,
                text="暂无类别",
                font=dialog_font,
                text_color=_TEXT_COLOR,
            ).pack(pady=12)

        # 提示语回到常态：上一次写盘失败留下的红字，只该活到下一次成功动作为止。
        _set_hint(_HINT_LINE_1)

        _resize_dialog(len(rows))

    def _build_row(name: str) -> None:
        """画一行类别：透明容器 + 名字按钮（左，撑满）+ 删除按钮（右）。

        高亮只画在名字按钮上、容器保持透明，否则整行（连同右边的 ×）会一起变蓝，
        看起来像「连删除按钮也一起被选中了」。
        """
        is_current = name == current_text
        row = ctk.CTkFrame(list_frame, fg_color="transparent", corner_radius=0)
        row.pack(fill="x", padx=4, pady=1)

        ctk.CTkButton(
            row,
            text=_ACTION_REMOVE_TEXT,
            # 默认参数绑定 name：闭包直接引用外层变量的话，所有行都会作用到最后一项。
            command=lambda picked=name: _delete_category(picked),
            width=_ACTION_BUTTON_WIDTH,
            height=_ITEM_HEIGHT - 4,
            corner_radius=6,
            fg_color="transparent",
            hover_color=_SUBTLE_HOVER_COLOR,
            text_color=_HINT_COLOR,
            font=("Microsoft YaHei UI", 12),
        ).pack(side="right")

        ctk.CTkButton(
            row,
            text=name,
            # 同上：默认参数绑定 name，否则每一项都会变成最后一项。
            command=lambda picked=name: _choose(picked),
            height=_ITEM_HEIGHT,
            corner_radius=6,
            anchor="w",
            fg_color=_ACCENT_COLOR if is_current else "transparent",
            hover_color=_ACCENT_HOVER_COLOR if is_current else _SUBTLE_HOVER_COLOR,
            text_color="#FFFFFF" if is_current else _TEXT_COLOR,
            font=dialog_font,
        ).pack(side="left", fill="x", expand=True)

    # 画第一遍。此后只在动作里重画，所以整个弹窗生命周期内 list_frame 只有这一个实例。
    _render_list()

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
        # 光守 dialog 不够，必须连锚点一起守：锚点所在的父窗口被销毁时（典型路径见
        # _cleanup 注释里那条——编辑记录弹窗开着且类别列表已经弹出，用户直接点它右上角 ×），
        # 作为子控件的锚点会**先于**弹窗被销毁，而本回调同时还挂在锚点所在顶层窗口的
        # <Configure> 上，紧接着的一次布局变动就会带一个死的窗口路径找上来，
        # 在下面的 anchor.winfo_width() 抛 TclError: bad window path name（实测 traceback
        # 正落在那一行，只是被 Tk 的回调包装吞成 stderr 噪音，现象是用户关编辑弹窗时
        # 控制台多出一段 traceback）。锚点已经不存在，「对齐锚点」这件事本身就不成立了，
        # 直接返回即可；此时 dialog 可能还活着（销毁顺序就是如此），它的收尾由 _cleanup 负责，
        # 不在这里销毁弹窗。
        if not anchor.winfo_exists():
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

        锚点一旦被销毁（父窗口连带销毁）就不再有对齐目标——_reanchor 里那道守卫
        会让它直接返回，定时器也就没必要再续期了，见下面开头那段守卫。
        """
        # 锚点已经销毁时「对齐锚点」本身就不成立，再每 50ms 醒一次只是白跑（和
        # _reanchor 同一道判断）。所以这里直接 return、不再 after 续期；已触发的这次
        # 定时器 id 已经失效，用 None 标记「没有待处理的定时器」，_cleanup 里便跳过
        # after_cancel（对已失效的 id 调用本就是空操作，这里只是把状态说准）。
        # 注意只停定时器，**不动 dialog**：弹窗的收尾仍归 _cleanup，与 _reanchor 的守卫
        # 保持同一原则——守卫只负责让这个回调早退，不负责替别人决定弹窗的生死，
        # 否则会把「锚点没了但弹窗还活着」这种中间态提前变成「弹窗也没了」。
        if not anchor.winfo_exists():
            poll_id[0] = None
            return
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

    # 图标翻成「开态」：必须在本行之后、wait_window 之前设。
    # 早于 wait_window 是硬要求——_cleanup 会在弹窗关闭时把图标还原成 ▼，
    # 若这一行排在 wait_window 之后，它会先看到弹窗已经关闭、把 ▲ 又盖回去。
    # 晚于 _register_picker 则是因为登记之后弹窗才算真正进入「可交互」状态。
    _set_toggle_text(toggle_button, _TOGGLE_TEXT_OPEN)

    # 这里刻意不 grab_set（与 calendar_picker 相反）：一旦 grab，弹窗外的一切点击都被
    # Tk 拒绝，「再点一次同一个 ▼ 关闭」和「点外面关闭」这两条需求就永远实现不了。
    # 调用方原本握着的 grab 已在函数开头借走，_cleanup 里会原样还回去。
    parent.wait_window(dialog)
    # 再兜一次：不管弹窗走的是哪条关闭路径，函数返回前保证回调和定时器都已摘掉。
    _cleanup()
    return result[0]
