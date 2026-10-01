"""Reusable user-interface frames for AccountKeeper."""

from __future__ import annotations

from datetime import date
import tkinter as tk
from typing import Callable
from tkinter import ttk

import customtkinter as ctk

from calendar_picker import ask_date
# 标签候选统一由 tag_prefs 算（就是它自己那份标签列表），
# 不再从数据库 DISTINCT 取历史标签：那条路会让用户临时输入的写法越积越多，
# 候选读取与偏好写入只在 tag_prefs 维护；UI 不缓存标签池，避免多处实现漂移。
from tag_picker import ask_tags
# 手输的多标签要按顿号拆开，拆分与清理规则（strip / 去空 / 首次出现去重）
# 与偏好层共用一份实现：#58 Step 3a 新增，避免这里再抄一遍口径、两处迟早漂开。
from tag_prefs import split_tag_input

# ---------- 标签 chips 区的几何常量（需求 3.14.3 形态 1） ----------
# 一颗 chip 的高度与圆角：28 + 圆角 14 刚好是「药丸」形，也比一行输入框（36）矮，
# 一眼能看出它是可以点掉的小标，而不是输入框。
_CHIP_HEIGHT = 28
_CHIP_CORNER_RADIUS = 14
_CHIP_GAP_X = 6  # 同一行里 chip 之间的水平间距
_CHIP_GAP_Y = 4  # 换行后上下两行 chip 之间的垂直间距


