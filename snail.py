"""Interactive snail animation and speech bubbles for AccountKeeper."""

from __future__ import annotations

import json
import random
import tkinter as tk
import tkinter.font as tkfont

import customtkinter as ctk
from PIL import Image

from config import (
    DEFAULT_SNAIL_MESSAGE,
    SNAIL_IMAGE_CANDIDATES,
    SNAIL_MESSAGES_PATH,
)


# 气泡宽度上限占主窗口宽度的比例：留出余量，避免气泡贴着窗口边缘或超出可视区域。
BUBBLE_WIDTH_RATIO = 0.8

# 右键彩蛋的固定文案：刻意不放进 snail_messages.json，
# 否则左键随机抽文案时也会抽到这句，彩蛋就不再是"彩蛋"了。
SNAIL_EASTER_EGG_MESSAGE = "诶~我躲（恭喜你找到了作者的彩蛋）"


def _wrap_message_by_width(
    font: tkfont.Font,
    message: str,
    max_text_width: int,
) -> list[str]:
    """保留显式换行与空行，再按像素宽度折行，统一文字和尺寸模型。"""
    lines: list[str] = []
    # 统一常见换行格式后用 split 保留首尾及连续空行，不能用会丢尾部空行的 splitlines。
    paragraphs = message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for paragraph in paragraphs:
        if font.measure(paragraph) <= max_text_width:
            # 短段落含空串都直接保留，既减少逐字测量，也让空行参与高度计算。
            lines.append(paragraph)
            continue
        current = ""
        for char in paragraph:
            # 逐字累加并实时测量，一旦超过可用宽度就先收下当前这行。
            if current and font.measure(current + char) > max_text_width:
                # 只有空格之前已占大半行才按单词断，避免中文夹英文时留下过大的空白。
                head, separator, tail = current.rpartition(" ")
                if (
                    head
                    and separator
                    and char != " "
                    and font.measure(head) >= max_text_width * 0.5
                ):
                    lines.append(head)
                    current = tail + char
                    continue
                # 长词或中文按字符折行；单个超宽字符独占一行，保证循环始终能推进。
                lines.append(current.rstrip())
                current = "" if char == " " else char
            else:
                current += char
        # 每个段落各自收尾，显式换行不能被自动折行吞掉；空消息同样保留一行。
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
        # 窗口是否持有焦点：失焦期间即便气泡自动关闭，也不该让蜗牛"恢复"爬行。
        self._window_focused = True
        # 窗口是否已经真正显示过：未显示的 Toplevel 会谎报 Tk 默认的 200x200，
        # 直接拿它当入口坐标，蜗牛就会从窗口偏左处冒出来（见 _on_window_shown）。
        self._window_shown = False
        # 当前活动气泡的 Canvas 与自动关闭定时器，任一时刻最多只有一个气泡。
        self._bubble_canvas: tk.Canvas | None = None
        self._bubble_after_id: str | None = None
        # 保留原文才能在窗口变窄时重新折行，不能把上一次的自动折行当成显式换行。
        self._bubble_message: str | None = None
        self._bubble_view: tk.Canvas | None = None
        self._geometry_after_id: str | None = None
        self._geometry_signature: tuple[int, ...] | None = None
        self.snail_label: ctk.CTkLabel | None = None
        self.snail_photo: ctk.CTkImage | None = None

    def start(self) -> None:
        """Load the snail image, create its label, and start its animation."""
        try:
            image = self._prepare_snail_image()
            # 40 是逻辑显示尺寸，源图保留高分辨率，让 CTk 按 DPI 直接生成清晰图像。
            scale = 40 / max(image.size)
            display_size = tuple(max(1, round(length * scale)) for length in image.size)
            self.snail_photo = ctk.CTkImage(
                light_image=image,
                dark_image=image,
                size=display_size,
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
        # 蜗牛是标题区的悬浮装饰，显式提升层级以满足独立组件的显示要求。
        self.snail_label.lift()
        # 先强制刷新几何信息，否则 winfo_width() 可能返回 1（窗口尚未真正布局完成）。
        self.master.update_idletasks()
        window_width = self.master.winfo_width()
        # 只有窗口已布局且已显示时才摆放蜗牛，否则坐标不可靠。
        # 这条分支覆盖"窗口早已显示、之后才创建蜗牛"的场景；正常启动时窗口还没显示，走下面的 <Map>。
        if window_width > 1 and self.master.winfo_ismapped():
            self._window_shown = True
            self._enter_from_right(window_width)
        # 正常启动时窗口尚未显示，而此时的 winfo_width() 不是 1 而是 Tk 默认的 200，
        # 拿它当入口坐标会让蜗牛从窗口偏左处冒出来，所以入口摆放必须推迟到窗口真正显示的 <Map> 事件。
        self.master.bind("<Map>", self._on_window_shown)
        self.snail_label.bind("<Button-1>", self._snail_clicked)
        # 右键彩蛋：随机传送到行进路线上并冒出一句固定文案（<Button-3> 即 Windows 下的右键）。
        self.snail_label.bind("<Button-3>", self._snail_right_clicked)
        # 用户真正切走窗口（例如 Alt+Tab）时暂停动画，切回来自动恢复；
        # 用焦点事件而不是键盘事件判断，才不会把 Alt 这类系统按键误当成"离开窗口"。
        self.master.bind("<FocusOut>", self._on_focus_out)
        self.master.bind("<FocusIn>", self._on_focus_in)
        # 原生 bind + add 保留 CTk 自己的尺寸监听；只处理相关控件，不被气泡重绘自激。
        for widget in (self.master, self.title_block, self.snail_label):
            tk.Misc.bind(widget, "<Configure>", self._on_geometry_changed, add="+")
        self._animate_snail()

    def _on_geometry_changed(self, event: tk.Event) -> None:
        """将一轮窗口、标题和图片尺寸变化合并到布局完成之后处理。"""
        if event.widget not in (self.master, self.title_block, self.snail_label):
            return
        if self._geometry_after_id is None:
            # 等 CTk 的 DPI 尺寸与标题布局一起落定，避免按这一轮中间尺寸反复移动。
            self._geometry_after_id = self.master.after_idle(self._reflow_geometry)

    def _reflow_geometry(self) -> None:
        """仅在实际几何变化时收拢暂停位置并重排现有气泡。"""
        self._geometry_after_id = None
        if (
            self.snail_label is None or not self.snail_label.winfo_exists()
            or not self.title_block.winfo_exists()
        ):
            return
        window_width, snail_width, text_left, text_right = self._snail_bounds()
        window_height = self.master.winfo_height()
        if window_width <= 1 or window_height <= 1:
            return  # 未布局的 1px 不是有效边界，等待下一次尺寸事件。
        signature = (
            window_width, window_height, snail_width, self.snail_label.winfo_height(),
            text_left, text_right,
        )
        if signature == self._geometry_signature:
            return  # 单纯拖动窗口或蜗牛位移不能重置气泡和计时。
        self._geometry_signature = signature
        if self.snail_paused and self.snail_started:
            # 取最近的合法路线落点，既保持可见，也避免缩窗后收拢到标题上。
            candidates = [
                min(max(self.snail_x, left), right)
                for left, right in self._snail_route_segments()
            ]
            self.snail_x = min(candidates, key=lambda x: abs(x - self.snail_x))
            self._place_snail()
        if self._bubble_canvas is not None:
            # 原 Canvas 和关闭定时器保持不变，只重绘；缩放不会续期或解除暂停。
            self._layout_speech_bubble()

    def _on_focus_out(self, _event: tk.Event) -> None:
        """窗口失去焦点时暂停动画，避免在后台空转重绘。"""
        self._window_focused = False
        self.snail_paused = True

    def _on_focus_in(self, _event: tk.Event) -> None:
        """窗口重新获得焦点时恢复动画；气泡仍在显示则保持暂停，避免蜗牛与气泡错位。"""
        self._window_focused = True
        # 气泡打开期间 snail_paused 同样为 True，这里不能无条件清零，
        # 否则「点开气泡 → 切走窗口 → 切回来」会让蜗牛在气泡还挂着时重新爬动。
        if self._bubble_canvas is None:
            self.snail_paused = False

    def _on_window_shown(self, _event: tk.Event) -> None:
        """窗口真正显示后补一次入口摆放：只有这时 winfo_width() 才是真实宽度。"""
        self._window_shown = True
        # 已经入场过就不再摆：从最小化恢复同样会触发 <Map>，那时应保持蜗牛当前位置。
        if not self.snail_started:
            self._enter_from_right(self.master.winfo_width())

    def _enter_from_right(self, window_width: int) -> None:
        """把蜗牛放到窗口最右侧，让它"从屏幕外爬进来"。"""
        if window_width <= 1:
            # 几何信息还没生效，留给下一帧动画重试。
            return
        self.snail_x = window_width
        self.snail_started = True
        self._place_snail()

    def _place_snail(self) -> None:
        """全部定位统一使用物理像素，保持与 winfo_* 的测量结果一致。"""
        if self.snail_label is not None and self.snail_label.winfo_exists():
            # 显式调用原生 Tk：CTk 只缩放图片尺寸，不对位置二次缩放或缓存旧定位。
            tk.Place.place(self.snail_label, x=self.snail_x, y=15, anchor="nw")

    def _snail_bounds(self) -> tuple[int, int, int, int]:
        """取得窗口宽度、蜗牛宽度与标题左右边界，全部为物理像素。"""
        window_width = self.master.winfo_width()
        # 已布局时使用当前实际宽度，DPI 改变后的下一次路线计算会自动取到新尺寸。
        snail_width = (
            self.snail_label.winfo_width()
            if self.snail_label is not None and self.snail_label.winfo_exists()
            else 40
        )
        # root 坐标相减后得到窗口内坐标，拖动主窗不会改变标题避让口径。
        text_left = self.title_block.winfo_rootx() - self.master.winfo_rootx()
        text_right = text_left + self.title_block.winfo_width()
        return window_width, snail_width, text_left, text_right

    def _prepare_snail_image(self) -> Image.Image:
        """读取已清理的透明素材，保留源图像素与抗锯齿 alpha。"""
        last_error: OSError | ValueError | None = None
        for image_path in SNAIL_IMAGE_CANDIDATES:
            try:
                # convert 返回独立图像，退出上下文后可以关闭文件而继续交给 CTk 使用。
                with Image.open(image_path) as source:
                    image = source.convert("RGBA")
                alpha_min, alpha_max = image.getchannel("A").getextrema()
                if alpha_min == 255 or alpha_max == 0:
                    # 旧绘图截图与全透明空图都不能充当 UI 素材，避免回退时重新带入网格。
                    raise ValueError("蜗牛素材必须具有透明背景和可见线条")
                return image
            except (OSError, ValueError) as error:
                # 候选缺失、损坏或不透明时继续尝试；装饰加载失败由 start 统一降级。
                last_error = error
        raise OSError("没有可用的透明蜗牛素材") from last_error

    def _animate_snail(self) -> None:
        """让蜗牛从右向左爬行，遇到标题和左边界时传送。"""
        # 暂停时只维持定时器心跳，不做位移，这样恢复时能立刻接着爬。
        if self.snail_paused:
            self.snail_animation_id = self.master.after(30, self._animate_snail)
            return
        # 窗口尚未真正显示时 winfo_width() 会谎报 Tk 默认的 200（不是 1），
        # 据此摆放会让蜗牛从窗口偏左处冒出来，所以必须先等 <Map> 事件把 _window_shown 置位。
        # 这里刻意不再判断 winfo_ismapped()：按下 Alt 键会让 Tk 短暂认为窗口未映射，
        # 而动画定时器一旦据此提前 return，蜗牛就会永远停在原地（表现为卡死），
        # 所以改用"只在启动阶段判一次"的 _window_shown，它与 Alt 无关。
        if not self._window_shown or self.master.winfo_width() <= 1:
            self.snail_animation_id = self.master.after(100, self._animate_snail)
            return
        # 控件已被销毁（例如窗口关闭）时彻底停止循环，避免对已销毁对象调用方法报错。
        if self.snail_label is None or not self.snail_label.winfo_exists():
            return

        window_width, snail_width, text_left, text_right = self._snail_bounds()
        if not self.snail_started:
            # 首帧只负责摆好初始位置，不移动，避免出现"从左上角突然跳到右边"的闪烁。
            # 兜底：正常启动时 <Map> 事件已经摆好，这里只在事件没赶上（例如绑定前窗口就已显示）时生效。
            self._enter_from_right(window_width)
            self.snail_animation_id = self.master.after(30, self._animate_snail)
            return
        # 每次左移 2 像素，配合 30ms 的定时器形成匀速爬行动画。
        self.snail_x -= 2
        # 当蜗牛身体与标题文字产生重叠时，直接"传送"到标题左侧，模拟从标题后面钻过去。
        if self.snail_x <= text_right and self.snail_x + snail_width > text_left:
            self.snail_x = text_left - snail_width
        # 爬出左边界后回到右侧重新入场，形成无限循环。
        if self.snail_x < 0:
            self.snail_x = window_width

        # 通过同一物理坐标入口移动，不重建控件；y 固定为顶部15物理像素。
        self._place_snail()
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
        # 子视口随外层 Canvas 一起销毁，清掉引用，下一条消息从顶部开始阅读。
        self._bubble_view = None
        self._bubble_message = None
        if restore_pause and self._window_focused:
            # 气泡关闭后让蜗牛继续爬行；连点蜗牛时传 False 可保持暂停，避免动画闪跳。
            # 窗口已失焦时也不恢复：否则用户切走后气泡到点自动关闭，蜗牛又会在后台爬。
            self.snail_paused = False

    def _show_speech_bubble(self, message: str) -> None:
        """在主窗口内创建一个跟随蜗牛的圆角气泡 Canvas。"""
        # 先清掉旧气泡，保证同时只存在一个气泡（restore_pause=False 避免中途恢复动画）。
        self._destroy_active_bubble(restore_pause=False)
        if self.snail_label is None or not self.snail_label.winfo_exists():
            return

        # Canvas 保持同一个实例，尺寸变化只改布局，避免闪烁及误重置自动关闭时间。
        self._bubble_canvas = tk.Canvas(
            self.master, bg="#F0F4F8", highlightthickness=0, borderwidth=0,
        )
        self._bubble_message = message
        self._bubble_canvas.bind("<Button-1>", lambda _event: self._destroy_active_bubble())
        self._layout_speech_bubble()
        self._restart_bubble_timeout()

    def _restart_bubble_timeout(self) -> None:
        """新消息或主动滚动后重新计时，普通重排不调用此函数。"""
        if self._bubble_after_id is not None:
            self.master.after_cancel(self._bubble_after_id)
        # 滚动也算正在阅读，最后一次滚动后的3秒才关闭，不让长文读到一半消失。
        self._bubble_after_id = self.master.after(3000, self._destroy_active_bubble)

    def _layout_speech_bubble(self) -> None:
        """共用首次显示和窗口重排的测量、绘制与可滚动正文布局。"""
        canvas = self._bubble_canvas
        if canvas is None or self._bubble_message is None or self.snail_label is None:
            return
        # 先保存滚动进度再重建视口，缩窗后仍停在大致相同的正文位置。
        scroll_fraction = self._bubble_view.yview()[0] if self._bubble_view is not None else 0.0
        for child in canvas.winfo_children():
            child.destroy()
        self._bubble_view = None
        canvas.delete("all")

        bubble_font = ("Microsoft YaHei UI", 11)
        # 用临时字体对象量出文字宽度，据此决定气泡尺寸，实现"气泡大小跟着文字走"。
        # 这里直接用 bubble_font 这个字体描述来构造：保证"量出来的宽度"和"画出来的文字"
        # 使用完全相同的字体，否则两者解析结果一旦不同，换行宽度就会与实际渲染对不上。
        temp_font = tkfont.Font(root=self.master, font=bubble_font)
        padding_x = 18
        padding_y = 12
        window_width = self.master.winfo_width()
        window_height = self.master.winfo_height()
        margin = 12
        # 气泡宽度必须有上限，否则一句长提示语会横向顶出窗口、两端文字被裁掉；
        # 优先保留120像素的小气泡，但窗口边距是硬上限，不能为下限牺牲可见性。
        max_bubble_width = max(
            1, min(max(120, int(window_width * BUBBLE_WIDTH_RATIO)), window_width - margin * 2),
        )
        # 一行文字最多能占的宽度 = 气泡上限减去左右内边距。
        max_text_width = max(1, max_bubble_width - padding_x * 2)
        # 所有消息都先解析显式换行，再自动折行，避免 Canvas 画多行而这里只预算一行。
        wrapped_lines = _wrap_message_by_width(temp_font, self._bubble_message, max_text_width)
        # 宽度取换行后最宽的一行，既不超出上限也不会留下大片空白。
        longest_line = max(temp_font.measure(line) for line in wrapped_lines)
        bubble_width = min(max(120, longest_line + padding_x * 2), max_bubble_width)
        # 高度按实际行数计算：linespace 是单行文字高度，行数变多气泡就相应变高。
        line_height = temp_font.metrics("linespace")
        natural_height = max(40, line_height * len(wrapped_lines) + padding_y * 2)

        # 只使用主窗口内的相对坐标，Canvas 会随主窗口一起移动和缩放。
        # place 更新 winfo_x 有一轮延迟，使用同口径的实际目标坐标，避免收拢后气泡落在旧位置。
        snail_center_x = self.snail_x + self.snail_label.winfo_width() / 2
        below_y = 15 + self.snail_label.winfo_height() + 10
        # 蜗牛固定在顶部15px，上方不够放正文；下方可用高度必须同时扣掉尾巴和底部边距。
        bubble_height = max(1, min(natural_height, window_height - margin - below_y - 12))
        overflow = natural_height > bubble_height
        scrollbar_width = 16 if overflow else 0
        if overflow:
            # 滚动条占正文宽度，必须先扣掉再折行，保证最后一列不被滚动条遮住。
            # Canvas 文字 bbox 比 measure 略宽，另留4像素免得视口裁掉首尾笔画。
            max_text_width = max(1, max_bubble_width - padding_x * 2 - scrollbar_width - 4)
            wrapped_lines = _wrap_message_by_width(temp_font, self._bubble_message, max_text_width)
            longest_line = max(temp_font.measure(line) for line in wrapped_lines)
            bubble_width = min(
                max(120, longest_line + padding_x * 2 + scrollbar_width + 4), max_bubble_width,
            )
        canvas_height = bubble_height + 12
        # 水平优先居中，正文固定在蜗牛下方，再按窗口边距收拢。
        bubble_x = int(snail_center_x - bubble_width / 2)
        bubble_y = below_y
        # window_width 在计算气泡宽度上限时已经取过，这里直接复用，避免同一次绘制重复查询。
        bubble_y = max(margin, min(bubble_y, window_height - margin - canvas_height))
        # 水平方向做边界收拢，防止气泡被窗口边缘裁掉一半。
        if bubble_x < margin:
            bubble_x = margin
        if bubble_x + bubble_width > window_width - margin:
            bubble_x = max(margin, window_width - margin - bubble_width)

        # Canvas 直接挂在主窗口上（不是 Toplevel），这样拖动窗口时气泡会跟着一起移动，
        # 不会出现 Toplevel 那种"窗口动了气泡还停在原处"的滞后感，这是刻意的设计约束。
        canvas.configure(width=bubble_width, height=canvas_height)
        canvas.place(x=bubble_x, y=bubble_y)
        tk.Misc.lift(canvas)  # Canvas.lift 是图元提升；这里需要提升整个气泡控件。

        # 用 8 个控制点 + smooth=True，让 create_polygon 渲染成圆角矩形。
        body_top = 12  # 顶部12px留给向上指向蜗牛的尾巴，正文从其下方开始。
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
        # 主体贴边时尾巴中心收进圆角之间，三角形两侧也不能伸出 Canvas。
        tail_half = min(8, max(0, (bubble_width - radius * 2) // 2))
        tail_x = max(
            radius + tail_half,
            min(int(snail_center_x - bubble_x), bubble_width - radius - tail_half),
        )
        tail_points = [
            (tail_x - tail_half, body_top + 1),
            (tail_x + tail_half, body_top + 1),
            (tail_x, 0),
        ]
        canvas.create_polygon(
            tail_points,
            fill="#FFF7D8",
            outline="#FFF7D8",
            smooth=True,
        )

        if overflow:
            view_width = max(1, bubble_width - padding_x * 2 - scrollbar_width)
            view_height = max(1, bubble_height - padding_y * 2)
            # 独立 Canvas 只裁视口，不裁原文；字号与短消息保持一致，可滚到所有行。
            view = tk.Canvas(canvas, bg="#FFF7D8", highlightthickness=0, borderwidth=0)
            view.place(x=padding_x, y=body_top + padding_y, width=view_width, height=view_height)
            text_id = view.create_text(
                view_width / 2, 0, anchor="n", text="\n".join(wrapped_lines),
                fill="#3E4A5A", font=bubble_font, justify="center",
            )
            text_box = view.bbox(text_id)
            content_height = max(line_height * len(wrapped_lines), text_box[3] if text_box else 0)
            view.configure(scrollregion=(0, 0, view_width, content_height), yscrollincrement=line_height)
            scrollbar = tk.Scrollbar(
                canvas, orient="vertical", width=scrollbar_width, command=self._scroll_bubble,
            )
            scrollbar.place(
                x=padding_x + view_width, y=body_top + padding_y,
                width=scrollbar_width, height=view_height,
            )
            view.configure(yscrollcommand=scrollbar.set)
            view.yview_moveto(scroll_fraction)
            view.bind("<MouseWheel>", self._wheel_bubble)
            scrollbar.bind("<MouseWheel>", self._wheel_bubble)
            view.bind("<Button-1>", lambda _event: self._destroy_active_bubble())
            self._bubble_view = view
            return
        canvas.create_text(
            bubble_width / 2,
            # 文字在正文区域垂直居中，不能把尾巴高度当成正文高度的一部分。
            body_top + bubble_height / 2,
            # 传入已经手工折好行的文本，Tk 不再需要自己换行，行宽与气泡宽度严格对应。
            text="\n".join(wrapped_lines),
            fill="#3E4A5A",
            font=bubble_font,
            justify="center",
        )

    def _scroll_bubble(self, *args: str) -> None:
        """滚动条操作只移动长文视口，保持蜗牛暂停并续期阅读时间。"""
        if self._bubble_view is not None:
            self._bubble_view.yview(*args)
            self._restart_bubble_timeout()

    def _wheel_bubble(self, event: tk.Event) -> str:
        if event.delta:
            # Windows 普通滚轮以120为一格，高精度滚轮小于120时仍至少移动一行。
            units = max(1, abs(event.delta) // 120)
            if event.delta > 0:
                units = -units
            self._scroll_bubble("scroll", str(units), "units")
        return "break"  # 气泡内滚轮由正文消费，不再滚动下面的记账列表。

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

    def _snail_right_clicked(self, _event: tk.Event) -> None:
        """彩蛋：右键蜗牛时随机传送到行进路线上，并弹出固定文案的气泡。"""
        # 与左键一致，先暂停：否则蜗牛会在气泡展示期间继续爬走，气泡看起来像脱了钩。
        self.snail_paused = True
        self._teleport_snail()
        self._show_speech_bubble(SNAIL_EASTER_EGG_MESSAGE)

    def _snail_route_segments(self) -> list[tuple[int, int]]:
        """给出行进路线上可停留的横向区间（已挖掉与标题文字重叠的那一段）。"""
        # 与动画共用边界，避免 DPI 改变时两处采用不同的蜗牛尺寸或标题坐标。
        window_width, snail_width, text_left, text_right = self._snail_bounds()
        # 右端要留出一个蜗牛身位，保证传送后整只蜗牛都在窗口内可见。
        right_limit = max(0, window_width - snail_width)
        segments: list[tuple[int, int]] = []
        # 标题左侧的一段：只有窗口足够宽、放得下整只蜗牛时才存在。
        left_limit = min(text_left - snail_width, right_limit)
        if left_limit >= 0:
            segments.append((0, left_limit))
        # 标题右侧到窗口右缘的一段。
        if text_right <= right_limit:
            segments.append((text_right, right_limit))
        # 窗口太窄时两段都放不下，退化成整条路线，至少保证传送可用。
        if not segments:
            segments.append((0, right_limit))
        return segments

    def _teleport_snail(self) -> None:
        """把蜗牛随机挪到行进路线上的某个位置（右键彩蛋用）。"""
        if self.snail_label is None or not self.snail_label.winfo_exists():
            return
        # 窗口还没完成布局时几何尺寸不可信，放弃这次传送，蜗牛维持原位。
        if self.master.winfo_width() <= 1:
            return
        segments = self._snail_route_segments()
        # 按区间长度加权抽取：长区间被选中的概率更高，落点在整条路线上更均匀。
        start, end = random.choices(
            segments,
            weights=[end - start + 1 for start, end in segments],
        )[0]
        self.snail_x = random.randint(start, end)
        # 标记为已入场：否则 _animate_snail 的首帧会按"从右侧爬进来"重置坐标，传送就白做了。
        self.snail_started = True
        # 彩蛋也走同一定位入口，传送后的下一帧不会因 CTk 缓存跳回入场位置。
        self._place_snail()
        # 强制刷新一次几何信息：place 不会立刻更新 winfo_x()，
        # 不刷新的话紧接着创建的气泡仍会按蜗牛传送前的位置去定位。
        self.master.update_idletasks()
