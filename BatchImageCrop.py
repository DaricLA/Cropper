"""
MBO/PBO批量图片剪裁工具 v2.3
- 批量加载图片，缩略图预览列表（左右排布节省空间）
- 裁剪框固定比例、大小可调（拖拽角落缩放）
- 每张图独立裁剪位置/大小，可选共享
- 单图独立翻转/旋转，实时预览
- 零压缩无损输出，直接截取原图像素
- 绿色免安装，ttkbootstrap flatly 主题
- 键盘上下键切换图片，滚轮独立控制列表/预览
"""

import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox
import ttkbootstrap as ttkb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk
import os

SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tiff', '.tif', '.gif')
THUMB_MAX_W = 100
THUMB_MAX_H = 70
HANDLE_SIZE = 8  # 角落拖拽热区半径(px)


class ToolTip:
    """简易悬浮提示框"""
    def __init__(self, widget, text_getter=None, static_text=None):
        self.widget = widget
        self.text_getter = text_getter
        self.static_text = static_text
        self.tip_window = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event=None):
        text = self.text_getter() if self.text_getter else self.static_text
        if not text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 5
        y = self.widget.winfo_rooty()
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=text, justify="left",
                         background="#ffffe0", relief="solid", borderwidth=1,
                         font=("Microsoft YaHei", 9), wraplength=400)
        label.pack(ipadx=4, ipady=2)

    def _hide(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class PerImageSettings:
    """每张图的独立设置"""
    def __init__(self):
        self.flip_h = False
        self.flip_v = False
        self.rotate = 0
        self.crop_left = 0.0
        self.crop_top = 0.0
        self.crop_scale = 1.0


class BatchImageCrop:
    def __init__(self, root):
        self.root = root
        self.root.title("MBO/PBO批量图片剪裁工具 v2.3")
        self.root.geometry("1500x950")
        self.root.minsize(1500, 950)

        # --- 数据 ---
        self.image_files = []
        self.image_settings = {}
        self.thumbnails = {}
        self.current_index = -1
        self.original_image = None
        self.display_photo = None

        # 裁剪比例
        self.ratio_w = 4
        self.ratio_h = 3

        # 画布状态
        self.scale = 1.0
        self.zoom = 1.0
        self.pad_x = 0
        self.pad_y = 0

        # 交互状态
        self.drag_mode = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_init_left = 0.0
        self.drag_init_top = 0.0
        self.drag_init_scale = 1.0

        # UI 变量
        self.size_w_var = tk.StringVar(value="10")
        self.size_h_var = tk.StringVar(value="7")
        self.output_dir_var = tk.StringVar()
        self.suffix_var = tk.StringVar(value="_cropped")
        self.flip_h_var = tk.BooleanVar(value=False)
        self.flip_v_var = tk.BooleanVar(value=False)
        self.rotate_var = tk.StringVar(value="0")

        # 共享设置
        self.share_size_var = tk.BooleanVar(value=True)
        self.share_pos_var = tk.BooleanVar(value=False)

        # 滚动条自动隐藏定时器
        self._thumb_scroll_hide_after = None
        self._right_scroll_hide_after = None

        # 裁剪框居中标志：True=裁剪框保持在预览区居中，切换图片时自动继承
        self._crop_centered = False

        self.build_ui()

        # 键盘绑定
        self.root.bind('<Up>', lambda e: self.select_prev_image())
        self.root.bind('<Down>', lambda e: self.select_next_image())
        self.root.bind('<Control-Left>', lambda e: self.nudge(-5, 0))
        self.root.bind('<Control-Right>', lambda e: self.nudge(5, 0))
        self.root.bind('<Control-Up>', lambda e: self.nudge(0, -5))
        self.root.bind('<Control-Down>', lambda e: self.nudge(0, 5))
        self.root.bind('<Control-Shift-Left>', lambda e: self.nudge(-1, 0))
        self.root.bind('<Control-Shift-Right>', lambda e: self.nudge(1, 0))
        self.root.bind('<Control-Shift-Up>', lambda e: self.nudge(0, -1))
        self.root.bind('<Control-Shift-Down>', lambda e: self.nudge(0, 1))

    # ===================== 辅助计算 =====================
    def _get_max_crop_ratios(self, iw, ih):
        target_aspect = self.ratio_w / self.ratio_h
        img_aspect = iw / ih
        if target_aspect > img_aspect:
            return 1.0, img_aspect / target_aspect
        else:
            return target_aspect / img_aspect, 1.0

    def _get_crop_rect_rel(self, settings, iw, ih):
        max_w, max_h = self._get_max_crop_ratios(iw, ih)
        w = max_w * settings.crop_scale
        h = max_h * settings.crop_scale
        left = max(0.0, min(settings.crop_left, 1.0 - w))
        top = max(0.0, min(settings.crop_top, 1.0 - h))
        return left, top, w, h

    def _get_current_settings(self):
        if self.current_index < 0:
            return PerImageSettings()
        return self.image_settings.get(self.current_index, PerImageSettings())

    def _apply_transform(self, img, settings):
        if settings.flip_h:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if settings.flip_v:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        if settings.rotate == 90:
            img = img.transpose(Image.ROTATE_90)
        elif settings.rotate == 180:
            img = img.transpose(Image.ROTATE_180)
        elif settings.rotate == 270:
            img = img.transpose(Image.ROTATE_270)
        return img

    # ===================== 共享逻辑 =====================
    def _get_target_crop_pixels(self, src_settings):
        src_img = Image.open(self.image_files[self.current_index])
        if src_img.mode in ('RGBA', 'P'):
            src_img = src_img.convert('RGB')
        src_img = self._apply_transform(src_img, src_settings)
        iw, ih = src_img.size
        max_w, max_h = self._get_max_crop_ratios(iw, ih)
        target_w = max_w * src_settings.crop_scale * iw
        target_h = max_h * src_settings.crop_scale * ih
        return target_w, target_h

    def _compute_scale_for_pixels(self, target_w, target_h, iw, ih):
        max_w, max_h = self._get_max_crop_ratios(iw, ih)
        if max_w <= 0 or max_h <= 0:
            return 1.0
        scale_w = target_w / (max_w * iw)
        scale_h = target_h / (max_h * ih)
        new_scale = min(scale_w, scale_h)
        return max(0.1, min(1.0, new_scale))

    def _sync_shared(self, changed):
        if self.current_index < 0:
            return
        src = self._get_current_settings()
        target_w, target_h = None, None
        if changed == 'size' and self.share_size_var.get():
            target_w, target_h = self._get_target_crop_pixels(src)
        for i in range(len(self.image_files)):
            if i == self.current_index:
                continue
            s = self.image_settings.get(i, PerImageSettings())
            if changed == 'position' and self.share_pos_var.get():
                s.crop_left = src.crop_left
                s.crop_top = src.crop_top
            if changed == 'size' and self.share_size_var.get():
                try:
                    img = Image.open(self.image_files[i])
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    img = self._apply_transform(img, s)
                    iw, ih = img.size
                    s.crop_scale = self._compute_scale_for_pixels(target_w, target_h, iw, ih)
                except Exception:
                    pass
            self.image_settings[i] = s

    def _on_share_toggle(self):
        if self.current_index < 0:
            return
        src = self._get_current_settings()
        if self.share_pos_var.get():
            for i in range(len(self.image_files)):
                s = self.image_settings.get(i, PerImageSettings())
                s.crop_left = src.crop_left
                s.crop_top = src.crop_top
                self.image_settings[i] = s
        if self.share_size_var.get():
            target_w, target_h = self._get_target_crop_pixels(src)
            for i in range(len(self.image_files)):
                s = self.image_settings.get(i, PerImageSettings())
                try:
                    img = Image.open(self.image_files[i])
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    img = self._apply_transform(img, s)
                    iw, ih = img.size
                    s.crop_scale = self._compute_scale_for_pixels(target_w, target_h, iw, ih)
                except Exception:
                    pass
                self.image_settings[i] = s
        self._render_canvas()

    # ===================== UI 构建 =====================
    def build_ui(self):
        # ===== 顶部栏 =====
        top_frame = ttkb.Frame(self.root, padding=(10, 6))
        top_frame.pack(fill=X)

        ratio_lf = ttkb.LabelFrame(top_frame, text="裁剪比例（仅决定裁剪框形状）", padding=6)
        ratio_lf.pack(side=LEFT, padx=(0, 10))
        ttkb.Entry(ratio_lf, textvariable=self.size_w_var, width=5).pack(side=LEFT)
        ttkb.Label(ratio_lf, text=" : ").pack(side=LEFT)
        ttkb.Entry(ratio_lf, textvariable=self.size_h_var, width=5).pack(side=LEFT)
        ttkb.Label(ratio_lf, text="(任意单位，仅取比例)").pack(side=LEFT, padx=5)
        ttkb.Button(ratio_lf, text="应用", bootstyle="primary-outline", command=self.apply_ratio).pack(side=LEFT, padx=8)

        out_lf = ttkb.LabelFrame(top_frame, text="输出设置", padding=6)
        out_lf.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        ttkb.Label(out_lf, text="目录:").pack(side=LEFT)
        ttkb.Entry(out_lf, textvariable=self.output_dir_var, width=25).pack(side=LEFT, padx=(4, 4), fill=X, expand=True)
        ttkb.Button(out_lf, text="浏览", bootstyle="secondary-outline", command=self.browse_output).pack(side=LEFT, padx=(0, 6))
        ttkb.Label(out_lf, text="后缀:").pack(side=LEFT)
        ttkb.Entry(out_lf, textvariable=self.suffix_var, width=10).pack(side=LEFT, padx=4)

        import_lf = ttkb.LabelFrame(top_frame, text="导入", padding=6)
        import_lf.pack(side=LEFT)
        ttkb.Button(import_lf, text="添加图片", bootstyle="success", command=self.add_images).pack(side=LEFT, padx=3)
        ttkb.Button(import_lf, text="添加目录", bootstyle="success-outline", command=self.add_folder).pack(side=LEFT, padx=3)

        # ===== 中间区域 =====
        mid_frame = ttkb.Frame(self.root)
        mid_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)

        # 画布区域
        canvas_frame = ttkb.Frame(mid_frame)
        canvas_frame.pack(fill=BOTH, expand=True, side=LEFT)

        self.canvas = tk.Canvas(canvas_frame, bg="#1a1a2e", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)

        # 右侧面板（固定宽度）
        right_panel = ttkb.Frame(mid_frame, width=260)
        right_panel.pack(fill=Y, side=LEFT, padx=(8, 0))
        right_panel.pack_propagate(False)

        # 上方：可滚动图片列表（canvas 填满，滚动条 place 悬浮）
        right_scroll_frame = ttkb.Frame(right_panel)
        right_scroll_frame.pack(fill=BOTH, expand=True)

        right_canvas = tk.Canvas(right_scroll_frame, highlightthickness=0, background="white")
        right_canvas.pack(fill=BOTH, expand=True)

        right_scrollbar = ttk.Scrollbar(right_scroll_frame, orient="vertical", command=right_canvas.yview)
        right_canvas.configure(yscrollcommand=right_scrollbar.set)
        self.right_canvas = right_canvas
        self.right_sb = right_scrollbar

        right_content = ttkb.Frame(right_canvas)
        self.right_canvas_window = right_canvas.create_window((0, 0), window=right_content, anchor="nw")
        right_content.bind("<Configure>", lambda e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        right_canvas.bind("<Configure>", self._on_right_canvas_configure)
        right_canvas.bind("<MouseWheel>", self._on_right_scroll)
        right_canvas.bind("<Enter>", lambda e: self.show_right_scrollbar())
        right_canvas.bind("<Leave>", lambda e: self.schedule_hide_right_scrollbar())
        right_scrollbar.bind("<Enter>", lambda e: self.show_right_scrollbar())
        right_scrollbar.bind("<Leave>", lambda e: self.schedule_hide_right_scrollbar())

        # --- 图片列表（缩略图）---
        ttkb.Label(right_content, text="图片列表", font=("Microsoft YaHei", 10, "bold")).pack(pady=(5, 2))
        self.count_label = ttkb.Label(right_content, text="共 0 张", bootstyle="secondary")
        self.count_label.pack()

        thumb_outer = ttkb.Frame(right_content)
        thumb_outer.pack(fill=BOTH, expand=True, pady=4)

        self.thumb_canvas = tk.Canvas(thumb_outer, highlightthickness=0, bg="#f0f0f0")
        self.thumb_canvas.pack(fill=BOTH, expand=True)

        self.thumb_sb = ttk.Scrollbar(thumb_outer, orient="vertical", command=self.thumb_canvas.yview)
        self.thumb_canvas.configure(yscrollcommand=self.thumb_sb.set)

        self.thumb_inner = ttkb.Frame(self.thumb_canvas)
        self.thumb_canvas_window = self.thumb_canvas.create_window((0, 0), window=self.thumb_inner, anchor="nw")
        self.thumb_inner.bind("<Configure>", self._on_thumb_inner_configure)
        self.thumb_canvas.bind("<Configure>", self._on_thumb_canvas_configure)
        self.thumb_canvas.bind("<Enter>", lambda e: self._thumb_mouse_enter())
        self.thumb_canvas.bind("<Leave>", lambda e: self._thumb_mouse_leave())
        self.thumb_sb.bind("<Enter>", lambda e: self._thumb_mouse_enter())
        self.thumb_sb.bind("<Leave>", lambda e: self._thumb_mouse_leave())

        # ===== 下方固定控件区（不随列表滚动）=====
        # 列表按钮
        btn_row = ttkb.Frame(right_panel)
        btn_row.pack(fill=X, pady=(4, 2), padx=4)
        ttkb.Button(btn_row, text="清空", bootstyle="warning-outline", command=self.clear_list).pack(side=LEFT, expand=True, fill=X, padx=(0, 2))
        ttkb.Button(btn_row, text="删除选中", bootstyle="danger-outline", command=self.remove_selected).pack(side=LEFT, expand=True, fill=X, padx=(2, 0))

        # 共享设置
        ttkb.Separator(right_panel).pack(fill=X, pady=1)
        share_lf = ttkb.LabelFrame(right_panel, text="共享设置", padding=4)
        share_lf.pack(fill=X, padx=4, pady=1)
        ttkb.Checkbutton(share_lf, text="共享裁剪大小", variable=self.share_size_var,
                         command=self._on_share_toggle).pack(anchor=W)
        ttkb.Checkbutton(share_lf, text="共享裁剪位置", variable=self.share_pos_var,
                         command=self._on_share_toggle).pack(anchor=W)

        # 变换面板（紧凑布局）
        ttkb.Separator(right_panel).pack(fill=X, pady=1)
        ttkb.Label(right_panel, text="当前图片变换", font=("Microsoft YaHei", 10, "bold")).pack()

        transform_frame = ttkb.Frame(right_panel, padding=4)
        transform_frame.pack(fill=X, pady=(0, 2))

        # 水平翻转和垂直翻转放在同一行
        flip_row = ttkb.Frame(transform_frame)
        flip_row.pack(fill=X)
        ttkb.Checkbutton(flip_row, text="水平翻转", variable=self.flip_h_var,
                         command=self.on_transform_change).pack(side=LEFT, padx=(0, 12))
        ttkb.Checkbutton(flip_row, text="垂直翻转", variable=self.flip_v_var,
                         command=self.on_transform_change).pack(side=LEFT)

        rot_frame = ttkb.Frame(transform_frame)
        rot_frame.pack(fill=X, pady=(4, 0))
        ttkb.Label(rot_frame, text="旋转:").pack(side=LEFT)
        ttkb.Combobox(rot_frame, textvariable=self.rotate_var, values=["0", "90", "180", "270"],
                      state="readonly", width=5).pack(side=LEFT, padx=4)
        ttkb.Label(rot_frame, text="°").pack(side=LEFT)
        self.rotate_var.trace_add("write", lambda *a: self.on_transform_change())

        ttkb.Separator(transform_frame).pack(fill=X, pady=(4, 2))
        ttkb.Button(transform_frame, text="复制变换到所有图片", bootstyle="info-outline",
                    command=self.copy_transform_to_all).pack(fill=X)

        # 导出按钮
        ttkb.Separator(right_panel).pack(fill=X, pady=(2, 0))
        ttkb.Button(right_panel, text="批量裁剪导出",
                    command=self.batch_crop, bootstyle="info").pack(fill=X, padx=4, pady=(2, 4))

        # ===== 底部状态栏 =====
        bot_frame = ttkb.Frame(self.root, padding=(10, 5))
        bot_frame.pack(fill=X)

        self.info_label = ttkb.Label(bot_frame, text="就绪 — 添加图片后拖拽裁剪框调整位置和大小", font=("Microsoft YaHei", 9))
        self.info_label.pack(side=LEFT)

        self.progress = ttkb.Progressbar(bot_frame, mode="determinate", length=180, bootstyle="info")
        self.progress.pack(side=LEFT, padx=15)
        self.progress_label = ttkb.Label(bot_frame, text="", font=("Microsoft YaHei", 9))
        self.progress_label.pack(side=LEFT)

    # ===================== 缩略图管理 =====================
    def _on_thumb_inner_configure(self, event):
        self.thumb_canvas.configure(scrollregion=self.thumb_canvas.bbox("all"))

    def _on_thumb_canvas_configure(self, event):
        self.thumb_canvas.itemconfig(self.thumb_canvas_window, width=event.width)

    def _on_thumb_scroll(self, event):
        self.thumb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.show_thumb_scrollbar()
        self.schedule_hide_thumb_scrollbar()
        return "break"

    def _thumb_mouse_enter(self):
        self.canvas.unbind("<MouseWheel>")
        self.thumb_canvas.bind_all("<MouseWheel>", self._on_thumb_scroll)
        self.show_thumb_scrollbar()

    def _thumb_mouse_leave(self):
        self.thumb_canvas.unbind_all("<MouseWheel>")
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.schedule_hide_thumb_scrollbar()

    # ===================== 滚动条自动隐藏（place 悬浮，不占布局空间）=====================
    def show_thumb_scrollbar(self):
        if self.thumb_sb and not self.thumb_sb.winfo_ismapped():
            bbox = self.thumb_canvas.bbox("all")
            if bbox and bbox[3] > self.thumb_canvas.winfo_height():
                self.thumb_sb.place(relx=1.0, rely=0, relheight=1.0, anchor='ne', width=10)

    def hide_thumb_scrollbar(self):
        if self.thumb_sb and self.thumb_sb.winfo_ismapped():
            self.thumb_sb.place_forget()

    def schedule_hide_thumb_scrollbar(self):
        if self._thumb_scroll_hide_after:
            self.root.after_cancel(self._thumb_scroll_hide_after)
        self._thumb_scroll_hide_after = self.root.after(1000, self.hide_thumb_scrollbar)

    def show_right_scrollbar(self):
        if self.right_sb and not self.right_sb.winfo_ismapped():
            bbox = self.right_canvas.bbox("all")
            if bbox and bbox[3] > self.right_canvas.winfo_height():
                self.right_sb.place(relx=1.0, rely=0, relheight=1.0, anchor='ne', width=10)

    def hide_right_scrollbar(self):
        if self.right_sb and self.right_sb.winfo_ismapped():
            self.right_sb.place_forget()

    def schedule_hide_right_scrollbar(self):
        if self._right_scroll_hide_after:
            self.root.after_cancel(self._right_scroll_hide_after)
        self._right_scroll_hide_after = self.root.after(1000, self.hide_right_scrollbar)

    def _on_right_canvas_configure(self, event):
        self.right_canvas.itemconfig(self.right_canvas_window, width=event.width)
        # 窗口拉伸时同步设置 window 高度，让 thumb_outer 能 expand 填充更多空间
        self.right_canvas.itemconfig(self.right_canvas_window, height=event.height)

    def _on_right_scroll(self, event):
        self.right_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.show_right_scrollbar()
        self.schedule_hide_right_scrollbar()
        return "break"

    def _generate_thumbnail(self, path):
        try:
            img = Image.open(path)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.thumbnail((THUMB_MAX_W, THUMB_MAX_H), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _refresh_thumb_list(self):
        for widget in self.thumb_inner.winfo_children():
            widget.destroy()
        self.thumbnails.clear()

        for i, p in enumerate(self.image_files):
            self._create_thumb_item(i, p)

        self.count_label.config(text=f"共 {len(self.image_files)} 张")

    def _create_thumb_item(self, index, path):
        item_frame = ttkb.Frame(self.thumb_inner, relief="flat", padding=3)
        item_frame.pack(fill=X, padx=2, pady=1)

        thumb_frame = ttkb.Frame(item_frame)
        thumb_frame.pack(side=LEFT, padx=(0, 6))

        thumb = self._generate_thumbnail(path)
        if thumb:
            self.thumbnails[index] = thumb
            lbl = tk.Label(thumb_frame, image=thumb, cursor="hand2", bg="#e0e0e0")
            lbl.image = thumb
            lbl.pack()
            lbl.bind("<Button-1>", lambda e, idx=index: self._select_from_thumb(idx))
        else:
            lbl = tk.Label(thumb_frame, text="?", width=10, height=3, bg="#ddd")
            lbl.pack()

        info_frame = ttkb.Frame(item_frame)
        info_frame.pack(side=LEFT, fill=X, expand=True)

        fname = os.path.basename(path)
        s = self.image_settings.get(index, PerImageSettings())
        tags = []
        if s.flip_h: tags.append("H")
        if s.flip_v: tags.append("V")
        if s.rotate: tags.append(f"{s.rotate}°")
        tag_str = f" [{'+'.join(tags)}]" if tags else ""

        name_lbl = ttkb.Label(info_frame, text=f"{fname}{tag_str}",
                              font=("Microsoft YaHei", 9), cursor="hand2", anchor=W)
        name_lbl.pack(anchor=W, pady=2)
        name_lbl.bind("<Button-1>", lambda e, idx=index: self._select_from_thumb(idx))
        ToolTip(name_lbl, static_text=fname)

        idx_lbl = ttkb.Label(info_frame, text=f"#{index+1}",
                             font=("Microsoft YaHei", 8), bootstyle="secondary")
        idx_lbl.pack(anchor=W)

        item_frame._index = index
        item_frame._name_lbl = name_lbl
        item_frame._thumb_lbl = lbl if thumb else None

    def _select_from_thumb(self, index):
        """从缩略图选择图片（保持裁剪框居中状态）"""
        self.current_index = index
        self.zoom = 1.0
        self._highlight_thumb(index)
        self._sync_transform_vars()
        self._render_canvas()
        self._update_info_bar()

    def select_prev_image(self):
        """选择上一张图片（保持裁剪框居中状态）"""
        if not self.image_files or self.current_index <= 0:
            return
        self.current_index -= 1
        self.zoom = 1.0
        self._highlight_thumb(self.current_index)
        self._sync_transform_vars()
        self._load_current()
        self._update_info_bar()
        self._scroll_thumb_into_view(self.current_index)

    def select_next_image(self):
        """选择下一张图片（保持裁剪框居中状态）"""
        if not self.image_files or self.current_index >= len(self.image_files) - 1:
            return
        self.current_index += 1
        self.zoom = 1.0
        self._highlight_thumb(self.current_index)
        self._sync_transform_vars()
        self._load_current()
        self._update_info_bar()
        self._scroll_thumb_into_view(self.current_index)

    def _scroll_thumb_into_view(self, index):
        children = self.thumb_inner.winfo_children()
        if 0 <= index < len(children):
            widget = children[index]
            self.thumb_canvas.yview_scroll(
                self.thumb_canvas.canvasy(widget.winfo_y()) - self.thumb_canvas.canvasy(0),
                "units"
            )

    def _highlight_thumb(self, selected_index):
        for widget in self.thumb_inner.winfo_children():
            idx = getattr(widget, '_index', -1)
            if idx == selected_index:
                widget.configure(bootstyle="primary")
                widget['relief'] = "solid"
            else:
                widget.configure(bootstyle="default")
                widget['relief'] = "flat"

    def _update_all_thumbs(self):
        children = self.thumb_inner.winfo_children()
        for widget in children:
            idx = getattr(widget, '_index', -1)
            if idx < 0 or idx >= len(self.image_files):
                continue
            s = self.image_settings.get(idx, PerImageSettings())
            tags = []
            if s.flip_h: tags.append("H")
            if s.flip_v: tags.append("V")
            if s.rotate: tags.append(f"{s.rotate}°")
            tag_str = f" [{'+'.join(tags)}]" if tags else ""
            fname = os.path.basename(self.image_files[idx])
            name_lbl = getattr(widget, '_name_lbl', None)
            if name_lbl:
                name_lbl.config(text=f"{fname}{tag_str}")
        self._highlight_thumb(self.current_index)

    # ===================== 文件操作 =====================
    def add_images(self):
        paths = filedialog.askopenfilenames(
            title="选择图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp *.tiff *.tif *.gif"), ("所有文件", "*.*")]
        )
        added = 0
        for p in paths:
            if p not in self.image_files and os.path.splitext(p)[1].lower() in SUPPORTED_FORMATS:
                self.image_files.append(p)
                self.image_settings[len(self.image_files) - 1] = PerImageSettings()
                added += 1
        if added:
            self._init_crop_defaults()
            self._refresh_thumb_list()
            if self.current_index < 0:
                self.current_index = 0
            self._load_current()
            self._highlight_thumb(self.current_index)

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择图片目录")
        if not folder:
            return
        added = 0
        for f in sorted(os.listdir(folder)):
            fp = os.path.join(folder, f)
            if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in SUPPORTED_FORMATS and fp not in self.image_files:
                self.image_files.append(fp)
                self.image_settings[len(self.image_files) - 1] = PerImageSettings()
                added += 1
        if added:
            self._init_crop_defaults()
            self._refresh_thumb_list()
            if self.current_index < 0:
                self.current_index = 0
            self._load_current()
            self._highlight_thumb(self.current_index)

    def _init_crop_defaults(self):
        for i in range(len(self.image_files)):
            s = self.image_settings.get(i)
            if s is None:
                continue
            if s.crop_scale == 1.0 and s.crop_left == 0.0 and s.crop_top == 0.0:
                try:
                    img = Image.open(self.image_files[i])
                    ts = self._apply_transform(img, s)
                    iw, ih = ts.size
                    max_w, max_h = self._get_max_crop_ratios(iw, ih)
                    s.crop_left = (1.0 - max_w * s.crop_scale) / 2
                    s.crop_top = (1.0 - max_h * s.crop_scale) / 2
                    self.image_settings[i] = s
                except Exception:
                    pass

    def clear_list(self):
        self.image_files.clear()
        self.image_settings.clear()
        self.thumbnails.clear()
        self.current_index = -1
        self.original_image = None
        self._crop_centered = False
        for w in self.thumb_inner.winfo_children():
            w.destroy()
        self.count_label.config(text="共 0 张")
        self._render_canvas()

    def remove_selected(self):
        if self.current_index < 0:
            return
        idx = self.current_index
        self.image_files.pop(idx)
        new_settings = {}
        for i in range(len(self.image_files)):
            old_i = i if i < idx else i + 1
            if old_i in self.image_settings:
                new_settings[i] = self.image_settings[old_i]
            else:
                new_settings[i] = PerImageSettings()
        self.image_settings = new_settings
        self._crop_centered = False
        if self.current_index >= len(self.image_files):
            self.current_index = len(self.image_files) - 1
        self._refresh_thumb_list()
        self._load_current()
        self._highlight_thumb(self.current_index)

    # ===================== 图片加载 =====================
    def _load_current(self):
        if not self.image_files or self.current_index < 0:
            self.original_image = None
            self._render_canvas()
            return
        idx = self.current_index
        if idx >= len(self.image_files):
            return
        path = self.image_files[idx]
        try:
            img = Image.open(path)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            self.original_image = img
            self._update_info_bar()
        except Exception as e:
            self.original_image = None
            self.info_label.config(text=f"加载失败: {e}")
        self._render_canvas()

    def _update_info_bar(self):
        if self.original_image is None or self.current_index < 0:
            return
        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size
        left, top, w, h = self._get_crop_rect_rel(s, iw, ih)
        px_w = int(w * iw)
        px_h = int(h * ih)
        fname = os.path.basename(self.image_files[self.current_index])
        self.info_label.config(
            text=f"[{self.current_index+1}/{len(self.image_files)}] {fname}  |  {iw}×{ih} px  |  "
                 f"输出: {px_w}×{px_h} px"
        )

    # ===================== 变换操作 =====================
    def _sync_transform_vars(self):
        s = self._get_current_settings()
        self.flip_h_var.set(s.flip_h)
        self.flip_v_var.set(s.flip_v)
        self.rotate_var.set(str(s.rotate))

    def on_transform_change(self):
        if self.current_index < 0:
            return
        s = self._get_current_settings()
        s.flip_h = self.flip_h_var.get()
        s.flip_v = self.flip_v_var.get()
        try:
            s.rotate = int(self.rotate_var.get())
        except ValueError:
            s.rotate = 0
        self.image_settings[self.current_index] = s
        self._reload_current_image()
        self._update_all_thumbs()
        self._render_canvas()

    def _reload_current_image(self):
        if self.current_index < 0:
            return
        path = self.image_files[self.current_index]
        try:
            img = Image.open(path)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            self.original_image = img
        except Exception:
            pass

    def copy_transform_to_all(self):
        if self.current_index < 0:
            return
        src = self._get_current_settings()
        for i in range(len(self.image_files)):
            ns = self.image_settings.get(i, PerImageSettings())
            ns.flip_h = src.flip_h
            ns.flip_v = src.flip_v
            ns.rotate = src.rotate
            self.image_settings[i] = ns
        self._update_all_thumbs()
        self._render_canvas()

    # ===================== 比例设置 =====================
    def apply_ratio(self):
        try:
            w = float(self.size_w_var.get())
            h = float(self.size_h_var.get())
            if w <= 0 or h <= 0:
                raise ValueError
            self.ratio_w = w
            self.ratio_h = h
            self._recalc_all_crop_positions()
            self._crop_centered = False
            self._render_canvas()
            self._update_info_bar()
        except (ValueError, ZeroDivisionError):
            messagebox.showerror("错误", "请输入有效的正数")

    def _recalc_all_crop_positions(self):
        for i in range(len(self.image_files)):
            s = self.image_settings.get(i, PerImageSettings())
            try:
                img = Image.open(self.image_files[i])
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                ts = self._apply_transform(img, s)
                iw, ih = ts.size
                max_w, max_h = self._get_max_crop_ratios(iw, ih)
                cw = max_w * s.crop_scale
                ch = max_h * s.crop_scale
                s.crop_left = (1.0 - cw) / 2
                s.crop_top = (1.0 - ch) / 2
                self.image_settings[i] = s
            except Exception:
                pass

    # ===================== 画布渲染 =====================
    def on_canvas_resize(self, event):
        self._render_canvas()

    def _render_canvas(self):
        """绘制画布：图片 + 裁剪框遮罩 + 缩放手柄
        居中逻辑：_crop_centered=True 时，每次渲染从当前图片的裁剪框
        相对坐标重新计算居中偏移，确保跨图片切换时各图使用自身裁剪框位置。"""
        self.canvas.delete("all")
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        if self.original_image is None:
            self.canvas.create_text(cw // 2, ch // 2, text="添加图片开始",
                                     fill="#555", font=("Microsoft YaHei", 16))
            return

        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size

        margin = 20
        avail_w = cw - margin * 2
        avail_h = ch - margin * 2
        base_scale = min(avail_w / iw, avail_h / ih)
        self.scale = base_scale * self.zoom

        dw = max(1, int(iw * self.scale))
        dh = max(1, int(ih * self.scale))

        # 计算显示位置：居中模式下，每次从当前图片裁剪框重新计算，跨图片自动适配
        if self._crop_centered:
            left_rel, top_rel, w_rel, h_rel = self._get_crop_rect_rel(s, iw, ih)
            cx = left_rel + w_rel / 2
            cy = top_rel + h_rel / 2
            self.pad_x = int(cw / 2 - cx * dw)
            self.pad_y = int(ch / 2 - cy * dh)
        else:
            self.pad_x = (cw - dw) // 2
            self.pad_y = (ch - dh) // 2

        disp = img.resize((dw, dh), Image.LANCZOS)
        self.display_photo = ImageTk.PhotoImage(disp)
        self.canvas.create_image(self.pad_x, self.pad_y, anchor=tk.NW, image=self.display_photo, tags="img")

        left_rel, top_rel, w_rel, h_rel = self._get_crop_rect_rel(s, iw, ih)
        box_x = self.pad_x + int(left_rel * dw)
        box_y = self.pad_y + int(top_rel * dh)
        box_w = max(1, int(w_rel * dw))
        box_h = max(1, int(h_rel * dh))

        # 遮罩
        if box_y > self.pad_y:
            self.canvas.create_rectangle(self.pad_x, self.pad_y, self.pad_x + dw, box_y,
                                          fill="black", stipple="gray50", outline="")
        box_bottom = box_y + box_h
        if box_bottom < self.pad_y + dh:
            self.canvas.create_rectangle(self.pad_x, box_bottom, self.pad_x + dw, self.pad_y + dh,
                                          fill="black", stipple="gray50", outline="")
        if box_x > self.pad_x:
            self.canvas.create_rectangle(self.pad_x, box_y, box_x, box_bottom,
                                          fill="black", stipple="gray50", outline="")
        box_right = box_x + box_w
        if box_right < self.pad_x + dw:
            self.canvas.create_rectangle(box_right, box_y, self.pad_x + dw, box_bottom,
                                          fill="black", stipple="gray50", outline="")

        self.canvas.create_rectangle(box_x, box_y, box_right, box_bottom,
                                      outline="#00ff88", width=2)

        hs = HANDLE_SIZE
        corners = [
            (box_x, box_y),
            (box_x + box_w, box_y),
            (box_x, box_bottom),
            (box_x + box_w, box_bottom),
        ]
        for cx, cy in corners:
            self.canvas.create_rectangle(cx - hs, cy - hs, cx + hs, cy + hs,
                                          fill="#00ff88", outline="#005522", width=1)

        px_w = int(w_rel * iw)
        px_h = int(h_rel * ih)
        info_text = f"输出: {px_w}×{px_h} px"
        cx_text = box_x + box_w // 2
        cy_text = box_y - 10
        if cy_text < self.pad_y + 15:
            cy_text = box_bottom + 15
            anchor = tk.N
        else:
            anchor = tk.S
        self.canvas.create_text(cx_text, cy_text, text=info_text, fill="#00ff88",
                                 font=("Consolas", 10, "bold"), anchor=anchor)

    # ===================== 鼠标交互 =====================
    def _hit_test(self, mx, my):
        if self.original_image is None:
            return None
        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size
        left_rel, top_rel, w_rel, h_rel = self._get_crop_rect_rel(s, iw, ih)

        dw = max(1, int(iw * self.scale))
        dh = max(1, int(ih * self.scale))

        box_x = self.pad_x + int(left_rel * dw)
        box_y = self.pad_y + int(top_rel * dh)
        box_w = max(1, int(w_rel * dw))
        box_h = max(1, int(h_rel * dh))
        box_right = box_x + box_w
        box_bottom = box_y + box_h

        hs = HANDLE_SIZE + 4

        if abs(mx - box_x) < hs and abs(my - box_y) < hs:
            return 'nw'
        if abs(mx - box_right) < hs and abs(my - box_y) < hs:
            return 'ne'
        if abs(mx - box_x) < hs and abs(my - box_bottom) < hs:
            return 'sw'
        if abs(mx - box_right) < hs and abs(my - box_bottom) < hs:
            return 'se'

        if box_x <= mx <= box_right and box_y <= my <= box_bottom:
            return 'move'

        return None

    def on_mouse_down(self, event):
        if self.original_image is None:
            return
        hit = self._hit_test(event.x, event.y)
        if hit is None:
            return

        self.drag_mode = hit
        self.drag_start_x = event.x
        self.drag_start_y = event.y

        s = self._get_current_settings()
        self.drag_init_left = s.crop_left
        self.drag_init_top = s.crop_top
        self.drag_init_scale = s.crop_scale

    def on_mouse_drag(self, event):
        if not self.drag_mode or self.original_image is None:
            return

        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size
        dw = max(1, int(iw * self.scale))
        dh = max(1, int(ih * self.scale))

        dx = (event.x - self.drag_start_x) / dw if dw > 0 else 0
        dy = (event.y - self.drag_start_y) / dh if dh > 0 else 0

        if self.drag_mode == 'move':
            max_w, max_h = self._get_max_crop_ratios(iw, ih)
            cw = max_w * s.crop_scale
            ch = max_h * s.crop_scale
            s.crop_left = max(0, min(self.drag_init_left + dx, 1.0 - cw))
            s.crop_top = max(0, min(self.drag_init_top + dy, 1.0 - ch))
            self.image_settings[self.current_index] = s
            self._sync_shared('position')

        elif self.drag_mode in ('se', 'sw', 'ne', 'nw'):
            max_w, max_h = self._get_max_crop_ratios(iw, ih)
            if max_w <= 0 or max_h <= 0:
                return

            if self.drag_mode == 'se':
                new_w_rel = (self.drag_init_left + max_w * self.drag_init_scale) + dx - self.drag_init_left
            elif self.drag_mode == 'sw':
                new_w_rel = max_w * self.drag_init_scale - dx
            elif self.drag_mode == 'ne':
                new_w_rel = (self.drag_init_left + max_w * self.drag_init_scale) + dx - self.drag_init_left
            elif self.drag_mode == 'nw':
                new_w_rel = max_w * self.drag_init_scale - dx
            else:
                new_w_rel = max_w * self.drag_init_scale

            if max_w > 0:
                new_scale = new_w_rel / max_w
            else:
                new_scale = self.drag_init_scale

            new_scale = max(0.1, min(1.0, new_scale))
            s.crop_scale = new_scale

            actual_w = max_w * new_scale
            actual_h = max_h * new_scale

            if self.drag_mode == 'se':
                s.crop_left = self.drag_init_left
                s.crop_top = self.drag_init_top
            elif self.drag_mode == 'sw':
                old_right = self.drag_init_left + max_w * self.drag_init_scale
                s.crop_left = old_right - actual_w
                s.crop_top = self.drag_init_top
            elif self.drag_mode == 'ne':
                s.crop_left = self.drag_init_left
                old_bottom = self.drag_init_top + max_h * self.drag_init_scale
                s.crop_top = old_bottom - actual_h
            elif self.drag_mode == 'nw':
                old_right = self.drag_init_left + max_w * self.drag_init_scale
                old_bottom = self.drag_init_top + max_h * self.drag_init_scale
                s.crop_left = old_right - actual_w
                s.crop_top = old_bottom - actual_h

            s.crop_left = max(0, min(s.crop_left, 1.0 - actual_w))
            s.crop_top = max(0, min(s.crop_top, 1.0 - actual_h))

            self.image_settings[self.current_index] = s
            self._sync_shared('size')
            if self.share_pos_var.get():
                self._sync_shared('position')

        self._render_canvas()
        self._update_info_bar()

    def on_mouse_up(self, event):
        if self.drag_mode and self.original_image is not None:
            self._crop_centered = True
            self._render_canvas()
            self._update_info_bar()
        self.drag_mode = None

    def on_mouse_wheel(self, event):
        if self.original_image is None:
            return
        if event.delta > 0:
            self.zoom = min(self.zoom * 1.15, 5.0)
        else:
            self.zoom = max(self.zoom / 1.15, 0.3)
        self._render_canvas()

    def nudge(self, dx, dy):
        if self.original_image is None or self.current_index < 0:
            return
        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size

        max_w, max_h = self._get_max_crop_ratios(iw, ih)
        cw = max_w * s.crop_scale
        ch = max_h * s.crop_scale

        rx = dx / iw if iw > 0 else 0
        ry = dy / ih if ih > 0 else 0

        s.crop_left = max(0, min(s.crop_left + rx, 1.0 - cw))
        s.crop_top = max(0, min(s.crop_top + ry, 1.0 - ch))
        self.image_settings[self.current_index] = s
        self._sync_shared('position')
        self._crop_centered = False
        self._render_canvas()
        self._update_info_bar()

    # ===================== 列表选择 =====================
    def browse_output(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.output_dir_var.set(d)

    # ===================== 批量裁剪 =====================
    def batch_crop(self):
        if not self.image_files:
            messagebox.showwarning("提示", "请先添加图片")
            return

        out_dir = self.output_dir_var.get().strip()
        if not out_dir:
            out_dir = filedialog.askdirectory(title="选择输出目录")
            if not out_dir:
                return
            self.output_dir_var.set(out_dir)
        os.makedirs(out_dir, exist_ok=True)

        try:
            rw = float(self.size_w_var.get())
            rh = float(self.size_h_var.get())
            if rw <= 0 or rh <= 0:
                raise ValueError
            self.ratio_w = rw
            self.ratio_h = rh
        except ValueError:
            messagebox.showerror("错误", "裁剪比例无效")
            return

        total = len(self.image_files)
        success = 0
        errors = []
        suffix = self.suffix_var.get()

        for i, path in enumerate(self.image_files):
            try:
                img = Image.open(path)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')

                s = self.image_settings.get(i, PerImageSettings())
                img = self._apply_transform(img, s)
                iw, ih = img.size

                left_rel, top_rel, w_rel, h_rel = self._get_crop_rect_rel(s, iw, ih)
                left = int(left_rel * iw)
                top = int(top_rel * ih)
                right = int((left_rel + w_rel) * iw)
                bottom = int((top_rel + h_rel) * ih)

                right = min(right, iw)
                bottom = min(bottom, ih)

                cropped = img.crop((left, top, right, bottom))

                base, ext = os.path.splitext(os.path.basename(path))
                out_name = f"{base}{suffix}{ext}"
                out_path = os.path.join(out_dir, out_name)
                counter = 1
                while os.path.exists(out_path):
                    out_name = f"{base}{suffix}_{counter}{ext}"
                    out_path = os.path.join(out_dir, out_name)
                    counter += 1

                if ext.lower() in ('.jpg', '.jpeg'):
                    cropped.save(out_path, quality=100, subsampling=0)
                elif ext.lower() == '.png':
                    cropped.save(out_path, compress_level=6)
                else:
                    cropped.save(out_path)

                success += 1
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")

            pct = (i + 1) / total * 100
            self.progress['value'] = pct
            self.progress_label.config(text=f"{i+1}/{total}")
            self.root.update_idletasks()

        self.progress['value'] = 100
        msg = f"完成！成功裁剪 {success}/{total} 张\n输出: {out_dir}\n\n零压缩，直接截取原图像素。"
        if errors:
            msg += f"\n\n失败 {len(errors)} 张:\n" + "\n".join(errors[:10])
        self.info_label.config(text=f"完成: {success}/{total}")
        messagebox.showinfo("批量裁剪完成", msg)


def main():
    root = ttkb.Window(themename="flatly")
    app = BatchImageCrop(root)
    root.mainloop()


if __name__ == "__main__":
    main()
