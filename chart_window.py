"""财务分析窗口：完整缓存快照交工作线程聚合，TkAgg 在主线程内嵌绘制。"""

from __future__ import annotations

import tkinter as tk
import sys
from collections.abc import Callable, Iterable
from datetime import date
from queue import Empty, Queue
from threading import Event, Thread

import customtkinter as ctk

from dialog_lifecycle import ManagedToplevel, _cancel_owned_tasks
from config import RESOURCE_DIR
from file_picker import FileMenu
from window_icon import apply_window_icon_frames
from store import Account
from tag_aggregation import MonthStats, StatsRecord, build_month_stats, describe_change, offset_month


BG = "#F0F5FC"
INK = "#17243B"
MUTED = "#687992"
BLUE = "#287CF5"
GREEN = "#18A56C"
RED = "#DC5359"
BORDER = "#DCE6F3"

def _put_latest(queue, value):
    try:
        queue.get_nowait()
    except Empty:
        pass  # 队列为空是正常情况；仅丢弃尚未处理的旧请求或旧结果。
    queue.put_nowait(value)


class StatsWindow(ManagedToplevel):
    """独立的 CTk 窗口，TkAgg 的创建、绘制和销毁均由主线程执行。"""

    def __init__(self, master: tk.Misc, records_provider: Callable[[], Iterable[Account]]) -> None:
        super().__init__(master, fg_color=BG)
        self.title("财务分析")
        icon_path = RESOURCE_DIR / "image" / "app_icon.ico"
        if icon_path.exists():
            try:
                # 显式设置可阻止 CTk 的延迟默认图标覆盖；原生尺寸修正在窗口就绪后执行。
                self.iconbitmap(str(icon_path))
                if sys.platform == "win32":
                    self.after_idle(lambda: apply_window_icon_frames(self, icon_path))
            except tk.TclError as error:
                # 图标失败保留窗口功能，同时输出可定位的诊断，不把装饰性错误升级为崩溃。
                print(f"财务分析图标加载失败：{error}")
        self.geometry("1120x800")
        self.minsize(960, 700)
        self.month = date.today().strftime("%Y-%m")
        self.records_provider = records_provider
        self.data: MonthStats | None = None
        self.error = ""
        self.generation = 0
        self.tag_page = 0
        self.month_choices = ()
        self.jobs, self.results = Queue(maxsize=1), Queue(maxsize=1)
        self.stop = Event()
        self.poll_job = None
        self.worker = Thread(target=self._work, name="financial-stats", daemon=True)
        self.direction = "支出"
        self.canvas = None
        self.figure = None
        self.render_job = None
        self.revision = 0
        self.card_values = []
        self.card_changes = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_header()
        self._build_cards()
        self._build_insight()
        self._build_tabs()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.finish_setup()
        if not self.closing:
            self.worker.start()
            self.poll_job = self.after(40, self._poll_results)
            self.refresh()

    def _label(self, parent, text, size=13, color=INK, bold=False, **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color,
                            font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
                            **kwargs)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 26))
        header.grid_columnconfigure(0, weight=1)
        self._label(header, "财务分析", 29, bold=True, anchor="w").grid(row=0, column=0, sticky="w")
        self._label(header, "每一笔收支，清晰可见。", 13, MUTED).grid(row=1, column=0, sticky="w", pady=(4, 0))
        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=1, rowspan=2)
        # 放大箭头与点击区域，避免月份切换入口过小而难以辨认或点击。
        for text, offset in (("‹", -1), ("›", 1)):
            button = ctk.CTkButton(controls, text=text, width=36, height=36,
                                   fg_color="#E8EEF5", text_color="#334155", hover_color="#DDE7F2",
                                   border_width=0, corner_radius=8, font=("Microsoft YaHei UI", 22, "bold"),
                                   command=lambda step=offset: self.select_month(offset_month(self.month, step)))
            if offset == -1:
                button.pack(side="left")
                self.month_picker = ctk.StringVar(master=self, value=self.month)
                self.month_holder = ctk.CTkFrame(controls, fg_color="transparent")
                self.month_holder.pack(side="left", padx=6)
                self._sync_month_choices((self.month,))
            else:
                button.pack(side="left")
        ctk.CTkButton(controls, text="↻  刷新", command=self.refresh, width=88, height=38,
                      fg_color=BLUE, hover_color="#1769DB", font=("Microsoft YaHei UI", 13),
                      corner_radius=9).pack(side="left", padx=(14, 0))

    def _sync_month_choices(self, months):
        choices = tuple(sorted(set(months) | {self.month, date.today().strftime("%Y-%m")}, reverse=True))
        if choices == self.month_choices:
            return
        if self.month_choices:
            self.month_menu.destroy()  # 复用菜单的销毁协议，先清理旧展开会话和绑定。
        self.month_choices = choices
        self.month_menu = FileMenu(self.month_holder, choices, self.select_month)
        # 入口只负责布局，避免父层底色叠在子按钮圆角下形成不等宽的上下色带。
        self.month_menu.configure(width=146, height=36, corner_radius=0, fg_color="transparent")
        self.month_menu.grid_rowconfigure(0, weight=1)
        self.month_menu.label_button.configure(
            textvariable=self.month_picker, fg_color="#E8EEF5", hover_color="#DDE7F2",
            bg_color=BG, height=36, corner_radius=8,
            text_color="#475569", font=("Microsoft YaHei UI", 13))
        self.month_menu.toggle_button.configure(
            fg_color="#DDE7F2", hover_color="#D2DEE9", text_color="#475569",
            bg_color=BG, height=36, corner_radius=8)
        # 两个按钮铺满同一行，不保留共享菜单的一像素内衬，缩放取整也不会累积到底边。
        self.month_menu.label_button.grid(padx=0, pady=0, sticky="nsew")
        self.month_menu.toggle_button.grid(padx=0, pady=0, sticky="nsew")
        self.month_menu.pack()

    def _build_cards(self):
        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", padx=28)
        titles = ("总收入", "总支出", "总结余", "日均支出")
        colors = (GREEN, "#E86C39", BLUE, INK)
        icons = ("↑", "↓", "=", "日")
        # 等宽卡片共同伸缩，金额字号和留白保持一致。
        for index, (title, color, icon) in enumerate(zip(titles, colors, icons)):
            cards.grid_columnconfigure(index, weight=1, uniform="metric")
            card = ctk.CTkFrame(cards, fg_color="white", corner_radius=13,
                                border_width=1, border_color=BORDER)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 7, 0 if index == 3 else 7))
            self._label(card, f"{icon}   {title}", 14, MUTED).pack(anchor="w", padx=19, pady=(17, 6))
            value = self._label(card, "", 26, color, True)
            value.pack(anchor="w", padx=19)
            change = self._label(card, "", 12)
            change.pack(anchor="w", padx=19, pady=(7, 17))
            self.card_values.append(value)
            self.card_changes.append(change)

    def _build_insight(self):
        # 极值使用窄信息条，不与四张核心卡片争夺视觉层级。
        insight = ctk.CTkFrame(self, fg_color="#E7F0FE", corner_radius=9)
        insight.grid(row=2, column=0, sticky="ew", padx=28, pady=(20, 20))
        self.insight = self._label(insight, "", 13, "#3C557A", anchor="w", justify="left", wraplength=900)
        self.insight.pack(fill="x", padx=18, pady=12)

    def _build_tabs(self):
        # 页签背景与窗口一致，白色卡片放在页内，避免导航跨白色上边框形成拼接。
        self.tabs = ctk.CTkTabview(
            self, fg_color=BG, border_width=0, corner_radius=13,
            anchor="nw", command=self.request_render,
            segmented_button_fg_color="#EAF1FB", segmented_button_unselected_color="#EAF1FB",
            segmented_button_unselected_hover_color="#DFEAFB",
            segmented_button_selected_color=BLUE, segmented_button_selected_hover_color=BLUE,
            segmented_button_font=("Microsoft YaHei UI", 14),
            text_color=INK, text_color_disabled=MUTED)
        self.tabs.grid(row=3, column=0, sticky="nsew", padx=28, pady=(4, 0))
        self.chart_hosts = {}
        self.page_titles = {}
        for name in ("收支标签", "每日趋势", "近半年"):
            page = self.tabs.add(name)
            page.grid_columnconfigure(0, weight=1)
            page.grid_rowconfigure(0, weight=1)
            # 卡片与导航留出独立间距；三页仍共享一张画布，切换时释放旧资源。
            card = ctk.CTkFrame(page, fg_color="white", border_width=1,
                                border_color=BORDER, corner_radius=13)
            card.grid(row=0, column=0, sticky="nsew")
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(1, weight=1)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.grid(row=0, column=0, sticky="ew", padx=22, pady=(16, 8))
            # 标题和切换器组成一组，剩余宽度留在右侧，不让按钮悬在曲线上方。
            top.grid_columnconfigure(2, weight=1)
            title = self._label(top, "", 18, bold=True)
            title.grid(row=0, column=0, sticky="w")
            self.page_titles[name] = title
            if name == "收支标签":
                # 固定双色底和无描边样式，避免继承默认灰边；等宽选项留出文字两侧空隙。
                self.toggle = ctk.CTkSegmentedButton(
                    top, values=["支出", "收入"], command=self.select_direction,
                    fg_color="#EFF4FC", bg_color="white", border_width=0, corner_radius=8,
                    width=144, dynamic_resizing=False,
                    selected_color=BLUE, selected_hover_color=BLUE, text_color=INK,
                    unselected_color="#EFF4FC", unselected_hover_color="#E4EEFD", height=34,
                    font=("Microsoft YaHei UI", 13))
                self.toggle.set(self.direction)
                self.toggle.grid(row=0, column=1, padx=(22, 0))
                # 真账本标签数量不固定，每页六项，全部标签都可查看而不挤压文字。
                pager = ctk.CTkFrame(top, fg_color="transparent")
                pager.grid(row=0, column=3)
                self.prev_tags = ctk.CTkButton(pager, text="‹", width=36, height=36,
                                              font=("Microsoft YaHei UI", 22, "bold"),
                                              state="disabled", fg_color="#F1F5F9", text_color_disabled="#94A3B8",
                                              command=lambda: self._turn_tags(-1))
                self.prev_tags.pack(side="left")
                self.tag_page_label = self._label(pager, "1/1", 13, "#334155", bold=True, width=48)
                self.tag_page_label.pack(side="left")
                self.next_tags = ctk.CTkButton(pager, text="›", width=36, height=36,
                                              font=("Microsoft YaHei UI", 22, "bold"),
                                              state="disabled", fg_color="#F1F5F9", text_color_disabled="#94A3B8",
                                              command=lambda: self._turn_tags(1))
                self.next_tags.pack(side="left")
            host = ctk.CTkFrame(card, fg_color="white", corner_radius=0)
            host.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 12))
            self.chart_hosts[name] = host
        # 备注单独留出内外边距，长句可换行，避免贴底并挤入图表区域。
        note_box = ctk.CTkFrame(self, fg_color="#E8EFF9", corner_radius=9)
        note_box.grid(row=4, column=0, sticky="ew", padx=28, pady=(14, 24))
        self.note = self._label(note_box, "", 12, "#728198", anchor="w",
                                justify="left", wraplength=900)
        self.note.pack(fill="x", padx=18, pady=(10, 12))

    def select_month(self, month: str):
        self.month = month
        self.tag_page = 0
        self.month_picker.set(month)
        self.refresh()

    def select_direction(self, direction: str):
        self.direction = direction
        self.tag_page = 0
        self.request_render()

    def refresh(self):
        if self.closing:
            return
        self.generation += 1
        self.data, self.error = None, ""
        for value, change in zip(self.card_values, self.card_changes):
            value.configure(text="—")
            change.configure(text="正在统计", text_color=MUTED)
        self.insight.configure(text="正在读取账本缓存…")
        try:
            # Account 可变，必须复制字段，后台不能读取共享对象或跨线程调用 store。
            snapshot = tuple(StatsRecord(row.record_id, row.record_date, row.amount, tuple(row.tags))
                             for row in self.records_provider())
            _put_latest(self.jobs, (self.generation, snapshot, self.month, date.today()))
        except Exception as error:
            # 明确显示取数失败，不能继续显示上一次金额冒充当前账本。
            self._set_error(f"账本读取失败：{error}")
        self.request_render()

    def _set_error(self, message):
        self.error = message
        self.insight.configure(text=message)
        for value, change in zip(self.card_values, self.card_changes):
            value.configure(text="—")
            change.configure(text="统计失败，请刷新重试", text_color=RED)
        print(message)  # 可见失败与诊断同时保留，不把旧结果或零金额伪装成成功。

    def _work(self):
        while not self.stop.is_set():
            job = self.jobs.get()
            if job is None:
                return
            generation, records, month, today = job
            try:
                result = build_month_stats(records, month, today)
            except Exception as error:
                # 线程中的异常必须回传为可见错误，不能使界面永远停在加载中。
                result = error
            if self.stop.is_set():
                return
            _put_latest(self.results, (generation, result))

    def _poll_results(self):
        self.poll_job = None
        if self.closing:
            return
        try:
            generation, result = self.results.get_nowait()
        except Empty:
            result = None  # 尚未完成时保留加载状态，下一轮再取，不阻塞 GUI。
        # 切月或刷新后只接收当前请求，旧线程结果不能回填到新月份。
        if result is not None and generation == self.generation:
            if isinstance(result, Exception):
                self._set_error(f"统计失败：{result}")
            else:
                self.data = result
                self._update_cards()
                self._sync_month_choices(result.months)
            self.request_render()
        self.poll_job = self.after(40, self._poll_results)

    def _update_cards(self):
        data = self.data
        values = (data.totals.total_income, data.totals.total_expense,
                  data.totals.total_income - data.totals.total_expense, data.average)
        previous = (data.previous.total_income, data.previous.total_expense,
                    data.previous.total_income - data.previous.total_expense, data.previous_average)
        for value, before, label, change in zip(values, previous, self.card_values, self.card_changes):
            label.configure(text=f"¥ {value:,.2f}" if value is not None else "—")
            text, direction = describe_change(value, before)
            change.configure(text=f"较上月 {text}", text_color={1: RED, -1: GREEN, 0: MUTED}[direction])
        row = data.largest
        if row is None:
            detail = "暂无支出记录"
        else:
            day = date.fromisoformat(row.record_date)
            tags = "、".join(row.tags) if row.tags else "未分类"
            detail = f"{day.month:02d}月{day.day:02d}日  ·  {tags}  ·  ¥{abs(row.amount):,.2f}"
        self.insight.configure(text="当月最大单笔支出：" + detail)

    def _tag_rows(self):
        if self.data is None:
            return []
        rows = [(tag, total.expense if self.direction == "支出" else total.income)
                for tag, total in self.data.totals.by_tag.items()]
        # 零额桶不占用图表页数，同额稳定排序使连续刷新不会改变显示顺序。
        return sorted((row for row in rows if row[1] > 0), key=lambda row: (-row[1], row[0]))

    def _turn_tags(self, step):
        self.tag_page = max(0, self.tag_page + step)
        self.request_render()

    def request_render(self):
        if self.closing:
            return
        self.revision += 1
        if self.render_job is not None:
            self.after_cancel(self.render_job)
        # 仅合并连续操作，不把 after 宣称为后台绘图；TkAgg 严格在主线程运行。
        revision = self.revision
        self.render_job = self.after(30, lambda: self._render(revision))

    def _clear_chart(self):
        if self.canvas is not None:
            widget = self.canvas.get_tk_widget()
            # 主动切页也先取消画布自己的 idle，避免 Tcl 在销毁后调用旧命令。
            _cancel_owned_tasks(widget)
            widget.destroy()
            self.canvas = None
        if self.figure is not None:
            self.figure.clear()
            self.figure = None
        for host in self.chart_hosts.values():
            for child in host.winfo_children():
                child.destroy()

    def _render(self, revision):
        self.render_job = None
        if self.closing or revision != self.revision:
            return
        self._clear_chart()
        page = self.tabs.get()
        host = self.chart_hosts[page]
        data = self.data
        self.page_titles[page].configure(text={"收支标签": f"{self.direction}标签",
                                             "每日趋势": "每日支出趋势", "近半年": "近半年总收支"}[page])
        rows = self._tag_rows()
        pages = max(1, (len(rows) + 5) // 6)
        self.tag_page = min(self.tag_page, pages - 1)
        self.tag_page_label.configure(text=f"{self.tag_page + 1}/{pages}")
        # 启禁用条件保持一致，底色也随状态变化，不能只让箭头文字变灰。
        for button, enabled in ((self.prev_tags, bool(self.tag_page)),
                                (self.next_tags, self.tag_page + 1 < pages)):
            button.configure(state="normal" if enabled else "disabled", corner_radius=8,
                             fg_color="#2563EB" if enabled else "#F1F5F9", hover_color="#1D4ED8",
                             text_color="#FFFFFF", text_color_disabled="#94A3B8", border_width=1,
                             border_color="#2563EB" if enabled else "#E2E8F0")
        notes = {
            "收支标签": "多标签记录分别计入各项，标签合计可能大于当月总额。未分类表示没有标签的记录。",
            "每日趋势": "日均支出 = 当月总支出 ÷ 当月已过天数（含今天）；历史月份按整月天数。",
            "近半年": "收入向上，支出向下；纵轴负值仅表示支出方向。",
        }
        note = notes[page]
        if data is not None and data.skipped_count:
            note += f" 已跳过 {data.skipped_count} 条无效日期或金额记录。"
        if data is not None and data.future_count:
            note += f" 所选月份包含 {data.future_count} 笔未来日期账目。"
        self.note.configure(text=note)
        message = self.error if self.error else "正在统计…"
        if data is not None:
            has_data = {"收支标签": bool(rows), "每日趋势": bool(data.record_count),
                        "近半年": any(income or expense for _, income, expense in data.half_year)}[page]
            message = "" if has_data else "暂无数据"
        if message:
            self._label(host, message, 20, MUTED, wraplength=800).place(relx=0.5, rely=0.5, anchor="center")
            return
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            from matplotlib.font_manager import FontProperties
            from matplotlib.ticker import MaxNLocator
            # TkAgg 按实际控件尺寸重绘，较高 DPI 让字号对应足够像素，而不是拉伸旧位图。
            self.figure = Figure(figsize=(8, 3.2), dpi=125, facecolor="white", layout="constrained")
            ax = self.figure.add_subplot(111)
            font = FontProperties(family="Microsoft YaHei", size=11)
            ax.set_axisbelow(True)
            ax.tick_params(axis="both", colors=MUTED, length=0, pad=10, labelsize=11)
            # 限制数值轴的刻度密度，缩小窗口时仍保留文字之间的呼吸空间。
            (ax.xaxis if page == "收支标签" else ax.yaxis).set_major_locator(MaxNLocator(nbins=5, prune="upper"))
            for edge in ("top", "right", "left"):
                ax.spines[edge].set_visible(False)
            ax.spines["bottom"].set_color(BORDER)
            if page == "收支标签":
                self._draw_tags(ax, rows[self.tag_page * 6:(self.tag_page + 1) * 6], font)
            elif page == "每日趋势":
                self._draw_daily(ax, data, font)
            else:
                self._draw_half_year(ax, font)
            # 字体仅作用于本图，不改全局 rcParams，避免污染其他窗口。
            for tick in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
                tick.set_fontproperties(font)
            # 按当前画布重算文字边界；固定比例在缩放后不足以容纳轴标题，容易裁掉字底。
            self.figure.get_layout_engine().set(w_pad=0.12, h_pad=0.12)
            self.canvas = FigureCanvasTkAgg(self.figure, master=host)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            self.canvas.draw_idle()
        except (ImportError, RuntimeError, ValueError, tk.TclError) as error:
            # 错误保留可见诊断和重试入口，不能留下黑框或静默吞掉异常。
            self._clear_chart()
            self._label(host, "图表暂时无法显示，请点击刷新重试", 17, MUTED).pack(expand=True)
            self.note.configure(text=f"图表加载失败：{error}")
            print(f"财务分析图表加载失败：{error}")

    def _draw_tags(self, ax, rows, font):
        names, amounts = zip(*rows)
        values = [float(amount) for amount in amounts]  # 仅渲染边界转 float，不参与业务汇总。
        color = BLUE if self.direction == "支出" else GREEN
        ax.barh(range(len(names)), values, height=0.55, color=color, alpha=0.86)
        ax.set_yticks(range(len(names)), names)
        ax.invert_yaxis()
        ax.set_xlim(0, max(values) * 1.18)
        ax.grid(axis="x", color=BORDER, linestyle=(0, (3, 3)), linewidth=0.7)
        ax.set_xlabel("金额（元）", fontproperties=font, color=MUTED, labelpad=4)
        for index, value in enumerate(values):
            ax.text(value + max(values) * 0.015, index, f"{value:,.2f}",
                    va="center", color=INK, fontproperties=font)

    def _draw_daily(self, ax, data, font):
        values = [float(amount) for amount in data.daily]
        days = range(1, len(values) + 1)
        ax.plot(days, values, color=BLUE, marker="o", markersize=3.5,
                linewidth=1.8, label="每日支出")
        ax.fill_between(days, values, color=BLUE, alpha=0.055)
        average = float(data.average) if data.average is not None else 0
        if data.average is not None:
            ax.axhline(average, color=RED, linestyle=(0, (5, 3)), linewidth=1.8,
                       label=f"日均线 ¥{data.average:.2f}")
        ax.set_ylim(0, max(max(values), average, 1) * 1.25)
        ax.set_xlim(0.5, len(values) + 0.5)
        # 均匀抽取日期刻度，月底不会因为相邻两天都打刻度而文字交叠。
        ticks = sorted({1 + (len(values) - 1) * step // 5 for step in range(6)})
        ax.set_xticks(ticks, [f"{self.month[-2:]}/{day:02d}" for day in ticks])
        ax.grid(axis="y", color=BORDER, linestyle=(0, (3, 3)), linewidth=0.7)
        ax.set_ylabel("金额（元）", fontproperties=font, color=MUTED, labelpad=20)
        ax.legend(loc="lower right", bbox_to_anchor=(1, 1.04), ncol=2,
                  borderaxespad=0, frameon=False, prop=font)

    def _draw_half_year(self, ax, font):
        values = self.data.half_year
        incomes = [float(income) for _, income, _ in values]
        expenses = [float(expense) for _, _, expense in values]
        ax.bar(range(6), incomes, width=0.52, color=GREEN, label="收入")
        ax.bar(range(6), [-value for value in expenses], width=0.52, color="#F08B68", label="支出")
        ax.axhline(0, color="#899AB2", linewidth=1)
        lower, upper = max(max(expenses), 1), max(max(incomes), 1)
        ax.set_ylim(-lower * 1.55, upper * 1.35)
        ax.set_xlim(-0.65, 5.65)
        ax.set_xticks(range(6), [month.replace("-", "/") for month, _, _ in values])
        ax.set_ylabel("金额（元）", fontproperties=font, color=MUTED, labelpad=20)
        ax.grid(axis="y", color=BORDER, linestyle=(0, (3, 3)), linewidth=0.7)
        ax.legend(loc="lower right", bbox_to_anchor=(1, 1.04), ncol=2,
                  borderaxespad=0, frameon=False, prop=font)
        padding = (upper + lower) * 0.025
        for index, (_, income, expense) in enumerate(values):
            ax.text(index, incomes[index] + padding, f"{income:,.2f}", ha="center", va="bottom",
                    color=GREEN, fontproperties=font)
            ax.text(index, -expenses[index] - padding, f"{expense:,.2f}", ha="center", va="top",
                    color="#C86A49", fontproperties=font)

    def destroy(self):
        # 父窗销毁也会经过这里；先作废任务，阻止排队回调向已关闭窗口写入。
        if not self.closing and self._setup_complete:
            self.revision += 1
            self.generation += 1
            self.stop.set()
            _put_latest(self.jobs, None)  # 唤醒等待中的守护线程；主线程不 join，关窗不阻塞。
            if self.poll_job is not None:
                self.after_cancel(self.poll_job)
                self.poll_job = None
            if self.render_job is not None:
                self.after_cancel(self.render_job)
                self.render_job = None
            self._clear_chart()
        super().destroy()
