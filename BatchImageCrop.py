"""
批量图片裁剪工具 v1.0
- 批量加载图片，按目标宽高比可视化裁剪
- 拖拽/键盘微调裁剪框，滚轮缩放预览
- 单图独立翻转/旋转，可批量复制变换设置
- 零压缩无损输出，直接截取原图像素
- 不做 EXIF 修正，按原始像素处理
- 绿色免安装，ttkbootstrap flatly 主题
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import ttkbootstrap as ttkb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk
import os

SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tiff', '.tif', '.gif')

# N4: 不需要 EXIF 修正 — 按原始像素直接处理


class PerImageSettings:
    """每张图的独立变换设置"""
    def __init__(self):
        self.flip_h = False
        self.flip_v = False
        self.rotate = 0  # 0, 90, 180, 270


class BatchImageCrop:
    def __init__(self, root):
        self.root = root
        self.root.title("批量图片裁剪工具 v1.0")
        self.root.geometry("1200x800")
        self.root.minsize(1000, 700)

        # --- 数据 ---
        self.image_files = []
        self.image_settings = {}  # index -> PerImageSettings
        self.current_index = -1
        self.original_image = None
        self.display_photo = None

        # 裁剪比例 (相对于目标尺寸)
        self.ratio_w = 4
        self.ratio_h = 3

        # 裁剪框在图片上的相对位置 (0~1)
        self.crop_left = 0.0
        self.crop_top = 0.0

        # 画布状态
        self.scale = 1.0
        self.zoom = 1.0  # 预览缩放倍率
        self.pad_x = 0
        self.pad_y = 0

        # 拖拽状态
        self.dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_crop_left = 0.0
        self.drag_crop_top = 0.0

        # UI 变量
        self.unit_var = tk.StringVar(value="cm")
        self.size_w_var = tk.StringVar(value="10")
        self.size_h_var = tk.StringVar(value="7")
        self.output_dir_var = tk.StringVar()
        self.suffix_var = tk.StringVar(value="_cropped")
        self.flip_h_var = tk.BooleanVar(value=False)
        self.flip_v_var = tk.BooleanVar(value=False)
        self.rotate_var = tk.StringVar(value="0")

        self.build_ui()

        # 键盘绑定
        self.root.bind('<Left>', lambda e: self.nudge(-5, 0))
        self.root.bind('<Right>', lambda e: self.nudge(5, 0))
        self.root.bind('<Up>', lambda e: self.nudge(0, -5))
        self.root.bind('<Down>', lambda e: self.nudge(0, 5))
        self.root.bind('<Shift-Left>', lambda e: self.nudge(-1, 0))
        self.root.bind('<Shift-Right>', lambda e: self.nudge(1, 0))
        self.root.bind('<Shift-Up>', lambda e: self.nudge(0, -1))
        self.root.bind('<Shift-Down>', lambda e: self.nudge(0, 1))

    # ===================== UI 构建 =====================
    def build_ui(self):
        # ===== 顶部：裁剪比例设置 =====
        top_frame = ttkb.Frame(self.root, padding=(10, 6))
        top_frame.pack(fill=X)

        ratio_lf = ttkb.LabelFrame(top_frame, text="裁剪比例（仅决定裁剪框形状）", padding=6)
        ratio_lf.pack(side=LEFT, padx=(0, 10))

        ttkb.Entry(ratio_lf, textvariable=self.size_w_var, width=5).pack(side=LEFT)
        ttkb.Label(ratio_lf, text=" : ").pack(side=LEFT)
        ttkb.Entry(ratio_lf, textvariable=self.size_h_var, width=5).pack(side=LEFT)
        ttkb.Label(ratio_lf, text="(任意单位，仅取比例)").pack(side=LEFT, padx=5)
        ttkb.Button(ratio_lf, text="应用", bootstyle="primary-sm", command=self.apply_ratio).pack(side=LEFT, padx=8)

        # 输出目录
        out_lf = ttkb.LabelFrame(top_frame, text="输出设置", padding=6)
        out_lf.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))

        ttkb.Label(out_lf, text="目录:").pack(side=LEFT)
        ttkb.Entry(out_lf, textvariable=self.output_dir_var, width=25).pack(side=LEFT, padx=(4, 4), fill=X, expand=True)
        ttkb.Button(out_lf, text="浏览", bootstyle="secondary-sm", command=self.browse_output).pack(side=LEFT, padx=(0, 6))
        ttkb.Label(out_lf, text="后缀:").pack(side=LEFT)
        ttkb.Entry(out_lf, textvariable=self.suffix_var, width=10).pack(side=LEFT, padx=4)

        # 导入按钮
        import_lf = ttkb.LabelFrame(top_frame, text="导入", padding=6)
        import_lf.pack(side=LEFT)
        ttkb.Button(import_lf, text="添加图片", bootstyle="success", command=self.add_images).pack(side=LEFT, padx=3)
        ttkb.Button(import_lf, text="添加目录", bootstyle="success-outline", command=self.add_folder).pack(side=LEFT, padx=3)

        # ===== 中间：画布 + 右侧面板 =====
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

        # 右侧面板
        right_panel = ttkb.Frame(mid_frame, width=230)
        right_panel.pack(fill=Y, side=LEFT, padx=(8, 0))
        right_panel.pack_propagate(False)

        # 图片列表
        ttkb.Label(right_panel, text="图片列表", font=("Microsoft YaHei", 10, "bold")).pack(pady=(5, 2))
        self.count_label = ttkb.Label(right_panel, text="共 0 张", bootstyle="secondary")
        self.count_label.pack()

        list_frame = ttkb.Frame(right_panel)
        list_frame.pack(fill=BOTH, expand=True, pady=4)
        sb = ttkb.Scrollbar(list_frame)
        sb.pack(side=RIGHT, fill=Y)
        self.file_list = tk.Listbox(list_frame, selectmode=tk.SINGLE, yscrollcommand=sb.set,
                                     font=("Microsoft YaHei", 9), activestyle="dotbox")
        self.file_list.pack(fill=BOTH, expand=True)
        sb.config(command=self.file_list.yview)
        self.file_list.bind("<<ListboxSelect>>", self.on_list_select)

        btn_row = ttkb.Frame(right_panel)
        btn_row.pack(fill=X, pady=(0, 8))
        ttkb.Button(btn_row, text="清空", bootstyle="warning-sm", command=self.clear_list).pack(side=LEFT, expand=True, fill=X, padx=(0,2))
        ttkb.Button(btn_row, text="删除选中", bootstyle="danger-sm", command=self.remove_selected).pack(side=LEFT, expand=True, fill=X, padx=(2,0))

        # 变换面板（每张独立）
        ttkb.Separator(right_panel).pack(fill=X, pady=6)
        ttkb.Label(right_panel, text="当前图片变换", font=("Microsoft YaHei", 10, "bold")).pack()
        ttkb.Label(right_panel, text="（先变换，再裁剪）", bootstyle="secondary").pack()

        transform_frame = ttkb.Frame(right_panel, padding=6)
        transform_frame.pack(fill=X, pady=4)

        ttkb.Checkbutton(transform_frame, text="水平翻转", variable=self.flip_h_var,
                         command=self.on_transform_change).pack(anchor=W, pady=2)
        ttkb.Checkbutton(transform_frame, text="垂直翻转", variable=self.flip_v_var,
                         command=self.on_transform_change).pack(anchor=W, pady=2)

        rot_frame = ttkb.Frame(transform_frame)
        rot_frame.pack(fill=X, pady=2)
        ttkb.Label(rot_frame, text="旋转:").pack(side=LEFT)
        ttkb.Combobox(rot_frame, textvariable=self.rotate_var, values=["0", "90", "180", "270"],
                      state="readonly", width=5).pack(side=LEFT, padx=4)
        ttkb.Label(rot_frame, text="°").pack(side=LEFT)
        self.rotate_var.trace_add("write", lambda *a: self.on_transform_change())

        ttkb.Separator(transform_frame).pack(fill=X, pady=6)
        ttkb.Button(transform_frame, text="复制变换到所有图片", bootstyle="info-sm",
                    command=self.copy_transform_to_all).pack(fill=X)

        # ===== 底部状态栏 =====
        bot_frame = ttkb.Frame(self.root, padding=(10, 5))
        bot_frame.pack(fill=X)

        self.info_label = ttkb.Label(bot_frame, text="就绪 — 添加图片后拖拽裁剪框调整位置", font=("Microsoft YaHei", 9))
        self.info_label.pack(side=LEFT)

        self.progress = ttkb.Progressbar(bot_frame, mode="determinate", length=180, bootstyle="info")
        self.progress.pack(side=LEFT, padx=15)
        self.progress_label = ttkb.Label(bot_frame, text="", font=("Microsoft YaHei", 9))
        self.progress_label.pack(side=LEFT)

        ttkb.Button(bot_frame, text="批量裁剪导出", bootstyle="danger", command=self.batch_crop).pack(side=RIGHT)

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
            self._refresh_list()
            if self.current_index < 0:
                self.current_index = 0
            self._load_current()

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
            self._refresh_list()
            if self.current_index < 0:
                self.current_index = 0
            self._load_current()

    def clear_list(self):
        self.image_files.clear()
        self.image_settings.clear()
        self.current_index = -1
        self.original_image = None
        self._refresh_list()
        self._render_canvas()

    def remove_selected(self):
        sel = self.file_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self.image_files.pop(idx)
        # 重建 settings 索引
        new_settings = {}
        for i in range(len(self.image_files)):
            if i < idx:
                new_settings[i] = self.image_settings.get(i, PerImageSettings())
            elif i + 1 in self.image_settings:
                new_settings[i] = self.image_settings[i + 1]
            else:
                new_settings[i] = PerImageSettings()
        self.image_settings = new_settings
        if self.current_index >= len(self.image_files):
            self.current_index = len(self.image_files) - 1
        self._refresh_list()
        self._load_current()

    def _refresh_list(self):
        self.file_list.delete(0, tk.END)
        for i, p in enumerate(self.image_files):
            name = os.path.basename(p)
            s = self.image_settings.get(i, PerImageSettings())
            tags = []
            if s.flip_h:
                tags.append("H")
            if s.flip_v:
                tags.append("V")
            if s.rotate:
                tags.append(f"{s.rotate}°")
            tag_str = f" [{'+'.join(tags)}]" if tags else ""
            self.file_list.insert(tk.END, f"{name}{tag_str}")
        self.count_label.config(text=f"共 {len(self.image_files)} 张")

    # ===================== 图片加载 =====================
    def _load_current(self):
        if not self.image_files or self.current_index < 0:
            self.original_image = None
            self._render_canvas()
            return
        idx = self.current_index
        if idx >= len(self.image_files):
            return
        self.file_list.selection_clear(0, tk.END)
        self.file_list.selection_set(idx)
        self.file_list.see(idx)

        path = self.image_files[idx]
        try:
            img = Image.open(path)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            self.original_image = img
            w, h = img.size
            fname = os.path.basename(path)
            self.info_label.config(text=f"[{idx+1}/{len(self.image_files)}] {fname}  |  {w}×{h} px")
        except Exception as e:
            self.original_image = None
            self.info_label.config(text=f"加载失败: {e}")
        self._sync_transform_vars()
        self._render_canvas()

    # ===================== 变换操作 =====================
    def _get_current_settings(self):
        if self.current_index < 0:
            return PerImageSettings()
        return self.image_settings.get(self.current_index, PerImageSettings())

    def _sync_transform_vars(self):
        """从 settings 同步到 UI 变量"""
        s = self._get_current_settings()
        self.flip_h_var.set(s.flip_h)
        self.flip_v_var.set(s.flip_v)
        self.rotate_var.set(str(s.rotate))

    def on_transform_change(self):
        """UI 变量变化 -> 保存到 settings"""
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
        self._refresh_list()
        self.file_list.selection_set(self.current_index)

    def copy_transform_to_all(self):
        if self.current_index < 0:
            return
        src = self._get_current_settings()
        for i in range(len(self.image_files)):
            ns = PerImageSettings()
            ns.flip_h = src.flip_h
            ns.flip_v = src.flip_v
            ns.rotate = src.rotate
            self.image_settings[i] = ns
        self._refresh_list()
        self.file_list.selection_set(self.current_index)

    def _apply_transform(self, img, settings):
        """对图片应用翻转和旋转"""
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

    # ===================== 比例设置 =====================
    def apply_ratio(self):
        try:
            w = float(self.size_w_var.get())
            h = float(self.size_h_var.get())
            if w <= 0 or h <= 0:
                raise ValueError
            self.ratio_w = w
            self.ratio_h = h
            # 重新计算裁剪框
            self._recalc_crop_box()
            self._render_canvas()
        except (ValueError, ZeroDivisionError):
            messagebox.showerror("错误", "请输入有效的正数")

    def _recalc_crop_box(self):
        """根据新比例重新计算裁剪框，居中显示，尽量大"""
        if self.original_image is None:
            return
        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size

        target_aspect = self.ratio_w / self.ratio_h
        img_aspect = iw / ih

        if target_aspect > img_aspect:
            crop_w_ratio = 1.0
            crop_h_ratio = img_aspect / target_aspect
        else:
            crop_h_ratio = 1.0
            crop_w_ratio = target_aspect / img_aspect

        self.crop_left = (1.0 - crop_w_ratio) / 2
        self.crop_top = (1.0 - crop_h_ratio) / 2

    # ===================== 画布渲染 =====================
    def on_canvas_resize(self, event):
        self._render_canvas()

    def _render_canvas(self):
        """绘制画布：图片 + 裁剪框遮罩"""
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

        # 计算显示缩放
        margin = 30
        avail_w = cw - margin * 2
        avail_h = ch - margin * 2
        base_scale = min(avail_w / iw, avail_h / ih, 1.0)
        self.scale = base_scale * self.zoom

        dw = max(1, int(iw * self.scale))
        dh = max(1, int(ih * self.scale))
        self.pad_x = (cw - dw) // 2
        self.pad_y = (ch - dh) // 2

        # 缩放图片用于显示
        disp = img.resize((dw, dh), Image.LANCZOS)
        self.display_photo = ImageTk.PhotoImage(disp)
        self.canvas.create_image(self.pad_x, self.pad_y, anchor=tk.NW, image=self.display_photo, tags="img")

        # 计算裁剪框在画布上的坐标
        target_aspect = self.ratio_w / self.ratio_h
        img_aspect = iw / ih

        if target_aspect > img_aspect:
            crop_w_ratio = 1.0
            crop_h_ratio = img_aspect / target_aspect
        else:
            crop_h_ratio = 1.0
            crop_w_ratio = target_aspect / img_aspect

        # 裁剪框画布坐标
        box_canvas_x = self.pad_x + int(self.crop_left * dw)
        box_canvas_y = self.pad_y + int(self.crop_top * dh)
        box_canvas_w = int(crop_w_ratio * dw)
        box_canvas_h = int(crop_h_ratio * dh)

        # 确保不超出图片范围
        box_canvas_w = min(box_canvas_w, self.pad_x + dw - box_canvas_x)
        box_canvas_h = min(box_canvas_h, self.pad_y + dh - box_canvas_y)

        # 绘制遮罩 (框外半透明效果)
        # 上
        if box_canvas_y > self.pad_y:
            self.canvas.create_rectangle(self.pad_x, self.pad_y, self.pad_x + dw, box_canvas_y,
                                          fill="black", stipple="gray50", outline="")
        # 下
        box_bottom = box_canvas_y + box_canvas_h
        if box_bottom < self.pad_y + dh:
            self.canvas.create_rectangle(self.pad_x, box_bottom, self.pad_x + dw, self.pad_y + dh,
                                          fill="black", stipple="gray50", outline="")
        # 左
        if box_canvas_x > self.pad_x:
            self.canvas.create_rectangle(self.pad_x, box_canvas_y, box_canvas_x, box_bottom,
                                          fill="black", stipple="gray50", outline="")
        # 右
        box_right = box_canvas_x + box_canvas_w
        if box_right < self.pad_x + dw:
            self.canvas.create_rectangle(box_right, box_canvas_y, self.pad_x + dw, box_bottom,
                                          fill="black", stipple="gray50", outline="")

        # 裁剪框边框
        self.canvas.create_rectangle(box_canvas_x, box_canvas_y, box_right, box_bottom,
                                      outline="#00ff88", width=2)

        # 标注尺寸信息
        src_crop_w = int(crop_w_ratio * iw)
        src_crop_h = int(crop_h_ratio * ih)
        info_text = f"输出: {src_crop_w}×{src_crop_h} px (零压缩)"
        cx = box_canvas_x + box_canvas_w // 2
        cy = box_canvas_y - 8
        if cy < self.pad_y + 15:
            cy = box_bottom + 15
            anchor = tk.N
        else:
            anchor = tk.S
        self.canvas.create_text(cx, cy, text=info_text, fill="#00ff88",
                                 font=("Consolas", 10, "bold"), anchor=anchor)

    # ===================== 鼠标交互 =====================
    def on_mouse_down(self, event):
        if self.original_image is None:
            return
        self.dragging = True
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.drag_crop_left = self.crop_left
        self.drag_crop_top = self.crop_top

    def on_mouse_drag(self, event):
        if not self.dragging or self.original_image is None:
            return

        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size
        dw = max(1, int(iw * self.scale))
        dh = max(1, int(ih * self.scale))

        dx = (event.x - self.drag_start_x) / dw if dw > 0 else 0
        dy = (event.y - self.drag_start_y) / dh if dh > 0 else 0

        # 计算裁剪框尺寸
        target_aspect = self.ratio_w / self.ratio_h
        img_aspect = iw / ih
        if target_aspect > img_aspect:
            crop_w_ratio = 1.0
            crop_h_ratio = img_aspect / target_aspect
        else:
            crop_h_ratio = 1.0
            crop_w_ratio = target_aspect / img_aspect

        new_left = self.drag_crop_left + dx
        new_top = self.drag_crop_top + dy

        # 边界限制
        new_left = max(0, min(new_left, 1.0 - crop_w_ratio))
        new_top = max(0, min(new_top, 1.0 - crop_h_ratio))

        self.crop_left = new_left
        self.crop_top = new_top
        self._render_canvas()

    def on_mouse_up(self, event):
        self.dragging = False
        self._update_crop_info()

    def on_mouse_wheel(self, event):
        """滚轮缩放预览视图"""
        if self.original_image is None:
            return
        if event.delta > 0:
            self.zoom = min(self.zoom * 1.15, 5.0)
        else:
            self.zoom = max(self.zoom / 1.15, 0.3)
        self._render_canvas()

    def nudge(self, dx, dy):
        """键盘微调裁剪框"""
        if self.original_image is None:
            return
        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size

        target_aspect = self.ratio_w / self.ratio_h
        img_aspect = iw / ih
        if target_aspect > img_aspect:
            crop_w_ratio = 1.0
            crop_h_ratio = img_aspect / target_aspect
        else:
            crop_h_ratio = 1.0
            crop_w_ratio = target_aspect / img_aspect

        rx = dx / iw if iw > 0 else 0
        ry = dy / ih if ih > 0 else 0

        self.crop_left = max(0, min(self.crop_left + rx, 1.0 - crop_w_ratio))
        self.crop_top = max(0, min(self.crop_top + ry, 1.0 - crop_h_ratio))
        self._render_canvas()
        self._update_crop_info()

    def _update_crop_info(self):
        if self.original_image is None:
            return
        s = self._get_current_settings()
        img = self._apply_transform(self.original_image, s)
        iw, ih = img.size

        target_aspect = self.ratio_w / self.ratio_h
        img_aspect = iw / ih
        if target_aspect > img_aspect:
            crop_w_ratio = 1.0
            crop_h_ratio = img_aspect / target_aspect
        else:
            crop_h_ratio = 1.0
            crop_w_ratio = target_aspect / img_aspect

        src_x = int(self.crop_left * iw)
        src_y = int(self.crop_top * ih)
        src_w = int(crop_w_ratio * iw)
        src_h = int(crop_h_ratio * ih)

        fname = os.path.basename(self.image_files[self.current_index]) if self.current_index >= 0 else ""
        self.info_label.config(
            text=f"裁剪区域: ({src_x},{src_y}) {src_w}×{src_h} px → 零压缩输出"
        )

    # ===================== 列表选择 =====================
    def on_list_select(self, event):
        sel = self.file_list.curselection()
        if sel:
            self.current_index = sel[0]
            self.zoom = 1.0
            self._recalc_crop_box()
            self._load_current()

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

        # 验证比例
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
        target_aspect = rw / rh

        for i, path in enumerate(self.image_files):
            try:
                img = Image.open(path)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')

                # 应用变换
                s = self.image_settings.get(i, PerImageSettings())
                img = self._apply_transform(img, s)
                iw, ih = img.size
                img_aspect = iw / ih

                # 计算裁剪区域
                if target_aspect > img_aspect:
                    crop_w_ratio = 1.0
                    crop_h_ratio = img_aspect / target_aspect
                else:
                    crop_h_ratio = 1.0
                    crop_w_ratio = target_aspect / img_aspect

                # 所有图使用相同的相对裁剪位置
                cl = max(0, min(self.crop_left, 1.0 - crop_w_ratio))
                ct = max(0, min(self.crop_top, 1.0 - crop_h_ratio))

                left = int(cl * iw)
                top = int(ct * ih)
                right = left + int(crop_w_ratio * iw)
                bottom = top + int(crop_h_ratio * ih)

                right = min(right, iw)
                bottom = min(bottom, ih)

                # 裁剪（零缩放，直接截取像素）
                cropped = img.crop((left, top, right, bottom))

                # 输出
                base, ext = os.path.splitext(os.path.basename(path))
                out_name = f"{base}{suffix}{ext}"
                out_path = os.path.join(out_dir, out_name)
                counter = 1
                while os.path.exists(out_path):
                    out_name = f"{base}{suffix}_{counter}{ext}"
                    out_path = os.path.join(out_dir, out_name)
                    counter += 1

                # 无损保存
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
