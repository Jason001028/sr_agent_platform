from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from .core import open_image_file

CLASS_COLORS = {
    1: (255, 45, 45),
    2: (70, 170, 255),
    3: (255, 210, 60),
}
LINE_COLORS = {
    1: "#00f5ff",
    2: "#4aa3ff",
    3: "#ffd43b",
}


def contour_is_closed(
    points: list[tuple[int, int]], tolerance: float
) -> bool:
    if len(points) < 3:
        return False
    start_x, start_y = points[0]
    end_x, end_y = points[-1]
    return (start_x - end_x) ** 2 + (start_y - end_y) ** 2 <= tolerance**2


def fill_closed_contour(
    mask: Image.Image,
    points: list[tuple[int, int]],
    value: int,
) -> None:
    if len(points) >= 3:
        ImageDraw.Draw(mask).polygon(points, fill=value)


def overlay(image: Image.Image, mask: Image.Image, alpha: int = 125) -> Image.Image:
    rgb = image.convert("RGBA")
    if mask.size != rgb.size:
        mask = mask.resize(rgb.size, Image.Resampling.NEAREST)
    mask = mask.convert("L")
    transparent = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
    rendered = rgb
    for value, color in CLASS_COLORS.items():
        selected = mask.point(lambda pixel, target=value: 255 if pixel == target else 0)
        color_layer = Image.new("RGBA", rgb.size, (*color, alpha))
        rendered = Image.alpha_composite(
            rendered, Image.composite(color_layer, transparent, selected)
        )
    binary_leftover = mask.point(
        lambda pixel: 255 if pixel not in (0, *CLASS_COLORS.keys()) else 0
    )
    fallback = Image.new("RGBA", rgb.size, (*CLASS_COLORS[1], alpha))
    return Image.alpha_composite(
        rendered, Image.composite(fallback, transparent, binary_leftover)
    )


class MaskEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        image_path: str,
        mask_path: str,
        paint_value: int = 1,
        label_name: str = "云",
        output_path: str | Path | None = None,
    ):
        super().__init__(parent)
        self.title("人工编辑云掩膜")
        self.geometry("1100x780")
        self.result: str | None = None
        self.image_path = Path(image_path)
        self.mask_path = Path(mask_path)
        self.output_path = Path(output_path) if output_path else None
        self.paint_value = paint_value if paint_value in CLASS_COLORS else 1
        self.label_name = label_name
        with open_image_file(self.image_path) as source:
            self.image = source.convert("RGB")
        with open_image_file(self.mask_path) as source_mask:
            self.mask = source_mask.convert("L")
        if self.mask.size != self.image.size:
            self.mask = self.mask.resize(self.image.size, Image.Resampling.NEAREST)
        known_values = set(CLASS_COLORS)
        self.mask = self.mask.point(
            lambda value: 0
            if value == 0
            else value
            if value in known_values
            else self.paint_value
        )
        self.history: list[Image.Image] = [self.mask.copy()]
        self.tool = tk.StringVar(value="add")
        self.shape = tk.StringVar(value="brush")
        self.brush_size = tk.IntVar(value=25)
        self.scale = 1.0
        self.last_point: tuple[int, int] | None = None
        self.contour_points: list[tuple[int, int]] = []
        self.contour_shadow_id: int | None = None
        self.contour_line_id: int | None = None
        self.tk_image = None
        self._build()
        self.after(50, self.fit)
        self.transient(parent)
        self.grab_set()

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill=tk.X)
        ttk.Radiobutton(toolbar, text="增加标注", variable=self.tool, value="add").pack(side=tk.LEFT)
        ttk.Radiobutton(toolbar, text="擦除标注", variable=self.tool, value="erase").pack(side=tk.LEFT, padx=6)
        ttk.Label(
            toolbar, text=f"当前写入：{self.paint_value} {self.label_name}"
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Radiobutton(
            toolbar, text="画笔", variable=self.shape, value="brush"
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            toolbar, text="闭合轮廓填充", variable=self.shape, value="contour"
        ).pack(side=tk.LEFT, padx=6)
        ttk.Label(toolbar, text="画笔").pack(side=tk.LEFT, padx=(12, 2))
        ttk.Scale(toolbar, from_=3, to=120, variable=self.brush_size, orient=tk.HORIZONTAL).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Button(toolbar, text="撤销", command=self.undo).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="适应窗口", command=self.fit).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="保存编辑", command=self.save).pack(side=tk.RIGHT)

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(frame, background="#1d2228", cursor="crosshair")
        xbar = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        ybar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        xbar.pack(side=tk.BOTTOM, fill=tk.X)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.start_draw)
        self.canvas.bind("<B1-Motion>", self.draw)
        self.canvas.bind("<ButtonRelease-1>", self.end_draw)
        self.canvas.bind("<MouseWheel>", self.zoom)

    def refresh(self) -> None:
        preview = overlay(self.image, self.mask)
        size = (
            max(1, int(preview.width * self.scale)),
            max(1, int(preview.height * self.scale)),
        )
        preview = preview.resize(size, Image.Resampling.BILINEAR)
        self.tk_image = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self.contour_shadow_id = None
        self.contour_line_id = None
        self.canvas.create_image(0, 0, image=self.tk_image, anchor=tk.NW)
        if self.shape.get() == "contour" and self.contour_points:
            self.draw_contour_overlay()
        self.canvas.configure(scrollregion=(0, 0, size[0], size[1]))

    def draw_contour_overlay(self) -> None:
        self.canvas.delete("contour_overlay")
        self.contour_shadow_id = None
        self.contour_line_id = None
        if len(self.contour_points) >= 2:
            display_points = [
                coordinate
                for point in self.contour_points
                for coordinate in (point[0] * self.scale, point[1] * self.scale)
            ]
            self.contour_shadow_id = self.canvas.create_line(
                *display_points,
                fill="#101010",
                width=7,
                smooth=True,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                tags=("contour_overlay",),
            )
            line_color = (
                LINE_COLORS.get(self.paint_value, "#00f5ff")
                if self.tool.get() == "add"
                else "#ff4df0"
            )
            self.contour_line_id = self.canvas.create_line(
                *display_points,
                fill=line_color,
                width=3,
                smooth=True,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                tags=("contour_overlay",),
            )
        if self.contour_points:
            start_x, start_y = self.contour_points[0]
            radius = 7
            start_x *= self.scale
            start_y *= self.scale
            self.canvas.create_oval(
                start_x - radius,
                start_y - radius,
                start_x + radius,
                start_y + radius,
                fill="#ffe66d",
                outline="#ffe66d",
                width=3,
                tags=("contour_overlay",),
            )
        self.canvas.tag_raise("contour_overlay")

    def fit(self) -> None:
        width = max(100, self.canvas.winfo_width() - 20)
        height = max(100, self.canvas.winfo_height() - 20)
        self.scale = min(width / self.image.width, height / self.image.height)
        self.refresh()

    def zoom(self, event: tk.Event) -> None:
        self.scale = max(0.05, min(8.0, self.scale * (1.15 if event.delta > 0 else 0.87)))
        self.refresh()

    def image_point(self, event: tk.Event) -> tuple[int, int]:
        x = int(self.canvas.canvasx(event.x) / self.scale)
        y = int(self.canvas.canvasy(event.y) / self.scale)
        return max(0, min(self.image.width - 1, x)), max(0, min(self.image.height - 1, y))

    def start_draw(self, event: tk.Event) -> None:
        self.history.append(self.mask.copy())
        self.history = self.history[-30:]
        self.last_point = self.image_point(event)
        if self.shape.get() == "contour":
            self.contour_points = [self.last_point]
            self.refresh()
            return
        self.draw(event)

    def draw(self, event: tk.Event) -> None:
        point = self.image_point(event)
        if self.shape.get() == "contour":
            if not self.contour_points:
                self.contour_points = [point]
            last_x, last_y = self.contour_points[-1]
            minimum_step = max(1, int(3 / max(self.scale, 0.05)))
            if (point[0] - last_x) ** 2 + (point[1] - last_y) ** 2 >= minimum_step**2:
                self.contour_points.append(point)
                self.draw_contour_overlay()
            return
        if self.last_point is None:
            self.last_point = point
        value = self.paint_value if self.tool.get() == "add" else 0
        draw = ImageDraw.Draw(self.mask)
        draw.line(
            [self.last_point, point],
            fill=value,
            width=max(1, self.brush_size.get()),
            joint="curve",
        )
        radius = max(1, self.brush_size.get() // 2)
        draw.ellipse(
            [point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius],
            fill=value,
        )
        self.last_point = point
        self.refresh()

    def end_draw(self, event: tk.Event) -> None:
        if self.shape.get() == "contour":
            endpoint = self.image_point(event)
            if not self.contour_points or self.contour_points[-1] != endpoint:
                self.contour_points.append(endpoint)
            tolerance = max(
                self.brush_size.get() * 1.5,
                18 / max(self.scale, 0.05),
            )
            if contour_is_closed(self.contour_points, tolerance):
                value = self.paint_value if self.tool.get() == "add" else 0
                fill_closed_contour(self.mask, self.contour_points, value)
            else:
                messagebox.showinfo(
                    "轮廓未闭合",
                    "曲线首尾距离较远，本次没有填充。请回到黄色起点附近再松开鼠标。",
                    parent=self,
                )
                if len(self.history) > 1:
                    self.history.pop()
            self.contour_points = []
            self.last_point = None
            self.refresh()
            return
        self.last_point = None

    def undo(self) -> None:
        if len(self.history) > 1:
            self.mask = self.history.pop()
            self.refresh()

    def save(self) -> None:
        target = self.output_path or self.mask_path.with_name(f"{self.mask_path.stem}_manual.tif")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.mask.save(target, compression="tiff_lzw")
        self.result = str(target)
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        return
        messagebox.showinfo("已保存", f"人工掩膜已保存：\n{target}", parent=self)
        self.destroy()
