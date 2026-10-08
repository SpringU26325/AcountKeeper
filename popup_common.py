"""日期、标签和文件入口的共用外观与纯定位计算，不持有窗口或会话状态。

按钮尺寸采用 CTk 逻辑像素；定位参数由调用方测量为物理像素，不能混用这两种口径。
定位仅按原点为 (0, 0) 的屏幕总尺寸处理，不查询任务栏工作区、负坐标或跨屏布局。
"""

from collections.abc import Callable
import customtkinter as ctk


def make_toggle_button(master: ctk.CTkBaseClass, command: Callable[[], None],
                       *, height: int = 38, fg_color: str = "#F8FAFC") -> ctk.CTkButton:
    """返回交给调用方布局和销毁的按钮；点击如何切换弹窗仍由 command 的所属组件负责。"""
    # 用微软雅黑自带实心三角，三种入口不再混用原生菜单绘制的箭头。
    # 这里仅提供外观，不登记额外任务、焦点或全局绑定，避免共用按钮与弹窗会话争抢收尾。
    return ctk.CTkButton(
        master, text="▼", command=command, width=32, height=height,
        corner_radius=7, fg_color=fg_color, hover_color="#D2DEE9",
        text_color="#243447", font=("Microsoft YaHei UI", 11),
    )


def anchored_position(anchor: tuple[int, int, int], size: tuple[int, int],
                      screen: tuple[int, int]) -> tuple[int, int]:
    """输入锚点屏幕 x/y/高、弹窗宽高、屏幕宽高，返回物理屏幕坐标。

下方优先，不够则尝试上方，再收拢到屏幕边缘；只移动、不调整弹窗尺寸。
弹窗超出屏幕本身时不能保证全部可见，调用方仍须限制窗口尺寸或提供内部滚动。
"""
    anchor_x, anchor_y, anchor_height = anchor
    width, height = size
    screen_width, screen_height = screen
    # 物理像素统一留6px缝、8px边距，调用方不再各自复制越界和翻转公式。
    x = max(8, min(anchor_x, screen_width - width - 8))  # 先右收拢再保左边距，不跟随锚点宽度拉伸。
    y = anchor_y + anchor_height + 6
    if y + height > screen_height - 8:
        above = anchor_y - height - 6
        # 两侧都不够时优先保屏幕边距，可能不再紧贴锚点；绝不靠改变尺寸制造不同组件口径。
        y = above if above >= 8 else max(screen_height - height - 8, 8)
    return x, y