class TagChipsFrame(ctk.CTkFrame):
    """标签 chips、输入框与多选入口组成的可复用控件。"""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_layout_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=0)
        font_regular = ("Microsoft YaHei UI", 12)
        self.tag_input_var = tk.StringVar()
        self._tags: list[str] = []
        self._tag_buttons: dict[str, ctk.CTkButton] = {}
        self._chips_width = -1
        # 高度重算只关心实际行数变化；宽度变化但仍排成相同行数时无需通知外层。
        self._layout_rows = 0
        self._on_layout_change = on_layout_change
        self.columnconfigure(0, weight=1)

        self.tag_entry = ctk.CTkEntry(
            self,
            height=36,
            font=font_regular,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#F8FAFC",
            textvariable=self.tag_input_var,
        )
        self.tag_entry.grid(row=0, column=0, sticky="ew")
        self.tag_entry.bind("<Return>", lambda _event: self._commit_tag_input())

        self.tag_button = ctk.CTkButton(
            self,
            text="▼",
            command=self._pick_tags,
            width=36,
            height=36,
            corner_radius=9,
            fg_color="#E3EAF2",
            hover_color="#D2DEE9",
            text_color="#243447",
            font=("Microsoft YaHei UI", 11),
        )
        self.tag_button.grid(row=0, column=1, padx=(6, 0), sticky="e")

        self.chips_frame = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.chips_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.chips_frame.grid_remove()
        self.chips_frame.bind("<Configure>", self._on_chips_configure)

    def _commit_tag_input(self) -> None:
        """把输入框里的文字冲刷成 chip，并清空输入框。"""
        self._add_tags(split_tag_input(self.tag_input_var.get()))
        self.tag_input_var.set("")

    def _add_tag(self, name: str) -> bool:
        """加一颗 chip；名字为空或已存在时什么都不做。"""
        return self._add_tags((name,))

    def _add_tags(self, names: tuple[str, ...]) -> bool:
        """批量加 chip，并在整批完成后只安排一次流式重排。"""
        added = False
        for name in names:
            tag = (name or "").strip()
            if not tag or tag in self._tag_buttons:
                continue
            button = ctk.CTkButton(
                self.chips_frame,
                text=f"{tag} ×",
                command=lambda target=tag: self._remove_tag(target),
                height=_CHIP_HEIGHT,
                corner_radius=_CHIP_CORNER_RADIUS,
                fg_color="#E3EAF2",
                hover_color="#D2DEE9",
                text_color="#243447",
                font=("Microsoft YaHei UI", 11),
            )
            self._tag_buttons[tag] = button
            self._tags.append(tag)
            added = True
        if added:
            self._schedule_relayout()
        return added

    def _remove_tag(self, name: str) -> None:
        """删除当前记录的一颗 chip，不触碰常用标签池。"""
        button = self._tag_buttons.pop(name, None)
        if button is None:
            return
        button.destroy()
        if name in self._tags:
            self._tags.remove(name)
        self._schedule_relayout()

    def _schedule_relayout(self) -> None:
        """等新 chip 完成几何测量后再重排，避免按未测量的 1px 宽度换行。"""
        self.chips_frame.grid()
        self.after_idle(self._relayout_tags)

    def _on_chips_configure(self, event: tk.Event) -> None:
        """容器宽度改变才重排，掐断高度变化造成的 Configure 自激。"""
        if event.width == self._chips_width:
            return
        self._chips_width = event.width
        self._relayout_tags()

    def _relayout_tags(self) -> None:
        """按当前宽度流式换行；仅行数变化时通知外层调整高度。"""
        if not self._tag_buttons:
            self.chips_frame.grid_remove()
            rows = 0
        else:
            width = self._chips_available_width()
            column = 0
            row = 0
            used = 0
            for tag in self._tags:
                button = self._tag_buttons.get(tag)
                if button is None:
                    continue
                need = button.winfo_reqwidth() + _CHIP_GAP_X
                if column and width and used + need > width:
                    row += 1
                    column = 0
                    used = 0
                button.grid(
                    row=row,
                    column=column,
                    padx=(0, _CHIP_GAP_X),
                    pady=(0, _CHIP_GAP_Y),
                    sticky="w",
                )
                used += need
                column += 1
            rows = row + 1

        if rows != self._layout_rows:
            self._layout_rows = rows
            if self._on_layout_change is not None:
                self._on_layout_change()

    def _chips_available_width(self) -> int:
        """返回 chips 可用宽度；尚未映射时用 0 表示暂不换行。"""
        for widget in (self.chips_frame, self):
            width = widget.winfo_width()
            if width > 1:
                return width
        return 0

    def get_tags(self) -> tuple[str, ...]:
        """返回 chips 中的标签，不含输入框里尚未提交的文字。"""
        return tuple(self._tags)

    def collect_tags(self) -> tuple[str, ...]:
        """先冲刷输入框残留，再返回提交用的标签元组。"""
        self._commit_tag_input()
        return tuple(self._tags)

    def clear_tags(self) -> None:
        """清空 chips 与输入框，供新增记录成功后复位。"""
        for button in self._tag_buttons.values():
            button.destroy()
        self._tag_buttons.clear()
        self._tags.clear()
        self.tag_input_var.set("")
        self._relayout_tags()

    def set_tags(self, tags: tuple[str, ...]) -> None:
        """用给定标签整体替换 chips，供编辑弹窗按记录值初始化。"""
        self.clear_tags()
        # 复用批量添加的去重、布局调度，确保初始化与用户输入遵循同一规则。
        self._add_tags(tags)

    def _pick_tags(self) -> None:
        """仅在 picker 返回完成结果时合并所选标签与输入框残留。"""
        picked = globals()["ask_tags"](
            self.winfo_toplevel(),
            self.tag_entry,
            self.tag_input_var.get(),
            toggle_button=self.tag_button,
            selected_tags=self.get_tags(),
        )
        if picked is None:
            return
        typed = split_tag_input(self.tag_input_var.get())
        final = tuple(dict.fromkeys((*picked, *typed)))
        self.clear_tags()
        self._add_tags(final)


