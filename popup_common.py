"""日期、标签和文件入口的共用外观与纯定位计算，不持有窗口或会话状态。

按钮尺寸采用 CTk 逻辑像素；定位参数由调用方测量为物理像素，不能混用这两种口径。
定位仅按原点为 (0, 0) 的屏幕总尺寸处理，不查询任务栏工作区、负坐标或跨屏布局。
"""

from collections.abc import Callable
from functools import lru_cache
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk


ENTRY_CORNER_RADIUS = 8


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


def _sample_entry_outline(width: int, height: int, radius: int, stroke: int,
                          background: str, fill: str, border: str) -> Image.Image:
    factor = 4
    image = Image.new("RGB", (width * factor, height * factor), background)
    ImageDraw.Draw(image).rounded_rectangle(
        (0, 0, image.width - 1, image.height - 1),
        radius=radius * factor, fill=fill, outline=border, width=stroke * factor)
    return image.resize((width, height), Image.Resampling.BOX)


@lru_cache(maxsize=32)
def _entry_corners(radius: int, stroke: int, background: str,
                   fill: str, border: str) -> tuple[Image.Image, ...]:
    # 圆角仅随缩放、描边和颜色变化，缓存小块像素可跨宽度及字段复用。
    reach = max(radius, stroke)
    size = 2 * reach + 2
    sample = _sample_entry_outline(size, size, radius, stroke, background, fill, border)
    return tuple(sample.crop((x, y, x + reach, y + reach))
                 for x, y in ((0, 0), (size - reach, 0), (0, size - reach), (size - reach, size - reach)))


def _entry_outline(width: int, height: int, radius: int, stroke: int,
                   background: str, fill: str, border: str) -> Image.Image:
    reach = max(radius, stroke)
    if min(width, height) < 2 * reach + 2:
        # 极小尺寸下圆角可能相交，由整体采样保留 Pillow 的轮廓处理。
        return _sample_entry_outline(width, height, radius, stroke, background, fill, border)
    image = Image.new("RGB", (width, height), fill)
    # 直边和底色直接按物理像素绘制，只将抗锯齿圆角覆盖到四端。
    ImageDraw.Draw(image).rectangle((0, 0, width - 1, height - 1), outline=border, width=stroke)
    for corner, position in zip(_entry_corners(radius, stroke, background, fill, border),
                                ((0, 0), (width - reach, 0), (0, height - reach), (width - reach, height - reach)),
                                strict=True):
        image.paste(corner, position)
    return image


class RoundedFieldFrame(ctk.CTkFrame):
    """按字段的实际像素绘制一条连续的圆角轮廓。"""

    def __init__(self, master: ctk.CTkBaseClass, *, width: int = 200, height: int = 40,
                 precise_outline: bool = True) -> None:
        self._outline = None
        self._outline_style = None
        self._outline_photo: ImageTk.PhotoImage | None = None
        super().__init__(master, width=width, height=height, corner_radius=ENTRY_CORNER_RADIUS, border_width=1,
                         border_color="#D8E1EA", fg_color="#F8FAFC")
        if precise_outline:
            self._outline = self._canvas.create_image(0, 0, anchor="nw")
            # 相邻物理宽度可能舍入成相同逻辑宽度，仍须按实际尺寸更新边线。
            tk.Misc.bind(self, "<Configure>", self._draw_outline, add="+")

    def _draw(self, no_color_updates=False) -> None:
        super()._draw(no_color_updates)
        if self._outline is not None:
            self._draw_outline()  # 色彩、缩放变化也可能发生在同一外框尺寸下。

    def _draw_outline(self, _event: tk.Event | None = None) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return  # 首次布局前的占位尺寸不参与绘制。
        scale = self._get_widget_scaling()
        radius = round(self.cget("corner_radius") * scale)
        # Tk 内边距向下取整，描边采用同样的物理厚度，防止 150% 时内侧一列或一行被覆盖。
        stroke = max(1, int(self.cget("border_width") * scale))
        background, fill, border = (
            self._apply_appearance_mode(self.cget(name)) for name in ("bg_color", "fg_color", "border_color")
        )
        style = (width, height, radius, stroke, background, fill, border)
        if style != self._outline_style:
            self._outline_photo = ImageTk.PhotoImage(_entry_outline(*style), master=self)
            self._canvas.itemconfigure(self._outline, image=self._outline_photo)
            self._outline_style = style
        self._canvas.tag_raise(self._outline)  # CTk 重绘的底层图元不得盖过像素对齐的轮廓。


def entry_surface(
    master: ctk.CTkBaseClass,
    textvariable: tk.StringVar | None = None,
    placeholder_text: str | None = None,
) -> tuple[ctk.CTkFrame, ctk.CTkEntry]:
    """日期、金额、标签及备注共用的输入外框。"""
    # 外框统一描边；内部输入框和附加按钮不再各画一圈边框，避免控件割裂。
    surface = ctk.CTkFrame(master, height=40, corner_radius=8, border_width=1,
                           border_color="#D8E1EA", fg_color="#F8FAFC")
    surface.columnconfigure(0, weight=1)
    entry = ctk.CTkEntry(
        surface, width=1, height=38, corner_radius=0, border_width=0,
        fg_color="#F8FAFC", text_color="#243447", font=("Microsoft YaHei UI", 12),
        textvariable=textvariable, placeholder_text=placeholder_text,
    )
    # 最小请求宽度不占满网格；内边距避开外框圆角，伸缩空间交给字段外框。
    entry.grid(row=0, column=0, sticky="ew", padx=(9, 6), pady=1)
    return surface, entry
