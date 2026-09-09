from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from .app import (
    CONFIG_PATH,
    METHOD_LABELS,
    ROOT,
    STATE_PATH,
    find_omnicloudmask_model_dir,
    find_omnicloudmask_python,
)
from .core import (
    AppConfig,
    MySQLDatasetStore,
    ReviewStore,
    Scene,
    attach_cached_omnicloudmask_result,
    attach_omnicloudmask_result,
    cache_review_scene,
    compare_masks,
    copy_to_pending_import,
    filter_scenes_by_cloud_amount_order,
    filesystem_path,
    label_name,
    label_value,
    normalize_label_type,
    open_image_file,
    path_exists,
    run_omnicloudmask_for_scene,
)
from .editor import MaskEditor, overlay


CLOUD_ORDER_DEFAULT = "不筛选：原待选顺序"
CLOUD_ORDER_OPTIONS = {
    "综合 ≥ RDC ≥ Omni": ("combined", "rdc", "omnicloudmask"),
    "综合 ≥ Omni ≥ RDC": ("combined", "omnicloudmask", "rdc"),
    "RDC ≥ 综合 ≥ Omni": ("rdc", "combined", "omnicloudmask"),
    "RDC ≥ Omni ≥ 综合": ("rdc", "omnicloudmask", "combined"),
    "Omni ≥ 综合 ≥ RDC": ("omnicloudmask", "combined", "rdc"),
    "Omni ≥ RDC ≥ 综合": ("omnicloudmask", "rdc", "combined"),
}