class InputFrame(ctk.CTkFrame):
    """Input controls for adding a record."""

    def __init__(
        self,
        master: ctk.CTk,
        add_callback: Callable[[], None],
    ) -> None:
        super().__init__(
            master,
            corner_radius=14,
            fg_color="#FFFFFF",
            border_width=1,
            border_color="#D8E1EA",
        )
        font_regular = ("Microsoft YaHei UI", 12)
        font_small = ("Microsoft YaHei UI", 11)
        # 日期默认填今天，减少用户手动输入的次数。
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.amount_var = tk.StringVar()
        # 默认选中「支出」，因为日常记账中支出占绝大多数。
        self.amount_type_var = tk.StringVar(value="支出")
        self.note_var = tk.StringVar()

        # ---------- 卡片内的整体结构（需求 3.2） ----------
        # 第 0 行 = 标题行（标题 + 支出/收入切换 + 添加按钮），用 title_frame 承载；
        # 第 1 行 = 字段区（两行字段），用 master_frame 承载。
        # 两个容器都 grid 到 self 的第 0 列并 sticky="ew"，宽度因此完全相同，
        # 于是标题行最右侧的「添加」按钮与字段区最右侧的「备注框」右边缘严格对齐。
        # 【为什么要分成两层容器】标题文字的宽度和字段标签的宽度差得远（约 52px vs 22px），
        # 若把标题行塞进字段网格的第 0 列，网格会按最宽的那一项撑开该列，
        # 反而在「日期」标签和日期框之间凭空多出一段空隙。
        # 外面这一列必须是 weight=1，两个容器才能撑满卡片宽度。
        self.columnconfigure(0, weight=1)

        # ---------- 标题行（需求 3.2）：标题 | 支出/收入切换 | ⋯ | 添加 ----------
        # 列结构：0=标题（宽度由文字决定）｜1=切换按钮｜2=弹性空白｜3=添加按钮
        # 第 2 列是唯一的弹性列：它吃掉全部剩余宽度，把添加按钮顶到卡片最右侧，
        # 同时保证切换按钮始终紧跟在标题后面，不会跟着窗口一起往右漂。
        title_frame = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        title_frame.grid(row=0, column=0, padx=0, pady=(12, 8), sticky="ew")
        title_frame.columnconfigure(0, weight=0)
        title_frame.columnconfigure(1, weight=0)
        title_frame.columnconfigure(2, weight=1)
        title_frame.columnconfigure(3, weight=0)

        # 标题右侧 padx=0、切换按钮左侧 padx=16，两者之间正好 16px；
        # 若两边都写 16 会变成 32px，与「间距约 16px」的要求不符。
        ctk.CTkLabel(
            title_frame,
            text="添加记录",
            font=("Microsoft YaHei UI", 13, "bold"),
            text_color="#243447",
        ).grid(row=0, column=0, padx=(16, 0), pady=0, sticky="w")

        # 支出/收入切换按钮（需求 3.2）：只决定金额的正负号，不直接改写输入框里的数字。
        # 它只是从金额框旁边挪到了标题行。
        # width=100 必须配合 dynamic_resizing=False 才会生效：
        # CTkSegmentedButton 内部的每个分段按钮都是以 width=0 创建的，
        # 默认（dynamic_resizing=True）会让外层容器自动收缩到「文字宽度」，
        # 无论把 width 写成多少，实测都恒为 74px 左右；
        # 关掉自动收缩后，width=100 才会被完整尊重。
        self.amount_type_button = ctk.CTkSegmentedButton(
            title_frame,
            values=["支出", "收入"],
            variable=self.amount_type_var,
            # 候选在用户点标签区 ▼ 时由 tag_picker 现算；收支切换不影响共享标签池。
            # amount_type_var 由 CTkSegmentedButton 自己维护：用户点击走的是内部
            # set(value, from_button_callback=True)，那里会写回 self._variable。
            width=100,
            height=32,
            dynamic_resizing=False,
            corner_radius=8,
            font=("Microsoft YaHei UI", 10),
            selected_color="#2F80ED",
            selected_hover_color="#256AC4",
            # 浅色主题下必须显式指定未选中态的底色与文字色，否则「收入」二字会看不见。
            unselected_color="#C6D4DF",
            unselected_hover_color="#B7C7D4",
            text_color="#455A64",
        )
        # sticky="w"：按钮贴着自己那一列的左边缘，不随第 2 列变宽而漂移。
        self.amount_type_button.grid(
            row=0, column=1, padx=(16, 0), pady=0, sticky="w"
        )

        # 添加按钮（原来在第二行最右侧）：文字由「添加记录」缩为「添加」，
        # 因为标题已经占了「添加记录」四个字，按钮再重复一遍会显得啰嗦。
        # 宽度随之从 110px 缩到 88px，仍是「固定宽度」控件（所在列权重为 0）。
        # 右 padx=16 是卡片内边距，与字段区（master_frame 的右 padx）保持一致，
        # 所以按钮右边缘与备注框右边缘落在同一条竖线上。
        # command=add_callback 与原来完全一致。
        ctk.CTkButton(
            title_frame,
            text="添加",
            command=add_callback,
            width=88,
            height=36,
            corner_radius=10,
            fg_color="#2F80ED",
            hover_color="#256AC4",
            font=font_small,
        ).grid(row=0, column=3, padx=(0, 16), pady=0, sticky="e")

        # ---------- 字段区：按比例自适应的三行布局（需求 3.2；第三行为 #58 Step 3a 新增）----------
        # 【三条设计意图】
        #   1. 输入框按比例自适应：日期框、金额框、标签框、备注框都不写死 width，
        #      而是靠 grid 的列权重（weight）分配宽度。窗口拉宽时一起变宽、拉窄时
        #      一起变窄，任何宽度下都填满可用空间，卡片右侧不会留下大片空白。
        #   2. 只有图标型控件固定宽度：▼ 按钮 36px（日期、标签各一个）。它只有一个
        #      字符，跟着伸缩只会变形或拉得很空洞。（支出/收入切换按钮与添加按钮已挪到标题行。）
        #   3. 三行共用同一套列网格：第 0 行（日期 | 金额）分占第 1 / 第 3 列，
        #      标签区与备注跨满第 1~3 列，所以各行的左右边缘天然落在同一批竖线上。
        # 【为什么中间要有一层 master_frame】多行共用的列必须落在同一个容器里才可能
        #   对齐；master_frame 用 sticky="ew" 撑满 InputFrame，成为各行共享的列网格。
        # 【权重分配】第 1 列 : 第 3 列 = 15 : 25，对应「日期约占 15%、金额约占 25%」。
        #   注意权重分配的是「固定部分（两个标签列、一个 ▼ 按钮）之外剩余的空间」，
        #   所以窗口越宽，各输入框的实际占比会比 15/25 略有放大；但两者的比例关系
        #   始终保持不变，这正是「按比例自适应」的预期行为。
        #   跨列的行（标签、备注）不受权重影响：权重只决定各行内部「列与列」怎么分宽度。
        # 【窄窗口下的安全性】窗口最小尺寸是 820x560（见 ui.py），卡片内部至少 772px，
        #   而本布局各控件的自然宽度合计远小于此，始终留有余量，所以任何被允许的
        #   窗口宽度下字段都完整可见、不会重叠，也不会被压缩到看不全。
        # 【列结构】
        #   第 0 列 = 标签（日期 / 标签 / 备注）—— 权重 0，宽度只由文字决定
        #   第 1 列 = 日期框组（日期框 + ▼）—— weight=15，按比例伸缩
        #   第 2 列 = 标签（金额）—— 权重 0，宽度只由文字决定
        #   第 3 列 = 金额框 —— weight=25，按比例伸缩
        # 行与行之间留 10px 垂直间距：上行下边距 5px + 下行上边距 5px。
        # 【卡片高度不用手工算】Tk 的几何传播是默认开启的（全仓唯一的 pack_propagate(False)
        #   在 ui.py 的 header 上），master_frame 会按各行子控件的需求高度自动长高，
        #   chips 换成两行卡片就自动多出一行的高度；这里刻意不维护任何「卡片高度常量」，
        #   以免它与实际布局对不上。
        master_frame = ctk.CTkFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
        )
        # 右 padx=16：添加按钮已搬到标题行，字段区不再有「按钮列」来提供这圈内边距，
        # 所以改由 master_frame 自己留白；否则备注框、金额框会顶死在卡片右边框上。
        # 留 16px 后，字段区右边缘与标题行添加按钮的右边缘严格对齐。
        master_frame.grid(row=1, column=0, padx=(0, 16), pady=(0, 14), sticky="ew")
        # 只给两个「输入列」权重：它们会吃掉全部剩余宽度，所以卡片右侧不会留白。
        # 两个标签列的权重保持 0，宽度只由内容决定，各行才能始终左对齐。
        master_frame.columnconfigure(1, weight=15)
        master_frame.columnconfigure(3, weight=25)

        # ---------- 第一行左侧：日期（输入框 + ▼ 日历按钮） ----------
        ctk.CTkLabel(
            master_frame,
            text="日期",
            font=font_small,
            text_color="#455A64",
        ).grid(row=0, column=0, padx=(16, 6), pady=(0, 5), sticky="w")

        # 输入框与 ▼ 按钮放进透明容器水平排布；若各自 grid 到不同列，
        # 会把整行高度撑高并让标签错位。
        date_frame = ctk.CTkFrame(
            master_frame,
            fg_color="transparent",
            corner_radius=0,
        )
        # sticky="ew"：日期框组随第 1 列的权重一起伸缩，两端贴住列边缘，
        # 于是它和第二行的类别框左、右边缘同时对齐。
        # 右侧不留 padx：与金额区的间距统一由「金额」标签的左侧 padx 控制，
        # 避免两处同时加空隙后实际间距翻倍。
        date_frame.grid(row=0, column=1, padx=(0, 0), pady=(0, 5), sticky="ew")
        # 日期框可以放心使用 textvariable：它没有 placeholder_text，不存在占位符失效问题。
        # 这里不写 width：宽度交给列权重决定，窗口拉宽时日期框跟着变宽。
        # （▼ 按钮仍固定 36px，见下方。）
        self.date_entry = ctk.CTkEntry(
            date_frame,
            height=36,
            font=font_regular,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#F8FAFC",
            textvariable=self.date_var,
        )
        # 按钮文字用「▼」而不是 emoji 📅，理由有三条，按重要性排：
        #   1) 字形来源：「▼」(U+25BC) 是 Microsoft YaHei UI **自带**字形；
        #      📅 在该字体里没有字形，靠 Windows 的字体回退（Segoe UI Emoji）
        #      才显示得出来。主流 Windows 上实测能正常显示，但它确实比 ▼ 多绕了
        #      一层，跨环境一致性差一些。
        #   2) 视觉重量：📅 是彩色 emoji，在一排灰蓝色线框按钮里显得突兀。
        #   3) 宽度：emoji 的字宽随字体版本变，▼ 是稳定的单字形，按钮宽度好算。
        # 先 pack 按钮并 side="right"：让它钉在容器右端不被挤压；
        # 再 pack 输入框并 expand=True 占满剩余宽度，两者高度都是 36 保持齐平。
        ctk.CTkButton(
            date_frame,
            text="▼",
            command=self._pick_date,
            width=36,
            height=36,
            corner_radius=9,
            fg_color="#E3EAF2",
            hover_color="#D2DEE9",
            text_color="#243447",
            font=("Microsoft YaHei UI", 11),
        ).pack(side="right", padx=(6, 0))
        # expand=True + fill="both"：输入框吃掉容器里 ▼ 按钮之外的全部宽度，
        # 窗口变宽时新增的宽度全部落在日期框上（▼ 按钮宽度始终保持 36px）。
        self.date_entry.pack(side="left", fill="both", expand=True)

        # ---------- 第一行右侧：金额（只有输入框） ----------
        ctk.CTkLabel(
            master_frame,
            text="金额",
            font=font_small,
            text_color="#455A64",
        # 左侧 padx=12 + 标签宽 22 + 右侧 padx=6，恰好让日期组合与金额组合
        # 之间留出约 40px 水平间距（总计 12+22+6=40），避免两个组合粘在一起。
        ).grid(row=0, column=2, padx=(12, 6), pady=(0, 5), sticky="w")

        # 金额字段占第 3 列并填满整列（sticky="ew"）。
        # 这里不再需要透明容器：支出/收入切换按钮已经挪到标题行，金额框是这一列里
        # 唯一的控件，直接 grid 即可；去掉容器也顺带避开了「容器 + 内部 pack」
        # 两层布局叠在一起带来的行高偏差。
        # 腾出来的约 100px（原切换按钮宽度）全部归金额框，所以金额框变宽了，
        # 并且与第二行的备注框等宽、左右边缘都对齐。
        # 不写 width：宽度完全由第 3 列的权重决定，窗口拉宽时金额框同步变宽。
        # 右 padx 保持 0：间距统一由「金额」标签的左侧 padx 控制，
        # 否则两处同时加空隙实际间距会翻倍。
        # 金额输入框刻意不用 textvariable，而是用 placeholder_text 显示占位提示；
        # 代价是取用户输入时必须读控件本身（amount_entry.get()），不能读 StringVar。
        # 也正因为如此，这里不绑定 <KeyRelease> 去回写 StringVar：粘贴（尤其右键粘贴）
        # 只发送 <<Paste>> 之类的虚拟事件、不产生按键事件，靠按键回写必然漏掉粘贴内容。
        self.amount_entry = ctk.CTkEntry(
            master_frame,
            height=36,
            font=font_regular,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#F8FAFC",
            placeholder_text=" 请输入金额 ",
        )
        self.amount_entry.grid(row=0, column=3, padx=(0, 0), pady=(0, 5), sticky="ew")

        # ---------- 第二行：标签区（chips + 输入框 + ▼，需求 3.14.3 形态 1） ----------
        # 这一行内部再分两小行装在 tags_frame 里：第 0 行 = 输入框 + ▼，第 1 行 = chips。
        # 【为什么输入行在 chips 之上】chips 会随标签增多而换行。若把它排在输入框上面，
        #   每加一个标签就把正在输入的那个框往下顶一次、光标跟着跳；放在下面则输入框
        #   位置恒定，只是卡片在长高。
        # 【为什么标签区跨满第 1~3 列】chips 是流式换行的，给它最大宽度才能少换行；
        #   而且「这条记录属于哪些标签」与金额、备注并列整行也更符合阅读顺序。
        # height=36 + anchor="w" 让「标签」二字与输入框的垂直中心对齐：默认（按整行居中）
        #   时，标签区一旦因 chips 换行变高，二字就会漂到两行之间、看着像属于 chips 那一行。
        ctk.CTkLabel(
            master_frame,
            text="标签",
            font=font_small,
            text_color="#455A64",
            height=36,
            anchor="w",
        ).grid(row=1, column=0, padx=(16, 6), pady=(5, 0), sticky="nw")

        self.tag_chips = TagChipsFrame(master_frame)
        # 标签区仍占原来的网格位置；跨列后与日期、金额、备注的右边缘保持对齐。
        self.tag_chips.grid(
            row=1, column=1, columnspan=3, padx=0, pady=(5, 0), sticky="ew"
        )
        # 兼容既有调用与探针：变量和控件仍是同一对象，不复制状态。
        self.tag_input_var = self.tag_chips.tag_input_var
        self.chips_frame = self.tag_chips.chips_frame
        self.tag_button = self.tag_chips.tag_button

        # ---------- 第三行：备注 ----------
        # 标签区（chips 会换行）占了第二行整行，备注不能再和它并排，因此下移到第三行；
        # 从第 1 列起跨满 3 列（columnspan=3），右端仍落在 master_frame 的右内边距上，
        # 与金额框、标签框右边缘对齐。
        ctk.CTkLabel(
            master_frame,
            text="备注",
            font=font_small,
            text_color="#455A64",
        ).grid(row=2, column=0, padx=(16, 6), pady=(5, 0), sticky="w")

        # 不写 width：宽度完全由列权重（跨列时即整段可用宽度）决定，窗口拉宽时同步变宽。
        note_entry = ctk.CTkEntry(
            master_frame,
            height=36,
            font=font_regular,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#F8FAFC",
            textvariable=self.note_var,
        )
        # 右侧不留 padx：右边缘的留白统一由 master_frame 自己的右 padx=(0, 16) 提供，
        # 这样备注框右边缘才能与标题行的添加按钮右边缘对齐。
        note_entry.grid(
            row=2, column=1, columnspan=3, padx=(0, 0), pady=(5, 0), sticky="ew"
        )

    def _pick_date(self) -> None:
        """打开日历选择器，把选中的日期回填到日期输入框（需求 3.12）。"""
        # 用 winfo_toplevel() 而不是 self：弹窗必须挂在主窗口上，
        # 否则 transient 会认错父窗口，导致弹窗跑到主窗口下面或被最小化时一起消失。
        # anchor 传日期输入框本体：日历会贴在它的左下角弹出（与类别 ▼ 一致），
        # 而不是摆到屏幕中心——用户从哪一行点的 ▼，弹窗就出现在哪一行旁边。
        picked = ask_date(
            self.winfo_toplevel(),
            self.date_var.get().strip(),
            anchor=self.date_entry,
        )
        # 返回 None 表示用户取消/按 ESC，此时保持输入框原值不变。
        if picked:
            self.date_var.set(picked)

    def get_tags(self) -> tuple[str, ...]:
        """转发读取 chips，保留新增区既有调用入口。"""
        return self.tag_chips.get_tags()

    def collect_tags(self) -> tuple[str, ...]:
        """转发收集操作，提交前仍会冲刷输入框残留。"""
        return self.tag_chips.collect_tags()

    def clear_tags(self) -> None:
        """转发清空操作，新增记录成功后复位所有标签状态。"""
        self.tag_chips.clear_tags()


