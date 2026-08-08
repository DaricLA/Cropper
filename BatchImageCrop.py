"""
MBO/PBO批量图片剪裁工具 v2.5
- 批量加载图片，缩略图预览列表（左右排布节省空间）
- 裁剪框固定比例、大小可调（拖拽角落缩放）
- 每张图独立裁剪位置/大小，可选共享
- 单图独立翻转/旋转，实时预览
- 零压缩无损输出，直接截取原图像素
- 批量重命名（序号/替换/模板/插入删除/大小写），实时预览+冲突检测+撤销
- 绿色免安装，ttkbootstrap flatly 主题
- 键盘上下键切换图片，滚轮独立控制列表/预览
- 支持拖拽图片文件到裁剪区或列表区直接导入
"""

import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox
import ttkbootstrap as ttkb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk
import os
import re
from datetime import datetime

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tiff', '.tif', '.gif')
THUMB_MAX_W = 100
THUMB_MAX_H = 70
HANDLE_SIZE = 8


class ToolTip:
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
        self.root.title("MBO/PBO批量图片剪裁工具 v2.5")
        self.root.geometry("1500x950")
        self.root.minsize(1500, 950)

        # --- 数据 ---
        self.image_files = []
        self.image_settings = {}
        self.thumbnails = {}
        self.current_index = -1
        self.original_image = None
        self.display_photo = None

        self.ratio_w = 4
        self.ratio_h = 3

        self.scale = 1.0
        self.zoom = 1.0
        self.pad_x = 0
        self.pad_y = 0

        self.drag_mode = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_init_left = 0.0
        self.drag_init_top = 0.0
        self.drag_init_scale = 1.0

        self.size_w_var = tk.StringVar(value="8")
        self.size_h_var = tk.StringVar(value="6")
        self.output_dir_var = tk.StringVar()
        self.suffix_var = tk.StringVar(value="")
        self.flip_h_var = tk.BooleanVar(value=False)
        self.flip_v_var = tk.BooleanVar(value=False)
        self.rotate_var = tk.StringVar(value="0")

        self.share_size_var = tk.BooleanVar(value=True)
        self.share_pos_var = tk.BooleanVar(value=False)

        self._thumb_scroll_hide_after = None
        self._right_scroll_hide_after = None
        self._crop_centered = False

        # --- 重命名相关 ---
        self.rename_mode_var = tk.StringVar(value="sequential")
        self.rename_prefix_var = tk.StringVar(value="")
        self.rename_start_var = tk.StringVar(value="1")
        self.rename_digits_var = tk.StringVar(value="3")
        self.rename_find_var = tk.StringVar(value="")
        self.rename_replace_var = tk.StringVar(value="")
        self.rename_regex_var = tk.BooleanVar(value=False)
        self.rename_template_var = tk.StringVar(value="{name}_{num:03d}")
        self.rename_insert_pos_var = tk.StringVar(value="0")
        self.rename_insert_text_var = tk.StringVar(value="")
        self.rename_case_var = tk.StringVar(value="lower")
        self.rename_undo_stack = []

        self.build_ui()

        if HAS_DND:
            self._setup_dnd()

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

        # ===== Notebook 标签页 =====
        self.notebook = ttkb.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True, padx=10, pady=5)

        # --- Tab 1: 裁剪 ---
        crop_tab = ttkb.Frame(self.notebook)
        self.notebook.add(crop_tab, text="  裁剪  ")
        self._build_crop_tab(crop_tab)

        # --- Tab 2: 重命名 ---
        rename_tab = ttkb.Frame(self.notebook)
        self.notebook.add(rename_tab, text="  重命名  ")
        self._build_rename_tab(rename_tab)

        # 切换标签时刷新重命名列表
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ===== 底部状态栏 =====
        bot_frame = ttkb.Frame(self.root, padding=(10, 5))
        bot_frame.pack(fill=X)

        self.info_label = ttkb.Label(bot_frame, text="就绪 — 添加图片后拖拽裁剪框调整位置和大小", font=("Microsoft YaHei", 9))
        self.info_label.pack(side=LEFT)

        self.progress = ttkb.Progressbar(bot_frame, mode="determinate", length=180, bootstyle="info")
        self.progress.pack(side=LEFT, padx=15)
        self.progress_label = ttkb.Label(bot_frame, text="", font=("Microsoft YaHei", 9))
        self.progress_label.pack(side=LEFT)

    def _build_crop_tab(self, parent):
        """构建裁剪标签页"""
        mid_frame = ttkb.Frame(parent)
        mid_frame.pack(fill=BOTH, expand=True)

        canvas_frame = ttkb.Frame(mid_frame)
        canvas_frame.pack(fill=BOTH, expand=True, side=LEFT)

        self.canvas = tk.Canvas(canvas_frame, bg="#1a1a2e", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)

        right_panel = ttkb.Frame(mid_frame, width=260)
        right_panel.pack(fill=Y, side=LEFT, padx=(8, 0))
        right_panel.pack_propagate(False)

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

        btn_row = ttkb.Frame(right_panel)
        btn_row.pack(fill=X, pady=(4, 2), padx=4)
        ttkb.Button(btn_row, text="清空", bootstyle="warning-outline", command=self.clear_list).pack(side=LEFT, expand=True, fill=X, padx=(0, 2))
        ttkb.Button(btn_row, text="删除选中", bootstyle="danger-outline", command=self.remove_selected).pack(side=LEFT, expand=True, fill=X, padx=(2, 0))

        ttkb.Separator(right_panel).pack(fill=X, pady=1)
        share_lf = ttkb.LabelFrame(right_panel, text="共享设置", padding=4)
        share_lf.pack(fill=X, padx=4, pady=1)
        ttkb.Checkbutton(share_lf, text="共享裁剪大小", variable=self.share_size_var,
                         command=self._on_share_toggle).pack(anchor=W)
        ttkb.Checkbutton(share_lf, text="共享裁剪位置", variable=self.share_pos_var,
                         command=self._on_share_toggle).pack(anchor=W)

        ttkb.Separator(right_panel).pack(fill=X, pady=1)
        ttkb.Label(right_panel, text="当前图片变换", font=("Microsoft YaHei", 10, "bold")).pack()

        transform_frame = ttkb.Frame(right_panel, padding=4)
        transform_frame.pack(fill=X, pady=(0, 2))

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

        ttkb.Separator(right_panel).pack(fill=X, pady=(2, 0))
        ttkb.Button(right_panel, text="批量裁剪导出",
                    command=self.batch_crop, bootstyle="info").pack(fill=X, padx=4, pady=(2, 4))

    # ===================== 重命名 Tab =====================
    def _build_rename_tab(self, parent):
        """构建重命名标签页"""
        # 工具栏
        toolbar = ttkb.Frame(parent, padding=(6, 4))
        toolbar.pack(fill=X)
        ttkb.Button(toolbar, text="添加文件", bootstyle="success", command=self._rename_add_files).pack(side=LEFT, padx=2)
        ttkb.Button(toolbar, text="添加目录", bootstyle="success-outline", command=self._rename_add_folder).pack(side=LEFT, padx=2)
        ttkb.Button(toolbar, text="清空列表", bootstyle="warning-outline", command=self._rename_clear).pack(side=LEFT, padx=2)
        ttkb.Button(toolbar, text="撤销上次", bootstyle="secondary-outline", command=self._rename_undo).pack(side=LEFT, padx=2)
        self.rename_count_label = ttkb.Label(toolbar, text="共 0 个文件", bootstyle="secondary")
        self.rename_count_label.pack(side=LEFT, padx=10)

        # 中间：左右分栏
        main_area = ttkb.Frame(parent)
        main_area.pack(fill=BOTH, expand=True, padx=6, pady=2)

        # 左栏：原文件名列表
        left_frame = ttkb.LabelFrame(main_area, text="原文件名", padding=4)
        left_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 4))

        list_frame = ttkb.Frame(left_frame)
        list_frame.pack(fill=BOTH, expand=True)

        self.rename_listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED,
                                          font=("Microsoft YaHei", 9), activestyle="none",
                                          exportselection=False)
        rename_list_sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.rename_listbox.yview)
        self.rename_listbox.configure(yscrollcommand=rename_list_sb.set)
        self.rename_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        rename_list_sb.pack(side=LEFT, fill=Y)

        # 排序按钮
        sort_frame = ttkb.Frame(left_frame)
        sort_frame.pack(fill=X, pady=(4, 0))
        ttkb.Button(sort_frame, text="▲ 上移", bootstyle="secondary-outline", command=self._rename_move_up).pack(side=LEFT, padx=2, expand=True, fill=X)
        ttkb.Button(sort_frame, text="▼ 下移", bootstyle="secondary-outline", command=self._rename_move_down).pack(side=LEFT, padx=2, expand=True, fill=X)
        ttkb.Button(sort_frame, text="按名称排序", bootstyle="secondary-outline", command=self._rename_sort_by_name).pack(side=LEFT, padx=2, expand=True, fill=X)

        # 右栏：新文件名预览
        right_frame = ttkb.LabelFrame(main_area, text="新文件名预览（红色=冲突）", padding=4)
        right_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(4, 0))

        preview_frame = ttkb.Frame(right_frame)
        preview_frame.pack(fill=BOTH, expand=True)

        self.rename_preview = tk.Text(preview_frame, font=("Consolas", 9), state=tk.DISABLED,
                                       wrap=tk.NONE, bg="#fafafa", relief="solid", borderwidth=1)
        preview_pb = ttk.Scrollbar(preview_frame, orient="vertical", command=self.rename_preview.yview)
        self.rename_preview.configure(yscrollcommand=preview_pb.set)
        self.rename_preview.pack(side=LEFT, fill=BOTH, expand=True)
        preview_pb.pack(side=LEFT, fill=Y)

        # 底部：模式选择 + 参数
        bottom_frame = ttkb.LabelFrame(parent, text="重命名规则", padding=6)
        bottom_frame.pack(fill=X, padx=6, pady=(2, 4))

        # 模式选择
        mode_row = ttkb.Frame(bottom_frame)
        mode_row.pack(fill=X, pady=(0, 6))
        ttkb.Label(mode_row, text="模式:").pack(side=LEFT, padx=(0, 8))
        modes = [("序号", "sequential"), ("替换", "replace"), ("模板", "template"),
                 ("插入/删除", "insert"), ("大小写", "case")]
        for text, val in modes:
            ttkb.Radiobutton(mode_row, text=text, variable=self.rename_mode_var, value=val,
                             command=self._on_rename_mode_change).pack(side=LEFT, padx=6)

        # 序号模式参数
        self.seq_frame = ttkb.Frame(bottom_frame)
        self._build_seq_params(self.seq_frame)

        # 替换模式参数
        self.rep_frame = ttkb.Frame(bottom_frame)
        self._build_replace_params(self.rep_frame)

        # 模板模式参数
        self.tpl_frame = ttkb.Frame(bottom_frame)
        self._build_template_params(self.tpl_frame)

        # 插入/删除模式参数
        self.ins_frame = ttkb.Frame(bottom_frame)
        self._build_insert_params(self.ins_frame)

        # 大小写模式参数
        self.cas_frame = ttkb.Frame(bottom_frame)
        self._build_case_params(self.cas_frame)

        # 操作按钮
        action_row = ttkb.Frame(bottom_frame)
        action_row.pack(fill=X, pady=(8, 0))
        ttkb.Button(action_row, text="预览", bootstyle="info-outline", command=self._rename_preview_refresh).pack(side=LEFT, padx=4)
        ttkb.Button(action_row, text="执行重命名", bootstyle="info", command=self._rename_execute).pack(side=LEFT, padx=4)
        self.rename_status_label = ttkb.Label(action_row, text="", font=("Microsoft YaHei", 9))
        self.rename_status_label.pack(side=LEFT, padx=10)

        self._on_rename_mode_change()

    def _build_seq_params(self, parent):
        ttkb.Label(parent, text="前缀:").pack(side=LEFT, padx=(0, 2))
        ttkb.Entry(parent, textvariable=self.rename_prefix_var, width=12).pack(side=LEFT, padx=(0, 10))
        ttkb.Label(parent, text="起始:").pack(side=LEFT, padx=(0, 2))
        ttkb.Entry(parent, textvariable=self.rename_start_var, width=5).pack(side=LEFT, padx=(0, 10))
        ttkb.Label(parent, text="位数:").pack(side=LEFT, padx=(0, 2))
        ttkb.Entry(parent, textvariable=self.rename_digits_var, width=3).pack(side=LEFT, padx=(0, 10))
        ttkb.Label(parent, text="(不足补零)", bootstyle="secondary").pack(side=LEFT)

    def _build_replace_params(self, parent):
        ttkb.Label(parent, text="查找:").pack(side=LEFT, padx=(0, 2))
        ttkb.Entry(parent, textvariable=self.rename_find_var, width=14).pack(side=LEFT, padx=(0, 10))
        ttkb.Label(parent, text="替换为:").pack(side=LEFT, padx=(0, 2))
        ttkb.Entry(parent, textvariable=self.rename_replace_var, width=14).pack(side=LEFT, padx=(0, 10))
        ttkb.Checkbutton(parent, text="正则", variable=self.rename_regex_var).pack(side=LEFT)

    def _build_template_params(self, parent):
        ttkb.Label(parent, text="模板:").pack(side=LEFT, padx=(0, 2))
        ttkb.Entry(parent, textvariable=self.rename_template_var, width=30).pack(side=LEFT, padx=(0, 10))
        ttkb.Label(parent, text="{name}原文件名 {num}序号 {ext}扩展名 {date}日期", bootstyle="secondary").pack(side=LEFT)

    def _build_insert_params(self, parent):
        ttkb.Label(parent, text="位置:").pack(side=LEFT, padx=(0, 2))
        ttkb.Entry(parent, textvariable=self.rename_insert_pos_var, width=4).pack(side=LEFT, padx=(0, 4))
        ttkb.Label(parent, text="(0=开头, -1=扩展名前)", bootstyle="secondary").pack(side=LEFT, padx=(0, 10))
        ttkb.Label(parent, text="文本:").pack(side=LEFT, padx=(0, 2))
        ttkb.Entry(parent, textvariable=self.rename_insert_text_var, width=14).pack(side=LEFT)

    def _build_case_params(self, parent):
        modes = [("全小写", "lower"), ("全大写", "upper"), ("首字母大写", "title"),
                 ("扩展名统一小写", "ext_lower"), ("扩展名统一大写", "ext_upper")]
        for text, val in modes:
            ttkb.Radiobutton(parent, text=text, variable=self.rename_case_var, value=val).pack(side=LEFT, padx=6)

    def _on_rename_mode_change(self):
        """切换重命名模式时显示对应参数面板"""
        for f in (self.seq_frame, self.rep_frame, self.tpl_frame, self.ins_frame, self.cas_frame):
            f.pack_forget()
        mode = self.rename_mode_var.get()
        mapping = {"sequential": self.seq_frame, "replace": self.rep_frame,
                   "template": self.tpl_frame, "insert": self.ins_frame, "case": self.cas_frame}
        target = mapping.get(mode)
        if target:
            target.pack(fill=X, pady=2)
        self._rename_preview_refresh()

    def _on_tab_changed(self, event):
        """切换标签时刷新"""
        self._rename_refresh_list()
        self._rename_preview_refresh()

    # ===================== 重命名列表操作 =====================
    def _rename_refresh_list(self):
        """刷新重命名列表（共享 image_files）"""
        self.rename_listbox.delete(0, tk.END)
        for p in self.image_files:
            self.rename_listbox.insert(tk.END, os.path.basename(p))
        self.rename_count_label.config(text=f"共 {len(self.image_files)} 个文件")

    def _rename_add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择文件",
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
            self._rename_refresh_list()
            if self.current_index < 0:
                self.current_index = 0
                self._load_current()
                self._highlight_thumb(self.current_index)

    def _rename_add_folder(self):
        folder = filedialog.askdirectory(title="选择目录")
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
            self._rename_refresh_list()
            if self.current_index < 0:
                self.current_index = 0
                self._load_current()
                self._highlight_thumb(self.current_index)

    def _rename_clear(self):
        if not self.image_files:
            return
        if not messagebox.askyesno("确认", "确定清空所有文件？这也会清空裁剪列表。"):
            return
        self.image_files.clear()
        self.image_settings.clear()
        self.thumbnails.clear()
        self.current_index = -1
        self.original_image = None
        self._crop_centered = False
        for w in self.thumb_inner.winfo_children():
            w.destroy()
        self.count_label.config(text="共 0 张")
        self._rename_refresh_list()
        self._rename_preview_refresh()
        self._render_canvas()

    def _rename_move_up(self):
        sel = self.rename_listbox.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        self.image_files[idx], self.image_files[idx - 1] = self.image_files[idx - 1], self.image_files[idx]
        s = self.image_settings.pop(idx, None)
        s_prev = self.image_settings.pop(idx - 1, None)
        if s is not None:
            self.image_settings[idx - 1] = s
        if s_prev is not None:
            self.image_settings[idx] = s_prev
        self._rename_refresh_list()
        self._rename_preview_refresh()
        self.rename_listbox.selection_set(idx - 1)

    def _rename_move_down(self):
        sel = self.rename_listbox.curselection()
        if not sel or sel[0] >= len(self.image_files) - 1:
            return
        idx = sel[0]
        self.image_files[idx], self.image_files[idx + 1] = self.image_files[idx + 1], self.image_files[idx]
        s = self.image_settings.pop(idx, None)
        s_next = self.image_settings.pop(idx + 1, None)
        if s is not None:
            self.image_settings[idx + 1] = s
        if s_next is not None:
            self.image_settings[idx] = s_next
        self._rename_refresh_list()
        self._rename_preview_refresh()
        self.rename_listbox.selection_set(idx + 1)

    def _rename_sort_by_name(self):
        if not self.image_files:
            return
        paired = sorted(enumerate(self.image_files), key=lambda x: os.path.basename(x[1]).lower())
        new_order = [p[1] for p in paired]
        new_settings = {}
        for new_i, (old_i, _) in enumerate(paired):
            if old_i in self.image_settings:
                new_settings[new_i] = self.image_settings[old_i]
        self.image_files = new_order
        self.image_settings = new_settings
        self.current_index = -1
        self._refresh_thumb_list()
        self._rename_refresh_list()
        self._rename_preview_refresh()

    # ===================== 重命名逻辑 =====================
    def _rename_get_new_names(self):
        """根据当前模式生成新文件名列表，返回 [(old_path, new_name, has_conflict), ...]"""
        mode = self.rename_mode_var.get()
        results = []

        if mode == "sequential":
            try:
                start = int(self.rename_start_var.get())
                digits = int(self.rename_digits_var.get())
            except ValueError:
                return []
            prefix = self.rename_prefix_var.get()
            for i, p in enumerate(self.image_files):
                _, ext = os.path.splitext(p)
                num_str = f"{start + i:0{digits}d}"
                new_name = f"{prefix}{num_str}{ext}"
                results.append((p, new_name, False))

        elif mode == "replace":
            find = self.rename_find_var.get()
            repl = self.rename_replace_var.get()
            use_regex = self.rename_regex_var.get()
            for p in self.image_files:
                base, ext = os.path.splitext(os.path.basename(p))
                if use_regex:
                    try:
                        new_base = re.sub(find, repl, base)
                    except re.error:
                        new_base = base
                else:
                    new_base = base.replace(find, repl)
                results.append((p, new_base + ext, False))

        elif mode == "template":
            tmpl = self.rename_template_var.get()
            for i, p in enumerate(self.image_files):
                base, ext = os.path.splitext(os.path.basename(p))
                num_str = f"{i + 1:03d}"
                name = tmpl.replace("{name}", base).replace("{num}", num_str)
                name = name.replace("{ext}", ext[1:]).replace("{date}", datetime.now().strftime("%Y%m%d"))
                if not name.endswith(ext) and "." not in name.split("/")[-1].split("\\")[-1]:
                    name += ext
                results.append((p, name, False))

        elif mode == "insert":
            try:
                pos = int(self.rename_insert_pos_var.get())
            except ValueError:
                pos = 0
            text = self.rename_insert_text_var.get()
            for p in self.image_files:
                base, ext = os.path.splitext(os.path.basename(p))
                if pos == -1:
                    new_name = base + text + ext
                else:
                    new_name = base[:pos] + text + base[pos:] + ext
                results.append((p, new_name, False))

        elif mode == "case":
            case = self.rename_case_var.get()
            for p in self.image_files:
                base, ext = os.path.splitext(os.path.basename(p))
                if case == "lower":
                    new_base, new_ext = base.lower(), ext.lower()
                elif case == "upper":
                    new_base, new_ext = base.upper(), ext.upper()
                elif case == "title":
                    new_base, new_ext = base.title(), ext.lower()
                elif case == "ext_lower":
                    new_base, new_ext = base, ext.lower()
                elif case == "ext_upper":
                    new_base, new_ext = base, ext.upper()
                else:
                    new_base, new_ext = base, ext
                results.append((p, new_base + new_ext, False))

        # 冲突检测
        new_names_seen = {}
        for i, (p, name, _) in enumerate(results):
            old_dir = os.path.dirname(p)
            full_new = os.path.join(old_dir, name)
            conflict = False
            if name in new_names_seen:
                conflict = True
            elif os.path.exists(full_new) and old_dir != full_new:
                conflict = True
            new_names_seen[name] = True
            results[i] = (p, name, conflict)

        return results

    def _rename_preview_refresh(self):
        """刷新预览区"""
        results = self._rename_get_new_names()
        self.rename_preview.configure(state=tk.NORMAL)
        self.rename_preview.delete("1.0", tk.END)
        self.rename_preview.tag_configure("conflict", foreground="red")
        self.rename_preview.tag_configure("normal", foreground="#333")

        for p, name, conflict in results:
            old_name = os.path.basename(p)
            tag = "conflict" if conflict else "normal"
            self.rename_preview.insert(tk.END, f"{old_name}\n", ("normal",))
            self.rename_preview.insert(tk.END, f"  → {name}\n", (tag,))
            if conflict:
                self.rename_preview.insert(tk.END, f"  ⚠ 冲突！\n", ("conflict",))

        self.rename_preview.configure(state=tk.DISABLED)
        conflict_count = sum(1 for _, _, c in results if c)
        self.rename_status_label.config(
            text=f"共 {len(results)} 个文件" + (f"，{conflict_count} 个冲突" if conflict_count else "，无冲突"))

    def _rename_execute(self):
        """执行重命名"""
        if not self.image_files:
            messagebox.showwarning("提示", "没有文件")
            return

        results = self._rename_get_new_names()
        if not results:
            return

        conflict_count = sum(1 for _, _, c in results if c)
        if conflict_count:
            if not messagebox.askyesno("冲突警告", f"有 {conflict_count} 个文件存在冲突，是否跳过冲突文件继续？"):
                return

        # 保存撤销信息
        undo_info = []
        renamed = 0
        skipped = 0
        errors = []

        for old_path, new_name, conflict in results:
            if conflict:
                skipped += 1
                continue
            old_dir = os.path.dirname(old_path)
            new_path = os.path.join(old_dir, new_name)
            try:
                os.rename(old_path, new_path)
                undo_info.append((new_path, old_path))
                renamed += 1
            except Exception as e:
                errors.append(f"{os.path.basename(old_path)}: {e}")

        # 更新 image_files 中的路径
        name_map = {}
        for old_path, new_name, conflict in results:
            if not conflict:
                old_dir = os.path.dirname(old_path)
                name_map[old_path] = os.path.join(old_dir, new_name)
        self.image_files = [name_map.get(p, p) for p in self.image_files]

        self.rename_undo_stack.append(undo_info)

        self._rename_refresh_list()
        self._rename_preview_refresh()
        self._refresh_thumb_list()
        if self.current_index >= 0:
            self._load_current()

        msg = f"完成！重命名 {renamed} 个，跳过 {skipped} 个冲突"
        if errors:
            msg += f"\n失败 {len(errors)}:\n" + "\n".join(errors[:5])
        self.rename_status_label.config(text=msg)
        messagebox.showinfo("重命名完成", msg)

    def _rename_undo(self):
        """撤销上次重命名"""
        if not self.rename_undo_stack:
            messagebox.showinfo("提示", "没有可撤销的操作")
            return
        if not messagebox.askyesno("确认", "确定撤销上次重命名操作？"):
            return

        undo_info = self.rename_undo_stack.pop()
        reverted = 0
        errors = []
        name_map = {}
        for new_path, old_path in undo_info:
            try:
                os.rename(new_path, old_path)
                name_map[new_path] = old_path
                reverted += 1
            except Exception as e:
                errors.append(f"{os.path.basename(new_path)}: {e}")

        self.image_files = [name_map.get(p, p) for p in self.image_files]

        self._rename_refresh_list()
        self._rename_preview_refresh()
        self._refresh_thumb_list()
        if self.current_index >= 0:
            self._load_current()

        msg = f"已撤销 {reverted} 个重命名"
        if errors:
            msg += f"\n失败 {len(errors)}:\n" + "\n".join(errors[:5])
        self.rename_status_label.config(text=msg)
        messagebox.showinfo("撤销完成", msg)

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
        self.current_index = index
        self.zoom = 1.0
        self._highlight_thumb(index)
        self._sync_transform_vars()
        self._render_canvas()
        self._update_info_bar()

    def select_prev_image(self):
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
                self.thumb_canvas.canvasy(widget.winfo_y()) - self.thumb_canvas.canvasy(0), "units")

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

    # ===================== 拖拽导入 =====================
    def _setup_dnd(self):
        self.canvas.drop_target_register(DND_FILES)
        self.canvas.dnd_bind('<<Drop>>', self._on_dnd_drop)
        self.right_canvas.drop_target_register(DND_FILES)
        self.right_canvas.dnd_bind('<<Drop>>', self._on_dnd_drop)
        self.thumb_canvas.drop_target_register(DND_FILES)
        self.thumb_canvas.dnd_bind('<<Drop>>', self._on_dnd_drop)

    def _parse_dnd_files(self, data):
        files = re.findall(r'\{([^}]*)\}|(\S+)', data)
        result = []
        for f in files:
            path = f[0] if f[0] else f[1]
            if os.path.isfile(path):
                result.append(path)
        return result

    def _on_dnd_drop(self, event):
        files = self._parse_dnd_files(event.data)
        added = 0
        for p in files:
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
                 f"输出: {px_w}×{px_h} px")

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

    def on_canvas_resize(self, event):
        self._render_canvas()

    def _render_canvas(self):
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
        corners = [(box_x, box_y), (box_x + box_w, box_y), (box_x, box_bottom), (box_x + box_w, box_bottom)]
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
        if abs(mx - box_x) < hs and abs(my - box_y) < hs: return 'nw'
        if abs(mx - box_right) < hs and abs(my - box_y) < hs: return 'ne'
        if abs(mx - box_x) < hs and abs(my - box_bottom) < hs: return 'sw'
        if abs(mx - box_right) < hs and abs(my - box_bottom) < hs: return 'se'
        if box_x <= mx <= box_right and box_y <= my <= box_bottom: return 'move'
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
            new_scale = new_w_rel / max_w if max_w > 0 else self.drag_init_scale
            new_scale = max(0.1, min(1.0, new_scale))
            s.crop_scale = new_scale
            actual_w = max_w * new_scale
            actual_h = max_h * new_scale
            if self.drag_mode == 'se':
                s.crop_left, s.crop_top = self.drag_init_left, self.drag_init_top
            elif self.drag_mode == 'sw':
                old_right = self.drag_init_left + max_w * self.drag_init_scale
                s.crop_left, s.crop_top = old_right - actual_w, self.drag_init_top
            elif self.drag_mode == 'ne':
                old_bottom = self.drag_init_top + max_h * self.drag_init_scale
                s.crop_left, s.crop_top = self.drag_init_left, old_bottom - actual_h
            elif self.drag_mode == 'nw':
                old_right = self.drag_init_left + max_w * self.drag_init_scale
                old_bottom = self.drag_init_top + max_h * self.drag_init_scale
                s.crop_left, s.crop_top = old_right - actual_w, old_bottom - actual_h
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

    def browse_output(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.output_dir_var.set(d)

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
    if HAS_DND:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        root = TkinterDnD.Tk()
        ttkb.Style("flatly")
    else:
        root = ttkb.Window(themename="flatly")
    app = BatchImageCrop(root)
    root.mainloop()


if __name__ == "__main__":
    main()