class ReviewPicker(tk.Tk):
    def __init__(
        self,
        config_path: Path,
        state_path: Path,
        batch_id: str = "",
        history: bool = False,
        cache_date: str = "",
    ):
        super().__init__()
        self.title("云掩膜选图标注模块")
        self.geometry("1500x880")
        self.minsize(1180, 720)
        self.config_path = config_path
        self.state_path = state_path
        self.config_data = AppConfig.load(config_path)
        self._resolve_config_paths()
        self.store = ReviewStore(state_path)
        self.history_mode = history
        self.batch_id = batch_id or ("" if history else self.store.latest_review_batch())
        self.history_date = tk.StringVar(value=cache_date)
        self.queue: list[Scene] = []
        self.previous_stack: list[Scene] = []
        self.index = 0
        self.current_masks: dict[str, str] = {}
        self.preview_images: list[ImageTk.PhotoImage] = []
        self.omni_python_cache: str | None = None
        self.omni_batch_running = False
        self._pressed_shortcuts: set[str] = set()
        self.cloud_order_rule = tk.StringVar(value=CLOUD_ORDER_DEFAULT)
        self.label_type = tk.StringVar(
            value=normalize_label_type(self.config_data.default_annotation_type)
        )
        self.note = tk.StringVar()
        self.status = tk.StringVar(value="")
        self.progress = tk.StringVar(value="")
        self._build()
        if self.history_mode and not self.history_date.get().strip():
            self.clear_scene()
            self.status.set("请选择一个缓存日期后再加载历史待复核影像。")
        else:
            self.reload_queue()

    def _resolve_config_paths(self) -> None:
        for field_name in ("watch_roots",):
            values = getattr(self.config_data, field_name)
            setattr(
                self.config_data,
                field_name,
                [
                    str((ROOT / value).resolve())
                    if not Path(value).is_absolute()
                    else value
                    for value in values
                ],
            )
        for field_name in (
            "database_dir",
            "dataset_output_dir",
            "typical_dir",
            "cache_dir",
            "omnicloudmask_model_dir",
        ):
            value = getattr(self.config_data, field_name)
            if not Path(value).is_absolute():
                setattr(self.config_data, field_name, str((ROOT / value).resolve()))

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(
            header,
            text=(
                "历史选图"
                if self.history_mode
                else f"选图批次：{self.batch_id or '未指定'}"
            ),
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side=tk.LEFT)
        if self.history_mode:
            ttk.Label(header, text="缓存日期").pack(side=tk.LEFT, padx=(16, 3))
            self.date_combo = ttk.Combobox(
                header,
                textvariable=self.history_date,
                state="readonly",
                values=self.available_cache_dates(),
                width=10,
            )
            self.date_combo.pack(side=tk.LEFT, padx=3)
            self.date_combo.bind("<<ComboboxSelected>>", lambda _event: self.reload_queue())
            ttk.Button(header, text="加载日期", command=self.reload_queue).pack(
                side=tk.LEFT, padx=4
            )
        ttk.Label(header, text="云量排序").pack(side=tk.LEFT, padx=(16, 3))
        cloud_order_combo = ttk.Combobox(
            header,
            textvariable=self.cloud_order_rule,
            state="readonly",
            values=(CLOUD_ORDER_DEFAULT, *CLOUD_ORDER_OPTIONS.keys()),
            width=22,
        )
        cloud_order_combo.pack(side=tk.LEFT, padx=3)
        cloud_order_combo.bind("<<ComboboxSelected>>", lambda _event: self.reload_queue())
        self.omni_batch_button = ttk.Button(
            header, text="补全本文件夹 Omni", command=self.run_omni_batch
        )
        self.omni_batch_button.pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="刷新", command=self.reload_queue).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="运行本景 Omni", command=self.run_omni).pack(side=tk.RIGHT, padx=4)
        ttk.Label(header, text="标注类型").pack(side=tk.RIGHT, padx=(12, 3))
        values = tuple(
            value for value in self.config_data.annotation_types if value in {"cloud", "snow", "other"}
        ) or ("cloud", "snow", "other")
        ttk.Combobox(
            header,
            textvariable=self.label_type,
            state="readonly",
            values=values,
            width=8,
        ).pack(side=tk.RIGHT)

        self.scene_title = ttk.Label(outer, text="", font=("Microsoft YaHei UI", 10, "bold"))
        self.scene_title.pack(fill=tk.X, pady=(0, 4))

        panels = ttk.Frame(outer)
        panels.pack(fill=tk.BOTH, expand=True)
        for column in range(3):
            panels.columnconfigure(column, weight=1, uniform="panel")
        panels.rowconfigure(0, weight=1)
        self.image_labels: dict[str, ttk.Label] = {}
        self.metric_labels: dict[str, ttk.Label] = {}
        for column, method in enumerate(("combined", "rdc", "omnicloudmask")):
            frame = ttk.LabelFrame(panels, text=METHOD_LABELS[method], padding=5)
            frame.grid(row=0, column=column, sticky="nsew", padx=4)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            image_label = ttk.Label(frame, text="暂无", anchor=tk.CENTER)
            image_label.grid(row=0, column=0, sticky="nsew")
            image_label.bind("<Double-1>", lambda _event, key=method: self.edit_method(key))
            self.image_labels[method] = image_label
            metric = ttk.Label(frame, text="", anchor=tk.CENTER)
            metric.grid(row=1, column=0, sticky="ew", pady=4)
            self.metric_labels[method] = metric
            actions = ttk.Frame(frame)
            actions.grid(row=2, column=0)
            ttk.Button(actions, text="选用", command=lambda key=method: self.accept_method(key)).pack(side=tk.LEFT, padx=2)
            ttk.Button(actions, text="人工编辑", command=lambda key=method: self.edit_method(key)).pack(side=tk.LEFT, padx=2)

        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(footer, text="上一景", command=self.previous_scene).pack(side=tk.LEFT, padx=3)
        ttk.Button(footer, text="跳过/下一景 空格", command=self.skip_current).pack(side=tk.LEFT, padx=3)
        ttk.Button(footer, text="无云 W", command=lambda: self.create_constant_mask(False)).pack(side=tk.LEFT, padx=3)
        ttk.Button(footer, text="全云 Q", command=lambda: self.create_constant_mask(True)).pack(side=tk.LEFT, padx=3)
        ttk.Label(footer, text="备注").pack(side=tk.LEFT, padx=(18, 3))
        ttk.Entry(footer, textvariable=self.note).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(footer, textvariable=self.progress).pack(side=tk.RIGHT, padx=8)

        status = ttk.Frame(outer)
        status.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(
            status,
            text="快捷键：1综合法，2RDC，3Omni，W无云，Q全云，空格跳过",
        ).pack(side=tk.RIGHT)
        ttk.Label(status, textvariable=self.status).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.bind("<KeyPress>", self.handle_key)
        self.bind("<KeyRelease>", self.handle_key_release)

    def reload_queue(self) -> None:
        if self.history_mode:
            cache_date = self.history_date.get().strip()
            if not cache_date:
                self.queue = []
                self.clear_scene()
                self.status.set("请选择一个缓存日期后再加载历史待复核影像。")
                return
            raw_queue = self.store.review_queue_for_cache_date(cache_date, "difference")
        else:
            raw_queue = self.store.review_queue("difference", self.batch_id)
        rule_label = self.cloud_order_rule.get()
        ordered_methods = CLOUD_ORDER_OPTIONS.get(rule_label)
        if ordered_methods:
            self.queue = filter_scenes_by_cloud_amount_order(raw_queue, ordered_methods)
        else:
            self.queue = list(raw_queue)
        self.previous_stack.clear()
        self.index = min(self.index, max(0, len(self.queue) - 1))
        if self.queue:
            self.show_scene(self.index)
            if ordered_methods:
                self.status.set(
                    f"已按 {rule_label} 筛选：{len(self.queue)} / {len(raw_queue)} 景"
                )
        else:
            self.clear_scene()
            if ordered_methods and raw_queue:
                self.status.set(
                    f"当前规则 {rule_label} 下没有满足三种云量顺序的待候选景。"
                )
            elif self.history_mode:
                self.status.set(f"{self.history_date.get()} 没有未复核的历史缓存影像。")
            else:
                self.status.set("当前批次没有可加载的待复核景。")

    def available_cache_dates(self) -> tuple[str, ...]:
        review_root = Path(self.config_data.cache_dir).expanduser().resolve() / "review"
        if not review_root.exists():
            return ()
        return tuple(
            sorted(
                (
                    path.name
                    for path in review_root.iterdir()
                    if path.is_dir() and not path.name.startswith(".")
                ),
                reverse=True,
            )
        )

    def current_scene(self) -> Scene | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def show_scene(self, index: int) -> None:
        if not 0 <= index < len(self.queue):
            return
        self.index = index
        scene = self.queue[index]
        self.current_masks = dict(scene.masks)
        self.preview_images.clear()
        self.progress.set(f"{index + 1} / {len(self.queue)}")
        self.scene_title.configure(
            text=(
                f"{scene.scene_id} | {scene.acquired_date} | "
                f"差异 {scene.difference:.1%} / {scene.difference_percent:.1%} | "
                f"IoU {scene.iou:.2f}"
            )
        )
        try:
            with open_image_file(scene.image_path) as source:
                source.thumbnail((470, 650), Image.Resampling.LANCZOS)
                rgb = source.convert("RGB").copy()
        except Exception as exc:
            self.status.set(f"原图加载失败：{exc}")
            return
        for method, label in self.image_labels.items():
            path = self.current_masks.get(method)
            if not path or not path_exists(path):
                label.configure(image="", text="无结果，可人工编辑")
                self.metric_labels[method].configure(text="")
                continue
            try:
                with open_image_file(path) as mask:
                    small_mask = mask.convert("L").resize(rgb.size, Image.Resampling.NEAREST)
                preview = overlay(rgb, small_mask)
            except Exception as exc:
                label.configure(image="", text=f"加载失败：{exc}")
                self.metric_labels[method].configure(text="加载失败")
                continue
            tk_image = ImageTk.PhotoImage(preview)
            self.preview_images.append(tk_image)
            label.configure(image=tk_image, text="")
            ratio = scene.cloud_ratios.get(method)
            self.metric_labels[method].configure(
                text=f"云量：{ratio:.2%}" if ratio is not None else "人工/缓存结果"
            )
        self.status.set("已加载本景")

        self.focus_set()

    def clear_scene(self) -> None:
        self.scene_title.configure(text="当前无待复核景")
        self.progress.set("0 / 0")
        for method in self.image_labels:
            self.image_labels[method].configure(image="", text="暂无")
            self.metric_labels[method].configure(text="")

        self.focus_set()

    def accept_method(self, method: str) -> None:
        scene = self.current_scene()
        if not scene:
            return
        mask_path = self.current_masks.get(method)
        if not mask_path or not path_exists(mask_path):
            self.status.set(f"当前景没有 {METHOD_LABELS.get(method, method)} 掩膜")
            return
        try:
            copy_to_pending_import(
                scene,
                mask_path,
                method,
                self.note.get(),
                self.config_data,
                self.store,
                self.label_type.get(),
            )
            self.status.set(f"{scene.scene_id} 已加入待入库")
            self.remove_current()
        except Exception as exc:
            messagebox.showerror("加入待入库失败", str(exc), parent=self)

    def edit_method(self, method: str) -> None:
        scene = self.current_scene()
        if not scene:
            return
        mask_path = self.current_masks.get(method)
        if not mask_path or not path_exists(mask_path):
            mask_path = self.create_blank_mask(scene, method)
            self.current_masks[method] = mask_path
            scene.masks[method] = mask_path
        label_type = normalize_label_type(self.label_type.get())
        output_dir = Path(self.config_data.cache_dir).expanduser().resolve() / "picker_masks" / scene.scene_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{scene.scene_id}_{method}_{label_type}_manual.tif"
        editor = MaskEditor(
            self,
            scene.image_path,
            mask_path,
            paint_value=label_value(label_type),
            label_name=label_name(label_type),
            output_path=output_path,
        )
        self.wait_window(editor)
        if editor.result:
            self.current_masks[method] = editor.result
            scene.masks[method] = editor.result
            from .core import cloud_ratio

            scene.cloud_ratios[method] = cloud_ratio(editor.result)
            self.store.upsert_scenes([scene])
            self.show_scene(self.index)

    def create_blank_mask(self, scene: Scene, method: str) -> str:
        cache_root = Path(self.config_data.cache_dir).expanduser().resolve()
        target_dir = cache_root / "picker_masks" / scene.scene_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{scene.scene_id}_{method}_blank.tif"
        with open_image_file(scene.image_path) as image:
            mask = Image.new("L", image.size, 0)
        mask.save(target, compression="tiff_lzw")
        return str(target)

    def create_constant_mask(self, all_cloud: bool) -> None:
        scene = self.current_scene()
        if not scene:
            return
        label_type = normalize_label_type(self.label_type.get())
        cache_root = Path(self.config_data.cache_dir).expanduser().resolve() / "picker_masks"
        cache_root.mkdir(parents=True, exist_ok=True)
        target = cache_root / f"{scene.scene_id}_{'all' if all_cloud else 'empty'}_{label_type}.tif"
        with open_image_file(scene.image_path) as image:
            mask = Image.new("L", image.size, label_value(label_type) if all_cloud else 0)
        mask.save(target, compression="tiff_lzw")
        self.current_masks["combined"] = str(target)
        scene.masks["combined"] = str(target)
        scene.cloud_ratios["combined"] = 1.0 if all_cloud else 0.0
        self.store.upsert_scenes([scene])
        self.accept_method("combined")

    def run_omni(self) -> None:
        scene = self.current_scene()
        if not scene:
            return
        if attach_cached_omnicloudmask_result(scene, self.config_data):
            cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
            if cache_path:
                cache_path = cache_review_scene(
                    scene,
                    self.config_data,
                    root_override=cache_path,
                )
                scene.cloud_ratios["review_cache_path"] = cache_path
            self.store.upsert_scenes([scene])
            self.omni_finished(scene.masks["omnicloudmask"])
            return
        if not path_exists(scene.source_tif_path):
            messagebox.showerror("无法运行 Omni", "当前景没有可访问的 MSS.tif 路径。", parent=self)
            return
        self.status.set("正在运行 OmniCloudMask...")

        def worker() -> None:
            try:
                python_exe = self.omni_python_cache
                if not python_exe:
                    python_exe, dependency_error = find_omnicloudmask_python(
                        self.config_data.omnicloudmask_python
                    )
                    if not python_exe:
                        raise RuntimeError(dependency_error or "未找到 Omni 运行环境")
                    self.omni_python_cache = python_exe
                model_dir, missing = find_omnicloudmask_model_dir(
                    self.config_data.omnicloudmask_model_dir
                )
                if not model_dir:
                    raise RuntimeError("缺少 Omni 模型：" + ", ".join(missing))
                self.config_data.omnicloudmask_python = python_exe
                self.config_data.omnicloudmask_model_dir = model_dir
                mask_path = run_omnicloudmask_for_scene(scene, self.config_data)
                attach_omnicloudmask_result(scene, mask_path, self.config_data)
                cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
                if cache_path:
                    cache_path = cache_review_scene(
                        scene,
                        self.config_data,
                        root_override=cache_path,
                    )
                    scene.cloud_ratios["review_cache_path"] = cache_path
                self.store.upsert_scenes([scene])
                self.after(0, lambda: self.omni_finished(mask_path))
            except Exception as exc:
                self.after(0, lambda error=exc: messagebox.showerror("Omni 失败", str(error), parent=self))

        threading.Thread(target=worker, daemon=True).start()

    def omni_finished(self, mask_path: str) -> None:
        scene = self.current_scene()
        if scene:
            self.current_masks["omnicloudmask"] = mask_path
            self.status.set("OmniCloudMask 完成")
            self.show_scene(self.index)

    def omni_batch_source_scenes(self) -> list[Scene]:
        if self.history_mode:
            cache_date = self.history_date.get().strip()
            if cache_date:
                return self.store.review_cache_scenes_for_date(cache_date)
            return []
        batch_scenes = self.store.review_batch_scenes(self.batch_id)
        return batch_scenes or list(self.queue)

    def run_omni_batch(self) -> None:
        if self.omni_batch_running:
            self.status.set("Omni 批量补全正在运行")
            return
        candidates = [
            scene
            for scene in self.omni_batch_source_scenes()
            if "omnicloudmask" not in scene.masks
            and bool(scene.source_tif_path)
            and path_exists(scene.source_tif_path)
        ]
        if not candidates:
            self.status.set("本文件夹没有需要补齐的 Omni 结果")
            return
        self.omni_batch_running = True
        self.omni_batch_button.configure(state=tk.DISABLED)
        self.status.set(f"开始补全 Omni：待运行 {len(candidates)} 景")

        def worker() -> None:
            processed = 0
            skipped = 0
            errors: list[str] = []
            try:
                python_exe = self.omni_python_cache
                if not python_exe:
                    python_exe, dependency_error = find_omnicloudmask_python(
                        self.config_data.omnicloudmask_python
                    )
                    if not python_exe:
                        raise RuntimeError(dependency_error or "未找到 Omni 运行环境")
                    self.omni_python_cache = python_exe
                model_dir, missing = find_omnicloudmask_model_dir(
                    self.config_data.omnicloudmask_model_dir
                )
                if not model_dir:
                    raise RuntimeError("缺少 Omni 模型：" + ", ".join(missing))
                self.config_data.omnicloudmask_python = python_exe
                self.config_data.omnicloudmask_model_dir = model_dir
                total = len(candidates)
                for scene in candidates:
                    if "omnicloudmask" in scene.masks:
                        skipped += 1
                        continue
                    try:
                        found_cached = attach_cached_omnicloudmask_result(
                            scene,
                            self.config_data,
                        )
                        if not found_cached:
                            mask_path = run_omnicloudmask_for_scene(scene, self.config_data)
                            attach_omnicloudmask_result(scene, mask_path, self.config_data)
                        cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
                        if cache_path:
                            cache_path = cache_review_scene(
                                scene,
                                self.config_data,
                                root_override=cache_path,
                            )
                            scene.cloud_ratios["review_cache_path"] = cache_path
                        self.store.upsert_scenes([scene])
                        processed += 1
                        self.after(
                            0,
                            lambda done=processed, all_count=total, sid=scene.scene_id: self.status.set(
                                f"Omni 补全：{done} / {all_count}，当前 {sid}"
                            ),
                        )
                    except Exception as exc:
                        scene.cloud_ratios["omnicloudmask_error"] = str(exc)[-500:]
                        self.store.upsert_scenes([scene])
                        errors.append(f"{scene.scene_id}: {exc}")
                self.after(
                    0,
                    lambda done=processed, skipped_count=skipped, failed=len(errors): self.omni_batch_finished(
                        done, skipped_count, failed, errors[:3]
                    ),
                )
            except Exception as exc:
                self.after(0, lambda error=exc: self.omni_batch_failed(error))

        threading.Thread(target=worker, daemon=True).start()

    def omni_batch_finished(
        self,
        processed: int,
        skipped: int,
        failed: int,
        samples: list[str],
    ) -> None:
        self.omni_batch_running = False
        self.omni_batch_button.configure(state=tk.NORMAL)
        self.reload_queue()
        detail = f"Omni 补全完成：生成 {processed} 景，跳过 {skipped} 景，失败 {failed} 景"
        if samples:
            detail += "；示例错误：" + " | ".join(samples)
        self.status.set(detail)

    def omni_batch_failed(self, exc: Exception) -> None:
        self.omni_batch_running = False
        self.omni_batch_button.configure(state=tk.NORMAL)
        messagebox.showerror("Omni 批量补全失败", str(exc), parent=self)
        self.status.set("Omni 批量补全失败")

    def skip_current(self) -> None:
        scene = self.current_scene()
        if not scene:
            return
        self.store.mark_skipped(scene.scene_id)
        self.remove_current()

    def remove_current(self) -> None:
        if self.queue:
            self.previous_stack.append(self.queue.pop(self.index))
        self.index = min(self.index, max(0, len(self.queue) - 1))
        if self.queue:
            self.show_scene(self.index)
        else:
            self.clear_scene()

    def previous_scene(self) -> None:
        if self.index > 0 and self.queue:
            self.show_scene(max(0, self.index - 1))
            return
        if not self.previous_stack:
            self.status.set("没有可回看的上一景")
            return
        scene = self.previous_stack.pop()
        self.store.remove_pending_import(scene.scene_id)
        self.store.restore_for_review(scene.scene_id)
        scene.status = "pending"
        self.queue.insert(0, scene)
        self.show_scene(0)
        self.status.set(f"已回到上一景，并恢复为待复核：{scene.scene_id}")

    def handle_key(self, event: tk.Event) -> str | None:
        if getattr(event, "widget", None) is not None:
            try:
                if event.widget.winfo_class() in {
                    "Entry",
                    "TEntry",
                    "Text",
                    "TCombobox",
                    "Button",
                    "TButton",
                }:
                    return None
            except tk.TclError:
                return None
        key = (getattr(event, "char", "") or getattr(event, "keysym", "") or "").lower()
        mapping = {
            "1": lambda: self.accept_method("combined"),
            "2": lambda: self.accept_method("rdc"),
            "3": lambda: self.accept_method("omnicloudmask"),
            " ": self.skip_current,
            "space": self.skip_current,
            "w": lambda: self.create_constant_mask(False),
            "q": lambda: self.create_constant_mask(True),
        }
        action = mapping.get(key)
        if action:
            if key in self._pressed_shortcuts:
                return "break"
            self._pressed_shortcuts.add(key)
            action()
            return "break"
        return None

    def handle_key_release(self, event: tk.Event) -> str | None:
        key = (getattr(event, "char", "") or getattr(event, "keysym", "") or "").lower()
        if key:
            self._pressed_shortcuts.discard(key)
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--batch", default="")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--date", default="")
    args = parser.parse_args()
    app = ReviewPicker(
        Path(args.config),
        Path(args.state),
        args.batch,
        history=args.history,
        cache_date=args.date,
    )
    app.mainloop()


if __name__ == "__main__":
    main()