class ToolbarFrame(ctk.CTkFrame):
    """Search box and the responsive action-button bar."""

    def __init__(
        self,
        master: ctk.CTk,
        filter_callback: Callable[..., None],
        refresh_callback: Callable[[], None],
        delete_callback: Callable[[], None],
        stats_callback: Callable[[], None],
        export_callback: Callable[[], None],
        chart_callback: Callable[[], None],
        open_folder_callback: Callable[[], None],
    ) -> None:
        super().__init__(master, fg_color="transparent")
        font_small = ("Microsoft YaHei UI", 11)
        # 所有工具按钮共用同一套尺寸参数，保证视觉高度完全一致。
        # 这里的 width 是「最小宽度」而不是固定宽度：外层 grid 的列权重会在窗口变宽时
        # 把按钮一起拉宽（见下方 sticky="ew"）。取 110 是因为最长文字「删除选中记录」
        # 实际只需约 81px（文字 68.8 + 两侧内边距 12），110 留有余量又不至于夸张；
        # 关键在于六个按钮取同一个值，六列的「最小宽度」才相同，grid 才能把它们算成等宽。
        button_config = {
            "width": 110,
            "height": 34,
            "corner_radius": 9,
            "font": font_small,
        }
        self.search_var = tk.StringVar()
        # 需求 3.9 要求保留变量监听：任何对 search_var 的赋值都会立刻刷新列表；
        # 用户实际输入时则由输入框上的事件直接调用 filter_callback（见下方绑定）。
        self.search_var.trace_add("write", filter_callback)
        # 与金额输入框同理：为了使用原生 placeholder_text（需求 3.9）而放弃 textvariable，
        # 所以不靠 StringVar 回写内容，筛选时统一直接读 search_entry.get()。
        self.search_entry = ctk.CTkEntry(
            self,
            width=200,
            height=34,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#FFFFFF",
            font=font_small,
            placeholder_text="🔍 搜索日期/标签/备注",
        )
        # 用 grid 而不是 pack(side="left")：pack 是「从左往右按各自固定宽度依次占位」，
        # 它不会在窗口变窄时让步，最右边的按钮会被挤出可视区域。
        # grid 用「列权重」表达意图：第 0 列（搜索框）权重 0、宽度固定 200px 不参与伸缩；
        # 第 1~6 列（六个按钮）权重都是 1 且同属一个 uniform 组
        #   → 剩下多少宽度就由六列严格均分：窗口拉宽按钮变宽，拉窄按钮变窄。
        # uniform 是一道额外保险：它强制同组列宽相等，即使将来某个按钮因文字变长
        # 而抬高自己的最小宽度，六列也仍会取同一个宽度，不会出现参差不齐。
        self.columnconfigure(0, weight=0)
        for column_index in range(1, 7):
            self.columnconfigure(column_index, weight=1, uniform="toolbar_button")
        # 搜索框 sticky="w"：它所在列没有权重，宽度就固定 200px，不会被拉伸。
        self.search_entry.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="w")
        # 实时筛选的触发源必须挂在输入框上：没有 textvariable 就没有变量可监听，
        # 逐字符过滤只能由输入事件驱动。
        # 键盘输入用 <KeyRelease>；粘贴/剪切/清空走的是 <<Paste>>/<<Cut>>/<<Clear>> 虚拟事件
        # （右键粘贴尤其不会产生按键事件），漏掉它们就会出现"粘了字但列表不刷新"。
        # 虚拟事件的控件级回调先于 Tk 的类绑定执行，此刻文本还没插进输入框，
        # 因此必须 after_idle 延后到插入完成之后再读，否则过滤用的还是旧内容。
        self.search_entry.bind("<KeyRelease>", filter_callback, add="+")
        for sequence in ("<<Paste>>", "<<PasteSelection>>", "<<Cut>>", "<<Clear>>"):
            self.search_entry.bind(
                sequence,
                lambda _event: self.after_idle(filter_callback),
                add="+",
            )
        # 工具按钮按「轻-重」顺序从左到右排列，删除这类破坏性操作用红色以示警示。
        # 六个按钮共用同一套 grid 参数：sticky="ew" 让按钮撑满所在列（列有多宽按钮就多宽）；
        # 右内边距 8px 就是需求里「按钮之间的 8px 间距」。
        # 最后一个按钮也留这 8px：六列的「最小宽度」必须完全一致，少了这 8px 的
        # 那个列会比其他列宽出 8px，整体就不再等宽（这 8px 落在工具栏最右侧，
        # 与卡片左内边距作用相同，视觉上看不出来）。
        button_grid = {"row": 0, "padx": (0, 8), "pady": 0, "sticky": "ew"}
        ctk.CTkButton(
            self,
            text="刷新",
            command=refresh_callback,
            fg_color="#607D8B",
            hover_color="#4F6873",
            **button_config,
        ).grid(column=1, **button_grid)
        ctk.CTkButton(
            self,
            text="删除选中记录",
            command=delete_callback,
            fg_color="#E76F51",
            hover_color="#C9573D",
            **button_config,
        ).grid(column=2, **button_grid)
        ctk.CTkButton(
            self,
            text="月度统计",
            command=stats_callback,
            fg_color="#5B8E7D",
            hover_color="#477564",
            **button_config,
        ).grid(column=3, **button_grid)
        ctk.CTkButton(
            self,
            text="导出为CSV",
            command=export_callback,
            fg_color="#7B6D8D",
            hover_color="#635775",
            **button_config,
        ).grid(column=4, **button_grid)
        ctk.CTkButton(
            self,
            text="查看图表",
            command=chart_callback,
            fg_color="#4A90D9",
            hover_color="#3679BA",
            **button_config,
        ).grid(column=5, **button_grid)
        # 按钮文字始终在按钮内居中（CTkButton 内部就是居中放置文本标签），
        # 所以按钮变宽变窄都不会让文字跑偏，也不会截断。
        ctk.CTkButton(
            self,
            text="打开数据目录",
            command=open_folder_callback,
            fg_color="#607D8B",
            hover_color="#4F6873",
            **button_config,
        ).grid(column=6, **button_grid)


