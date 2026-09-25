"""Interactive snail animation and speech bubbles for AccountKeeper."""

from __future__ import annotations

import json
import random
import tkinter as tk
import tkinter.font as tkfont
from typing import cast

import customtkinter as ctk
from PIL import Image

from config import (
    DEFAULT_SNAIL_MESSAGE,
    SNAIL_IMAGE_CANDIDATES,
    SNAIL_IMAGE_PATH,
    SNAIL_MESSAGES_PATH,
)


# 气泡宽度上限占主窗口宽度的比例：留出余量，避免气泡贴着窗口边缘或超出可视区域。
BUBBLE_WIDTH_RATIO = 0.8


def _wrap_message_by_width(
    font: tkfont.Font,
    message: str,
    max_text_width: int,
) -> list[str]:
    """按像素宽度把提示语折成多行，让长文本"变高"而不是"变宽"。"""
    lines: list[str] = []
    current = ""
    for char in message:
        # 逐字累加并实时测量，一旦超过可用宽度就先收下当前这行。
        if current and font.measure(current + char) > max_text_width:
            # 只有在"空格之前的部分已经占满大半行"时才在空格处断行。
            # 否则为了迁就一个靠前的空格（例如"欢迎使用 AccountKeeper..."），
            # 会把整行剩下的内容全部推到下一行，出现第一行只有几个字、第二行爆满的难看效果。
            head, separator, tail = current.rpartition(" ")
            if (
                head
                and separator
                and char != " "
                and font.measure(head) >= max_text_width * 0.5
            ):
                # 按单词边界断行，避免把英文单词从中间劈成两半（中文没有空格，走下面的硬断）。
                lines.append(head)
                current = tail + char
                continue
            # 硬断：直接把当前内容作为一行，断点处的空格丢弃，避免新行以空格开头而偏移。
            lines.append(current.rstrip())
            current = "" if char == " " else char
        else:
            current += char
    # 收尾时同样要追加一次：即便 message 为空串也要返回一个元素，
    # 否则调用方对它做 max() 会因为空序列报错。
    lines.append(current.rstrip())
    return lines


