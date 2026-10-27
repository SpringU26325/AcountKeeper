"""主窗使用的弹窗公共入口；编辑和确认返回结果，账本写入由调用方负责。"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from calendar_picker import ask_month as picker_ask_month
# 函数只在实现模块读取状态；此处不重导出可重新赋值的编辑窗口或会话。
from confirm_dialogs import confirm_delete as confirm_delete, confirm_undo_import as confirm_undo_import
from dialog_common import center_dialog_on_parent as center_dialog_on_parent
from dialog_lifecycle import ManagedToplevel as ManagedToplevel
from edit_dialog import ask_edit_record as ask_edit_record


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