class RecordTableFrame(ctk.CTkFrame):
    """Record table and its scrollbar."""

    def __init__(self, master: ctk.CTk, edit_callback: Callable[[tk.Event], None]) -> None:
        super().__init__(
            master,
            corner_radius=14,
            fg_color="#FFFFFF",
            border_width=1,
            border_color="#D8E1EA",
        )
        font_small = ("Microsoft YaHei UI", 11)
        table_inner = ctk.CTkFrame(self, fg_color="transparent")
        table_inner.pack(fill="both", expand=True, padx=10, pady=10)
        # 列 key 用 tags 而不是 category（#62）：这一列现在装的是多值标签串，
        # key 名与类型对齐后，以后任何人看到 "tags" 都不会再误以为它是单值类别。
        # 注意 key 与表头文字是两回事，而 values / iid 都是按位置给的，不受改名影响。
        columns = ("id", "date", "amount", "tags", "note")
        # 自定义 ttk 样式，是为了摆脱 Windows 原生 Treeview 的灰色边框和紧凑行高，
        # 让表格与浅色圆角卡片风格保持一致。
        style = ttk.Style(self)
        # borderwidth=0 用于去掉原生外观自带的立体边框；rowheight 加高让行更好点选。
        style.configure(
            "Account.Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#263238",
            rowheight=34,
            font=font_small,
            borderwidth=0,
        )
        style.configure(
            "Account.Treeview.Heading",
            background="#E8F0F6",
            foreground="#37474F",
            font=("Microsoft YaHei UI", 11, "bold"),
            relief="flat",
        )
        # 原生选中色是深蓝，与浅色主题冲突，这里改成浅蓝以保持整体协调。
        style.map("Account.Treeview", background=[("selected", "#D7E9FC")])
        self.tree = ttk.Treeview(
            table_inner,
            columns=columns,
            show="headings",
            style="Account.Treeview",
        )
        # show="headings" 表示隐藏默认的首列树形图标，只显示自定义表头。
        headings = (
            ("id", "ID", 70),
            ("date", "日期", 120),
            ("amount", "金额", 120),
            # 表头文字仍写「标签」（§3.14.4：表格单元格用「、」连接，0 个时显示「—」）。
            ("tags", "标签", 140),
            ("note", "备注", 300),
        )
        for column, title, width in headings:
            self.tree.heading(column, text=title)
            self.tree.column(
                column,
                # 备注列内容较长，左对齐更易读；其余列都是短内容，居中对齐更整齐。
                width=width,
                anchor="center" if column != "note" else "w",
            )
        scrollbar = ttk.Scrollbar(
            table_inner,
            orient="vertical",
            command=self.tree.yview,
        )
        # 双向绑定：拖动滚动条能滚动表格，鼠标滚轮滚动表格时滚动条位置也会同步更新。
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        # 双击某一行即触发编辑回调（需求 3.5.1），比额外放一个「编辑」按钮更直观。
        self.tree.bind("<Double-1>", edit_callback)
        # 只让表格所在的第 0 行/列获得伸缩权重，保证窗口拉大时表格占满剩余空间。
        table_inner.rowconfigure(0, weight=1)
        table_inner.columnconfigure(0, weight=1)
