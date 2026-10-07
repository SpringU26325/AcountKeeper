"""日期、标签和文件入口共用的三角按钮及弹窗定位。"""

from collections.abc import Callable
import customtkinter as ctk


def make_toggle_button(master: ctk.CTkBaseClass, command: Callable[[], None],
                       *, height: int = 38, fg_color: str = "#F8FAFC") -> ctk.CTkButton:
    # 用微软雅黑自带实心三角，三种入口不再混用原生菜单绘制的箭头。
    return ctk.CTkButton(
        master, text="▼", command=command, width=32, height=height,
        corner_radius=7, fg_color=fg_color, hover_color="#D2DEE9",
        text_color="#243447", font=("Microsoft YaHei UI", 11),
    )


def anchored_position(anchor: tuple[int, int, int], size: tuple[int, int],
                      screen: tuple[int, int]) -> tuple[int, int]:
    """输入锚点x/y/高、弹窗宽高、屏幕宽高；返回物理像素坐标。"""
    anchor_x, anchor_y, anchor_height = anchor
    width, height = size
    screen_width, screen_height = screen
    # 物理像素统一留6px缝、8px边距，调用方不再各自复制越界和翻转公式。
    x = max(8, min(anchor_x, screen_width - width - 8))
    y = anchor_y + anchor_height + 6
    if y + height > screen_height - 8:
        above = anchor_y - height - 6
        y = above if above >= 8 else max(screen_height - height - 8, 8)
    return x, y