class SnailManager:
    """Manage the decorative snail, animation, and speech bubbles."""

    def __init__(self, master: ctk.CTk, title_block: ctk.CTkFrame) -> None:
        self.master = master
        # 标题控件：蜗牛不能从标题文字上压过去，需要它的矩形范围来"绕行"。
        self.title_block = title_block
        # after() 返回的定时器句柄，用于暂停/取消动画，避免留下野定时器。
        self.snail_animation_id: str | None = None
        self.snail_x = 0
        self.snail_started = False
        # 弹出气泡或用户点击时置为 True，让蜗牛原地等待，不遮挡正在阅读的气泡。
        self.snail_paused = False
        # 当前活动气泡的 Canvas 与自动关闭定时器，任一时刻最多只有一个气泡。
        self._bubble_canvas: tk.Canvas | None = None
        self._bubble_after_id: str | None = None
        self.snail_label: ctk.CTkLabel | None = None
        self.snail_photo: ctk.CTkImage | None = None

    def start(self) -> None:
        """Load the snail image, create its label, and start its animation."""
        try:
            image = self._prepare_snail_image()
            # 统一缩放到 40x40 再创建 CTkImage，避免原图过大拖慢每次重绘。
            image.thumbnail((40, 40), Image.Resampling.LANCZOS)
            self.snail_photo = ctk.CTkImage(
                light_image=image,
                dark_image=image,
                size=image.size,
            )
        except (OSError, ValueError) as error:
            # 装饰性元素加载失败不应该影响记账主功能，打印警告后直接放弃蜗牛。
            print(f"警告：无法加载蜗牛图片：{error}")
            return

        self.snail_label = ctk.CTkLabel(
            self.master,
            image=self.snail_photo,
            text="",
            cursor="hand2",
            fg_color="transparent",
        )
        # 先强制刷新几何信息，否则 winfo_width() 可能返回 1（窗口尚未真正布局完成）。
        self.master.update_idletasks()
        window_width = self.master.winfo_width()
        # 只有窗口已布局且已显示时才摆放蜗牛，否则坐标不可靠。
        if window_width > 1 and self.master.winfo_ismapped():
            # 起点放在窗口最右侧，让蜗牛"从屏幕外爬进来"。
            self.snail_x = window_width
            self.snail_label.place(x=self.snail_x, y=15, anchor="nw")
        self.snail_label.bind("<Button-1>", self._snail_clicked)
        self._animate_snail()

    def _prepare_snail_image(self) -> Image.Image:
        """处理白底、等比缩放，并加深蜗牛线条颜色。"""
        # 依次尝试候选路径，取第一个真实存在的文件；全都找不到时才退回默认路径，
        # 这样即便将来替换/改名图片，也不会因为硬编码路径而直接崩溃。
        image_path = next(
            (path for path in SNAIL_IMAGE_CANDIDATES if path.exists()),
            SNAIL_IMAGE_PATH,
        )
        image = Image.open(image_path).convert("RGBA")
        # 先整体缩放到 100x100 以内再做逐像素处理，可大幅减少循环次数（性能考虑）。
        image.thumbnail((100, 100), Image.Resampling.LANCZOS)
        for y in range(image.height):
            for x in range(image.width):
                red, green, blue, alpha_value = cast(
                    tuple[int, int, int, int], image.getpixel((x, y))
                )
                # 接近纯白的像素判定为背景，直接把 alpha 置 0 做成透明，
                # 否则浅色主题下会出现一个难看的白色方块。
                if red > 230 and green > 230 and blue > 230:
                    image.putpixel((x, y), (red, green, blue, 0))
                # 偏蓝的线条统一加深成深靛蓝，保证在浅色背景上足够清晰。
                elif blue > red + 20 and blue > green + 5:
                    image.putpixel((x, y), (30, 58, 138, alpha_value))
        return image

    def _animate_snail(self) -> None:
        """让蜗牛从右向左爬行，遇到标题和左边界时传送。"""
        # 暂停时只维持定时器心跳，不做位移，这样恢复时能立刻接着爬。
        if self.snail_paused:
            self.snail_animation_id = self.master.after(30, self._animate_snail)
            return
        # 窗口尚未完成布局或已最小化时，几何尺寸不可信，先慢速等待。
        if self.master.winfo_width() <= 1 or not self.master.winfo_ismapped():
            self.snail_animation_id = self.master.after(100, self._animate_snail)
            return
        # 控件已被销毁（例如窗口关闭）时彻底停止循环，避免对已销毁对象调用方法报错。
        if self.snail_label is None or not self.snail_label.winfo_exists():
            return

        window_width = self.master.winfo_width()
        snail_width = self.snail_label.winfo_width()
        if not self.snail_started:
            # 首帧只负责摆好初始位置，不移动，避免出现"从左上角突然跳到右边"的闪烁。
            self.snail_x = window_width
            self.snail_started = True
            self.snail_label.place(
                x=self.snail_x,
                y=15,
                anchor="nw",
            )
            self.snail_animation_id = self.master.after(30, self._animate_snail)
            return
        # 用屏幕绝对坐标相减换算成主窗口内的相对坐标，
        # 这样即使窗口被拖动或移动位置，标题的判定区间依然正确。
        text_left = self.title_block.winfo_rootx() - self.master.winfo_rootx()
        text_right = text_left + self.title_block.winfo_width()

        # 每次左移 2 像素，配合 30ms 的定时器形成匀速爬行动画。
        self.snail_x -= 2
        # 当蜗牛身体与标题文字产生重叠时，直接"传送"到标题左侧，模拟从标题后面钻过去。
        if self.snail_x <= text_right and self.snail_x + snail_width > text_left:
            self.snail_x = text_left - snail_width
        # 爬出左边界后回到右侧重新入场，形成无限循环。
        if self.snail_x < 0:
            self.snail_x = window_width

        # 只改 x 坐标，避免每帧重建控件；y 始终固定在窗口顶部。
        self.snail_label.place_configure(x=self.snail_x, y=15)
        self.snail_animation_id = self.master.after(30, self._animate_snail)

    def _destroy_active_bubble(self, restore_pause: bool = True) -> None:
        """销毁当前气泡 Canvas，并在需要时恢复蜗牛动画。"""
        # 必须先取消自动关闭定时器：否则气泡已被点掉后，3 秒到的回调仍会再执行一次，
        # 反复点击蜗牛就会累积多个待触发的定时器，导致"点了没反应"或动画抖动。
        if self._bubble_after_id is not None:
            self.master.after_cancel(self._bubble_after_id)
            self._bubble_after_id = None
        if self._bubble_canvas is not None:
            # 控件可能已随窗口一起被销毁，此时 destroy 会抛 TclError，忽略即可。
            try:
                self._bubble_canvas.destroy()
            except tk.TclError:
                pass
            self._bubble_canvas = None
        if restore_pause:
            # 气泡关闭后让蜗牛继续爬行；连点蜗牛时传 False 可保持暂停，避免动画闪跳。
            self.snail_paused = False

    def _show_speech_bubble(self, message: str) -> None:
        """在主窗口内创建一个跟随蜗牛的圆角气泡 Canvas。"""
        # 先清掉旧气泡，保证同时只存在一个气泡（restore_pause=False 避免中途恢复动画）。
        self._destroy_active_bubble(restore_pause=False)
        if self.snail_label is None or not self.snail_label.winfo_exists():
            return

        bubble_font = ("Microsoft YaHei UI", 11)
        # 用临时字体对象量出文字宽度，据此决定气泡尺寸，实现"气泡大小跟着文字走"。
        # 这里直接用 bubble_font 这个字体描述来构造：保证"量出来的宽度"和"画出来的文字"
        # 使用完全相同的字体，否则两者解析结果一旦不同，换行宽度就会与实际渲染对不上。
        temp_font = tkfont.Font(font=bubble_font)
        padding_x = 18
        padding_y = 12
        window_width = self.master.winfo_width()
        # 气泡宽度必须有上限，否则一句长提示语会横向顶出窗口、两端文字被裁掉；
        # 下限 120 保证窗口很窄时单字提示仍然是完整的小气泡。
        max_bubble_width = max(120, int(window_width * BUBBLE_WIDTH_RATIO))
        # 一行文字最多能占的宽度 = 气泡上限减去左右内边距。
        max_text_width = max(1, max_bubble_width - padding_x * 2)
        if temp_font.measure(message) <= max_text_width:
            wrapped_lines = [message]
        else:
            # 超过上限就自动换行：让气泡"长高"来容纳长文本，而不是无限变宽。
            wrapped_lines = _wrap_message_by_width(temp_font, message, max_text_width)
        # 宽度取换行后最宽的一行，既不超出上限也不会留下大片空白。
        longest_line = max(temp_font.measure(line) for line in wrapped_lines)
        bubble_width = min(max(120, longest_line + padding_x * 2), max_bubble_width)
        # 高度按实际行数计算：linespace 是单行文字高度，行数变多气泡就相应变高。
        bubble_height = max(
            40,
            temp_font.metrics("linespace") * len(wrapped_lines) + padding_y * 2,
        )
        # 多出的 12 像素高度留给指向蜗牛的小尾巴。
        canvas_height = bubble_height + 12

        # 只使用主窗口内的相对坐标，Canvas 会随主窗口一起移动和缩放。
        snail_center_x = self.snail_label.winfo_x() + self.snail_label.winfo_width() / 2
        snail_center_y = self.snail_label.winfo_y() + self.snail_label.winfo_height() / 2
        # 气泡水平居中对齐蜗牛，垂直方向整体放在蜗牛上方。
        bubble_x = int(snail_center_x - bubble_width / 2)
        bubble_y = int(snail_center_y - 10 - canvas_height)
        # window_width 在计算气泡宽度上限时已经取过，这里直接复用，避免同一次绘制重复查询。
        margin = 12
        tail_above = False
        if bubble_y < margin:
            # 蜗牛位于窗口顶部时，上方没有足够空间，改放到蜗牛下方。
            bubble_y = (
                self.snail_label.winfo_y()
                + self.snail_label.winfo_height()
                + 10
            )
            tail_above = True
        # 水平方向做边界收拢，防止气泡被窗口边缘裁掉一半。
        if bubble_x < margin:
            bubble_x = margin
        if bubble_x + bubble_width > window_width - margin:
            bubble_x = max(margin, window_width - margin - bubble_width)

        # Canvas 直接挂在主窗口上（不是 Toplevel），这样拖动窗口时气泡会跟着一起移动，
        # 不会出现 Toplevel 那种"窗口动了气泡还停在原处"的滞后感，这是刻意的设计约束。
        canvas = tk.Canvas(
            self.master,
            width=bubble_width,
            height=canvas_height,
            bg="#F0F4F8",
            highlightthickness=0,
        )
        canvas.place(x=bubble_x, y=bubble_y)

        # 用 8 个控制点 + smooth=True，让 create_polygon 渲染成圆角矩形。
        body_top = 12 if tail_above else 0
        # 圆角半径不能超过宽高的一半，否则形状会崩坏。
        radius = min(14, bubble_width // 4, bubble_height // 2)
        rounded_body = [
            (radius, body_top),
            (bubble_width - radius, body_top),
            (bubble_width, body_top + radius),
            (bubble_width, body_top + bubble_height - radius),
            (bubble_width - radius, body_top + bubble_height),
            (radius, body_top + bubble_height),
            (0, body_top + bubble_height - radius),
            (0, body_top + radius),
        ]
        canvas.create_polygon(
            rounded_body,
            fill="#FFF7D8",
            outline="#FFF7D8",
            smooth=True,
        )

        # 小尾巴要指向蜗牛中心：换算成相对 Canvas 的 x 坐标，避免气泡贴边时指错方向。
        tail_x = int(snail_center_x - bubble_x)
        if tail_above:
            tail_points = [
                (tail_x - 8, body_top + 1),
                (tail_x + 8, body_top + 1),
                (tail_x, 0),
            ]
        else:
            tail_points = [
                (tail_x - 8, bubble_height - 1),
                (tail_x + 8, bubble_height - 1),
                (tail_x, canvas_height),
            ]
        canvas.create_polygon(
            tail_points,
            fill="#FFF7D8",
            outline="#FFF7D8",
            smooth=True,
        )

        canvas.create_text(
            bubble_width / 2,
            # 文字垂直居中：位置随尾巴在上方与否平移，防止文字压到尾巴上。
            body_top + bubble_height / 2,
            # 传入已经手工折好行的文本，Tk 不再需要自己换行，行宽与气泡宽度严格对应。
            text="\n".join(wrapped_lines),
            fill="#3E4A5A",
            font=bubble_font,
            justify="center",
        )
        # 点击气泡立即关闭它（并恢复蜗牛动画），比干等 3 秒更符合直觉。
        canvas.bind("<Button-1>", lambda _event: self._destroy_active_bubble())
        self._bubble_canvas = canvas
        # 记下定时器句柄，便于用户提前点击关闭时取消它，避免定时器泄漏。
        self._bubble_after_id = self.master.after(
            3000,
            self._destroy_active_bubble,
        )

    def _snail_clicked(self, _event: tk.Event) -> None:
        """点击蜗牛时随机显示一条 JSON 消息气泡，并暂停蜗牛动画。"""
        # 先暂停蜗牛，防止它继续移动导致气泡位置显得错乱。
        self.snail_paused = True
        message = DEFAULT_SNAIL_MESSAGE
        try:
            with SNAIL_MESSAGES_PATH.open("r", encoding="utf-8") as file:
                messages = json.load(file)
            # 文件被用户改坏时（非列表或空列表）也要能用默认文案兜底。
            if not isinstance(messages, list) or not messages:
                raise ValueError("消息列表为空或格式错误")
            # 过滤掉非字符串与空字符串，避免把 None 之类的值显示成气泡文字。
            text_messages = [item for item in messages if isinstance(item, str) and item]
            if not text_messages:
                raise ValueError("消息列表中没有有效文本")
            message = random.choice(text_messages)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            # 文案只是趣味功能，读取出错只打印警告并退回默认文案，绝不打断用户记账。
            print(f"警告：无法读取蜗牛消息，使用默认提示：{error}")
        self._show_speech_bubble(message)
