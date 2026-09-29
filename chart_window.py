"""Chart window rendering for AccountKeeper."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import tkinter as tk
from typing import TYPE_CHECKING
from tkinter import messagebox

if TYPE_CHECKING:
    # 仅在类型检查时导入，避免 ui.py 与 chart_window.py 在运行时互相 import 形成循环依赖。
    from ui import AccountKeeperApp


def show_chart_window(app: AccountKeeperApp) -> None:
    """按月份汇总各分类收入和支出并显示图表。"""
    # 复用主窗口的月份输入对话框，保持交互方式一致（需求 3.6）。
    month = app.ask_month("查看图表", "请输入要查看的月份（格式：YYYY-MM）")
    if month is None:
        return

    # 每个分类都预置收入/支出两个桶，后面按符号分别累加，省去判断分类是否已存在。
    category_totals: defaultdict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"income": Decimal("0"), "expense": Decimal("0")}
    )
    for record in app.store.records:
        # 日期带前导零，所以按月前缀匹配即可精确筛选该月记录。
        if not record.record_date.startswith(month + "-"):
            continue
        # 非有限金额（例如历史脏数据里的 NaN）无法参与大小比较，直接跳过；
        # 否则下面这行 >= 会抛 InvalidOperation，导致图表窗口根本打不开。
        if not record.amount.is_finite():
            continue
        if record.amount >= 0:
            category_totals[record.category]["income"] += record.amount
        else:
            # 支出保持负数累加，画图时柱形会向 0 轴下方延伸，便于与收入对比。
            category_totals[record.category]["expense"] += record.amount

    # 用 defaultdict 是否为空判断该月有没有数据，比额外维护计数变量更简洁。
    if not category_totals:
        messagebox.showinfo("无法生成图表", "该月没有记录，无法生成图表")
        return
    _render_chart_window(app, month, category_totals)


def _render_chart_window(
    app: AccountKeeperApp,
    month: str,
    category_totals: defaultdict[str, dict[str, Decimal]],
) -> None:
    """在独立窗口中绘制月份收入与支出分类柱状图。"""
    # matplotlib 体积较大且只在看图时才需要，因此延迟到函数内部再导入，缩短程序启动时间。
    # 整个导入链都可能因为用户没装 matplotlib 而失败，所以统一包进 try 并给出安装指引，
    # 绝不能让主程序崩溃（需求 3.7 容错）。
    # try 的范围不止导入：Figure/ax.bar 在数据异常、字体缺失或 matplotlib 自身 bug 下同样会抛，
    # TkAgg 后端的初始化失败也发生在导入之后的 FigureCanvasTkAgg 里，所以一直包到画布挂载完成。
    #
    # 哨兵先置 None：窗口是在 try 内部才创建的，异常分支要靠它判断「窗口是否已经建出来」，
    # 否则异常时直接引用 chart_window 会抛 UnboundLocalError，把优雅降级变成二次崩溃。
    chart_window: tk.Toplevel | None = None
    try:
        import matplotlib

        # 必须显式指定 TkAgg 后端，否则会尝试使用默认 GUI 后端，导致无法嵌入 Tkinter 窗口。
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        # 指定中文字体并关闭 unicode 负号，否则标题、分类名会显示成方块，负号也会缺字。
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
        plt.rcParams["axes.unicode_minus"] = False

        chart_window = tk.Toplevel(app)
        # 图表同时画收入和支出两组柱子，标题必须体现"收入与支出"，与图内标题保持一致。
        chart_window.title(f"{month} 收入与支出统计")
        width, height = 700, 550
        # 按屏幕分辨率计算居中坐标，避免图表窗口出现在屏幕角落。
        screen_width = chart_window.winfo_screenwidth()
        screen_height = chart_window.winfo_screenheight()
        position_x = (screen_width - width) // 2
        position_y = (screen_height - height) // 2
        chart_window.geometry(f"{width}x{height}+{position_x}+{position_y}")

        keys = list(category_totals.keys())
        # 图表内部只接受 float，这里把 Decimal 转成 float；展示用途下精度损失可以接受。
        income_values = [
            float(category_totals[key]["income"])
            for key in keys
        ]
        expense_values = [
            float(category_totals[key]["expense"])
            for key in keys
        ]
        # 用 Figure 对象而不是 plt.show()，这样才能把图表真正嵌进 Tkinter 窗口。
        fig = Figure(figsize=(7, 5))
        ax = fig.add_subplot(111)
        positions = list(range(len(keys)))
        bar_width = 0.38
        # 收入柱向左偏移半个柱宽、支出柱向右偏移，形成并排分组柱状图而非相互遮挡。
        ax.bar(
            [position - bar_width / 2 for position in positions],
            income_values,
            width=bar_width,
            color="#4CAF50",
            label="收入",
        )
        ax.bar(
            [position + bar_width / 2 for position in positions],
            expense_values,
            width=bar_width,
            color="#E76F51",
            label="支出",
        )
        ax.set_xticks(positions)
        # 分类名可能较长，右对齐并旋转 30 度可避免文字互相重叠。
        ax.set_xticklabels(keys, rotation=30, ha="right")
        # 画一条 0 轴基准线，让上下延伸的收入/支出柱有共同参照。
        ax.axhline(0, color="#455A64", linewidth=0.8)
        ax.set_ylabel("金额（元）")
        ax.set_title(f"{month} 收入与支出统计")
        ax.legend()
        # 自动调整子图边距，防止旋转后的分类名被裁掉。
        fig.tight_layout()

        # FigureCanvasTkAgg 是 matplotlib 官方提供的 Tkinter 桥接控件。
        canvas = FigureCanvasTkAgg(fig, master=chart_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
    except ImportError:
        # ImportError 也可能发生在窗口建出来之后：缺 Tk 绑定时 matplotlib.use("TkAgg")
        # 和 FigureCanvasTkAgg(...) 自身抛的就是 ImportError，所以这里同样要清理掉空窗。
        if chart_window is not None:
            chart_window.destroy()
        messagebox.showerror("缺少依赖", "请先安装 matplotlib 库：\npip install matplotlib")
        return
    except Exception:
        # 必须捕获非 ImportError：matplotlib 装好了但 TkAgg 后端加载不了（版本不匹配、
        # 缺 Tk 绑定、字体缓存损坏）抛的是 TclError/ValueError/RuntimeError 之类，
        # 只捕 ImportError 等于这些情况一条都兜不住，点「查看图表」会直接崩掉主程序。
        #
        # 这里不复用下面的 close_chart：它定义在画布创建成功之后，此刻名字可能还没绑定；
        # 它依赖 canvas/fig/plt 三个值，导入失败时连 plt 都不存在，调用它只会变成二次异常。
        # 异常路径下唯一要做的是销毁可能已建出的窗口，避免屏幕上留下一个半残的空窗。
        if chart_window is not None:
            chart_window.destroy()
        messagebox.showerror("无法生成图表", "图表渲染失败，请检查 matplotlib 安装或重启程序")
        return

    def close_chart() -> None:
        # 关闭窗口时必须手动释放画布并关闭 Figure，否则 matplotlib 会持续占用内存。
        canvas.get_tk_widget().destroy()
        plt.close(fig)
        chart_window.destroy()

    chart_window.protocol("WM_DELETE_WINDOW", close_chart)
