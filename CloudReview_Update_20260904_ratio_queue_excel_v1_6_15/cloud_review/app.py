from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .core import (
    AppConfig,
    LABEL_NAMES,
    LABEL_VALUES,
    MySQLDatasetStore,
    ReviewStore,
    Scene,
    attach_cached_omnicloudmask_result,
    archive_import_item,
    attach_omnicloudmask_result,
    auto_omnicloudmask_allowed,
    cache_review_scene,
    compare_masks,
    copy_to_pending_import,
    copy_to_typical,
    delete_pending_files,
    discover_scan_groups,
    export_records_to_dataset,
    label_name,
    label_value,
    normalize_label_type,
    open_image_file,
    path_exists,
    path_is_file,
    path_is_dir,
    filesystem_path,
    run_omnicloudmask_for_scene,
    search_scene_candidates,
    search_scene_candidates_by_ids,
)
from .editor import MaskEditor, overlay


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "cloud_review.json"
STATE_PATH = ROOT / "data" / "cloud_review.sqlite3"
METHOD_LABELS = {
    "combined": "综合判别法",
    "rdc": "RDC",
    "omnicloudmask": "OmniCloudMask",
}
OMNI_REQUIRED_MODULES = (
    "rasterio",
    "torch",
    "torchvision",
    "timm",
    "segmentation_models_pytorch",
    "safetensors",
)
OMNI_MODEL_FILES = (
    "PM_model_OCM_7.97_R_G_NIR_3_smp_regnety_004.pycls_in1k_PT_state.safetensors",
    "PM_model_OCM_7.97_R_G_NIR_3_smp_edgenext_small.usi_in1k_PT_state.safetensors",
)


def find_omnicloudmask_python(configured_path: str = "") -> tuple[str | None, str]:
    candidates = [
        configured_path,
        sys.executable,
        r"D:\ProgramData\Anaconda3\python.exe",
        r"C:\ProgramData\Anaconda3\python.exe",
        str(Path.home() / "anaconda3" / "python.exe"),
        str(Path.home() / "miniconda3" / "python.exe"),
    ]
    path_python = shutil.which("python")
    if path_python:
        candidates.append(path_python)
    seen: set[str] = set()
    failures: list[str] = []
    check_code = "; ".join(f"import {module}" for module in OMNI_REQUIRED_MODULES)
    for candidate in candidates:
        if not candidate:
            continue
        resolved = os.path.normcase(os.path.abspath(os.path.expandvars(candidate)))
        if resolved in seen or not path_is_file(resolved):
            continue
        seen.add(resolved)
        try:
            completed = subprocess.run(
                [resolved, "-c", check_code],
                capture_output=True,
                text=True,
                timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode == 0:
                return resolved, ""
            detail = completed.stderr.strip().splitlines()
            failures.append(
                f"{resolved}: {detail[-1] if detail else '依赖检查失败'}"
            )
        except Exception as exc:
            failures.append(f"{resolved}: {exc}")
    return None, "\n".join(failures[-4:])


def find_omnicloudmask_model_dir(configured_path: str) -> tuple[str | None, list[str]]:
    candidates = [
        configured_path,
        str(ROOT / "models" / "omnicloudmask"),
        str(
            Path.home()
            / "AppData"
            / "Local"
            / "omnicloudmask"
            / "omnicloudmask"
            / "1.7.1"
        ),
    ]
    seen: set[str] = set()
    last_missing = list(OMNI_MODEL_FILES)
    for candidate in candidates:
        if not candidate:
            continue
        resolved = os.path.normcase(os.path.abspath(os.path.expandvars(candidate)))
        if resolved in seen:
            continue
        seen.add(resolved)
        directory = Path(resolved)
        missing: list[str] = []
        for filename in OMNI_MODEL_FILES:
            model_path = directory / filename
            try:
                if not model_path.is_file() or model_path.stat().st_size < 1024 * 1024:
                    missing.append(filename)
            except OSError:
                missing.append(filename)
        if not missing:
            return str(directory), []
        last_missing = missing
    return None, last_missing


class CloudReviewApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("遥感影像云数据集智能筛查与标注系统")
        self.geometry("1460x900")
        self.minsize(1100, 720)
        self.config_data = AppConfig.load(CONFIG_PATH)
        self._resolve_config_paths()
        self.store = ReviewStore(STATE_PATH)
        self.store.clear_scan_cache()
        self.store.reset_in_progress_scan_groups()
        self.queue: list[Scene] = []
        self.index = 0
        self.current_masks: dict[str, str] = {}
        self.preview_images: list[ImageTk.PhotoImage] = []
        self.scan_running = False
        self.scan_process: subprocess.Popen | None = None
        self.dataset_export_running = False
        self.omni_python_cache: str | None = None
        self.omni_queue_running = False
        self.omni_queue_manual = False
        self.omni_queue_stop_requested = False
        self.scan_result_path = Path(self.config_data.cache_dir) / "scan_result.json"
        self.scan_progress_path = Path(self.config_data.cache_dir) / "scan_progress.json"
        self.scan_config_path = Path(self.config_data.cache_dir) / "scan_config.json"
        self.scan_log_path = self.resolve_scan_log_path()
        self.current_scan_run_id = ""
        self.monitor_session_id = ""
        self.monitor_session_path: Path | None = None
        self.review_batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}"
        self.last_review_queue_refresh_count = 0
        self.monitor_enabled = tk.BooleanVar(value=False)
        self.database_ready = False
        self.mysql_store: MySQLDatasetStore | None = None
        self.environment_ready = False
        self.strategy = tk.StringVar(value=self.config_data.queue_strategy)
        self.limit = tk.IntVar(value=self.config_data.daily_review_limit)
        self.label_type = tk.StringVar(
            value=normalize_label_type(self.config_data.default_annotation_type)
        )
        self.note = tk.StringVar()
        self.status = tk.StringVar(value="准备就绪")
        self.progress = tk.StringVar(value="0 / 0")
        self.summary = tk.StringVar(value="")
        self.environment_status = tk.StringVar(value="环境尚未检查")
        self.scan_log_text = tk.StringVar(value="扫描日志：暂无")
        self._build_ui()
        self.status.set("请先打开“设置”确认路径，然后点击“检查环境”。")
        self.update_action_states()
        self.after(5000, self.auto_omni_queue_tick)

    def _resolve_config_paths(self) -> None:
        for field_name in ("watch_roots",):
            values = getattr(self.config_data, field_name)
            setattr(
                self.config_data,
                field_name,
                [str((ROOT / value).resolve()) if not Path(value).is_absolute() else value for value in values],
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

    def resolve_scan_log_path(self) -> Path:
        configured = (self.config_data.scan_log_path or "").strip()
        if configured:
            path = Path(configured).expanduser()
            return path if path.is_absolute() else (ROOT / path).resolve()
        return Path(self.config_data.cache_dir).expanduser().resolve() / "scan.log"

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Metric.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(header, text="云检测筛查与标注工作台", style="Title.TLabel").pack(side=tk.LEFT)
        self.monitor_button = ttk.Button(
            header, text="开始监控", command=self.toggle_monitoring
        )
        self.monitor_button.pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="设置", command=self.open_settings).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="检查环境", command=self.check_environment).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="分布统计", command=self.show_distribution_stats).pack(side=tk.RIGHT, padx=4)
        self.export_dataset_button = ttk.Button(
            header, text="导出数据集", command=self.export_dataset_from_database
        )
        self.export_dataset_button.pack(side=tk.RIGHT, padx=4)
        self.pending_button = ttk.Button(
            header, text="待入库管理 (0)", command=self.show_pending_imports
        )
        self.pending_button.pack(side=tk.RIGHT, padx=4)
        ttk.Button(
            header, text="历史选图", command=self.open_history_picker_module
        ).pack(side=tk.RIGHT, padx=4)
        ttk.Button(
            header, text="手工挑图", command=self.show_manual_scene_browser
        ).pack(side=tk.RIGHT, padx=4)
        self.omni_queue_button = ttk.Button(
            header, text="开始 Omni 队列", command=self.toggle_omni_queue
        )
        self.omni_queue_button.pack(side=tk.RIGHT, padx=4)
        self.scan_button = ttk.Button(header, text="扫描一次", command=self.scan_now)
        self.scan_button.pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="进度统计", command=self.show_scan_progress_window).pack(side=tk.RIGHT, padx=4)

        controls = ttk.LabelFrame(outer, text="今日复核计划", padding=8)
        controls.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(controls, textvariable=self.environment_status).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(controls, text="排序策略").pack(side=tk.LEFT)
        strategy = ttk.Combobox(
            controls,
            textvariable=self.strategy,
            state="readonly",
            width=12,
            values=("difference", "spatiotemporal", "latest", "random"),
        )
        strategy.pack(side=tk.LEFT, padx=5)
        ttk.Label(controls, text="今日上限").pack(side=tk.LEFT, padx=(12, 2))
        ttk.Spinbox(controls, from_=1, to=1000, textvariable=self.limit, width=7).pack(side=tk.LEFT)
        ttk.Button(controls, text="打开选图模块", command=self.open_picker_module).pack(side=tk.LEFT, padx=8)
        ttk.Button(
            controls, text="刷新待复核", command=self.refresh_queue_preserve_current
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(controls, textvariable=self.summary, style="Metric.TLabel").pack(side=tk.RIGHT)

        body = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)
        self.queue_frame = ttk.LabelFrame(body, text="待复核队列（共 0 景）", padding=6)
        compare_frame = ttk.Frame(body)
        body.add(self.queue_frame, weight=1)
        body.add(compare_frame, weight=5)

        self.tree = ttk.Treeview(
            self.queue_frame,
            columns=("date", "diff", "diffpct", "ai", "iou"),
            show="tree headings",
            selectmode="browse",
        )
        queue_scroll = ttk.Scrollbar(
            self.queue_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=queue_scroll.set)
        self.tree.heading("#0", text="景号")
        self.tree.heading("date", text="日期")
        self.tree.heading("diff", text="差异差值")
        self.tree.heading("diffpct", text="差异百分比")
        self.tree.heading("ai", text="AI")
        self.tree.heading("iou", text="IoU")
        self.tree.column("#0", width=210)
        self.tree.column("date", width=85)
        self.tree.column("diff", width=70, anchor=tk.CENTER)
        self.tree.column("diffpct", width=80, anchor=tk.CENTER)
        self.tree.column("ai", width=55, anchor=tk.CENTER)
        self.tree.column("iou", width=55, anchor=tk.CENTER)
        queue_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Button-3>", self.show_queue_context_menu)
        self.tree.bind("<Control-Button-1>", self.show_queue_context_menu)

        self.scene_title = ttk.Label(compare_frame, text="尚未加载数据", style="Metric.TLabel")
        self.scene_title.pack(fill=tk.X, pady=(0, 5))
        self.cards = ttk.Frame(compare_frame)
        self.cards.pack(fill=tk.BOTH, expand=True)
        for column in range(3):
            self.cards.columnconfigure(column, weight=1, uniform="method")
        self.cards.rowconfigure(0, weight=1)
        self.image_labels: dict[str, ttk.Label] = {}
        self.metric_labels: dict[str, ttk.Label] = {}
        self.accept_buttons: dict[str, ttk.Button] = {}
        self.database_action_buttons: list[ttk.Button] = []
        for column, method in enumerate(("combined", "rdc", "omnicloudmask")):
            card = ttk.LabelFrame(self.cards, text=METHOD_LABELS[method], padding=5)
            card.grid(row=0, column=column, sticky="nsew", padx=4)
            card.rowconfigure(0, weight=1)
            card.columnconfigure(0, weight=1)
            label = ttk.Label(card, text="未发现结果", anchor=tk.CENTER)
            label.grid(row=0, column=0, sticky="nsew")
            label.bind("<Double-1>", lambda _event, key=method: self.edit_method(key))
            metric = ttk.Label(card, text="", anchor=tk.CENTER)
            metric.grid(row=1, column=0, sticky="ew", pady=4)
            buttons = ttk.Frame(card)
            buttons.grid(row=2, column=0)
            accept_button = ttk.Button(
                buttons,
                text="加入待入库",
                command=lambda key=method: self.accept_method(key),
            )
            accept_button.pack(side=tk.LEFT, padx=2)
            self.accept_buttons[method] = accept_button
            ttk.Button(buttons, text="人工编辑", command=lambda key=method: self.edit_method(key)).pack(side=tk.LEFT, padx=2)
            if method == "omnicloudmask":
                ttk.Button(buttons, text="运行本景", command=self.run_omnicloudmask).pack(side=tk.LEFT, padx=2)
            self.image_labels[method] = label
            self.metric_labels[method] = metric

        actions = ttk.LabelFrame(compare_frame, text="复核操作", padding=8)
        actions.pack(fill=tk.X, pady=(8, 0))
        all_cloud_button = ttk.Button(
            actions, text="全当前类型", command=lambda: self.create_constant_mask(True)
        )
        all_cloud_button.pack(side=tk.LEFT, padx=3)
        no_cloud_button = ttk.Button(
            actions, text="无标注", command=lambda: self.create_constant_mask(False)
        )
        no_cloud_button.pack(side=tk.LEFT, padx=3)
        self.database_action_buttons.extend([all_cloud_button, no_cloud_button])
        ttk.Button(actions, text="跳过当前", command=self.skip_current).pack(side=tk.LEFT, padx=3)
        ttk.Button(actions, text="跳过当天", command=self.skip_day).pack(side=tk.LEFT, padx=3)
        ttk.Button(actions, text="加入典型数据集", command=self.add_typical).pack(side=tk.LEFT, padx=3)
        ttk.Label(actions, text="标注类型").pack(side=tk.LEFT, padx=(15, 3))
        label_values = tuple(
            label
            for label in self.config_data.annotation_types
            if label in LABEL_VALUES
        ) or tuple(LABEL_VALUES)
        ttk.Combobox(
            actions,
            textvariable=self.label_type,
            state="readonly",
            width=8,
            values=label_values,
        ).pack(side=tk.LEFT, padx=3)
        ttk.Label(actions, text="0背景 1云 2雪 3其他").pack(side=tk.LEFT, padx=(3, 8))
        ttk.Label(actions, text="备注").pack(side=tk.LEFT, padx=(15, 3))
        ttk.Entry(actions, textvariable=self.note).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(actions, textvariable=self.progress, style="Metric.TLabel").pack(side=tk.RIGHT, padx=8)
        body.forget(compare_frame)

        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(7, 0))
        ttk.Label(footer, textvariable=self.status).pack(side=tk.LEFT)
        ttk.Label(
            footer,
            text="主软件负责扫描和统计；标注请点击“选图模块”。",
        ).pack(side=tk.RIGHT)
        log_frame = ttk.LabelFrame(outer, text="扫描日志", padding=6)
        log_frame.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(
            log_frame,
            textvariable=self.scan_log_text,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(
            log_frame,
            text="打开日志目录",
            command=self.open_scan_log_folder,
        ).pack(side=tk.RIGHT, padx=4)
        # 快捷键和标注操作由独立选图模块负责，主窗口保持扫描/统计轻量。

    def current_label_type(self) -> str:
        return normalize_label_type(self.label_type.get())

    def open_picker_module(self) -> None:
        self.stop_omni_queue("打开选图模块，已暂停 Omni 队列")
        command = [
            sys.executable,
            "-m",
            "cloud_review.picker",
            "--config",
            str(CONFIG_PATH),
            "--state",
            str(STATE_PATH),
            "--batch",
            self.review_batch_id,
        ]
        try:
            subprocess.Popen(
                command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.status.set(f"已打开选图模块：{self.review_batch_id}")
        except Exception as exc:
            messagebox.showerror("选图模块启动失败", str(exc))

    def open_history_picker_module(self) -> None:
        self.stop_omni_queue("打开历史选图，已暂停 Omni 队列")
        command = [
            sys.executable,
            "-m",
            "cloud_review.picker",
            "--config",
            str(CONFIG_PATH),
            "--state",
            str(STATE_PATH),
            "--history",
        ]
        try:
            subprocess.Popen(
                command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.status.set("已打开历史选图模块")
        except Exception as exc:
            messagebox.showerror("历史选图启动失败", str(exc))

    def toggle_omni_queue(self) -> None:
        if self.omni_queue_running or self.omni_queue_manual:
            self.stop_omni_queue("已请求停止 Omni 队列")
            return
        self.omni_queue_manual = True
        self.omni_queue_stop_requested = False
        self.start_omni_queue(manual=True)

    def stop_omni_queue(self, message: str = "") -> None:
        self.omni_queue_manual = False
        self.omni_queue_stop_requested = True
        if hasattr(self, "omni_queue_button"):
            self.omni_queue_button.configure(text="开始 Omni 队列")
        if message:
            self.status.set(message)

    def auto_omni_queue_tick(self) -> None:
        if (
            self.config_data.auto_run_omnicloudmask_for_review
            and auto_omnicloudmask_allowed(self.config_data)
            and not self.omni_queue_running
            and not self.omni_queue_stop_requested
        ):
            self.start_omni_queue(manual=False)
        if not auto_omnicloudmask_allowed(self.config_data) and not self.omni_queue_manual:
            self.omni_queue_stop_requested = False
        self.after(5 * 60 * 1000, self.auto_omni_queue_tick)

    def start_omni_queue(self, manual: bool) -> None:
        if self.omni_queue_running:
            return
        self.omni_queue_running = True
        self.omni_queue_stop_requested = False
        if hasattr(self, "omni_queue_button"):
            self.omni_queue_button.configure(text="停止 Omni 队列")
        mode = "手动" if manual else "夜间自动"
        self.status.set(f"{mode} Omni 队列已启动")

        def worker() -> None:
            processed = 0
            last_error = ""
            try:
                while not self.omni_queue_stop_requested:
                    if not manual and not auto_omnicloudmask_allowed(self.config_data):
                        break
                    scenes = self.store.pending_omnicloudmask_queue(limit=50)
                    if not scenes:
                        break
                    scene = scenes[0]
                    try:
                        found_cached = attach_cached_omnicloudmask_result(
                            scene,
                            self.config_data,
                        )
                        if found_cached:
                            mask_path = scene.masks["omnicloudmask"]
                        else:
                            mask_path = run_omnicloudmask_for_scene(scene, self.config_data)
                            attach_omnicloudmask_result(scene, mask_path, self.config_data)
                        cache_path = str(
                            scene.cloud_ratios.get("review_cache_path") or ""
                        )
                        if cache_path:
                            cache_path = cache_review_scene(
                                scene,
                                self.config_data,
                                root_override=cache_path,
                            )
                            scene.cloud_ratios["review_cache_path"] = cache_path
                        elif scene.status in {"pending", "manual"}:
                            cache_path = cache_review_scene(scene, self.config_data)
                            scene.cloud_ratios["review_cache_path"] = cache_path
                        self.store.upsert_scenes([scene])
                        processed += 1
                        self.after(
                            0,
                            lambda sid=scene.scene_id, count=processed: self.status.set(
                                f"Omni 队列：已完成 {count} 景，当前 {sid}"
                            ),
                        )
                    except Exception as exc:
                        last_error = str(exc)
                        scene.cloud_ratios["omnicloudmask_error"] = last_error[-500:]
                        self.store.upsert_scenes([scene])
                        break
            finally:
                self.after(0, lambda: self.omni_queue_finished(processed, last_error))

        threading.Thread(target=worker, daemon=True).start()

    def omni_queue_finished(self, processed: int, last_error: str = "") -> None:
        self.omni_queue_running = False
        if not self.omni_queue_manual and not auto_omnicloudmask_allowed(self.config_data):
            self.omni_queue_stop_requested = False
        if hasattr(self, "omni_queue_button"):
            self.omni_queue_button.configure(text="开始 Omni 队列")
        if last_error:
            self.status.set(f"Omni 队列暂停：已完成 {processed} 景，错误：{last_error}")
        elif processed:
            self.status.set(f"Omni 队列完成：本轮生成 {processed} 景")
        elif self.omni_queue_stop_requested:
            self.status.set("Omni 队列已停止")
        else:
            self.status.set("Omni 队列暂无需要生成的待复核景")

    def shortcut_allowed(self, event: tk.Event) -> bool:
        widget = getattr(event, "widget", None)
        if widget is None:
            return False
        try:
            if widget.winfo_toplevel() is not self:
                return False
            if widget.winfo_class() in {
                "Entry",
                "TEntry",
                "Text",
                "Spinbox",
                "TSpinbox",
                "TCombobox",
            }:
                return False
        except tk.TclError:
            return False
        return not (int(getattr(event, "state", 0)) & (0x0004 | 0x0008))

    def shortcut_action_from_event(self, event: tk.Event) -> str:
        keysym = str(getattr(event, "keysym", "") or "").lower()
        char = str(getattr(event, "char", "") or "").lower()
        key = char or keysym
        mapping = {
            "1": "combined",
            "kp_1": "combined",
            "2": "rdc",
            "kp_2": "rdc",
            "3": "omnicloudmask",
            "kp_3": "omnicloudmask",
            "space": "skip",
            " ": "skip",
            "q": "all_cloud",
            "w": "no_cloud",
        }
        return mapping.get(keysym, mapping.get(key, ""))

    def handle_global_key(self, event: tk.Event) -> str | None:
        action = self.shortcut_action_from_event(event)
        if not action:
            return None
        return self.handle_shortcut(event, action)

    def handle_shortcut(self, event: tk.Event, action: str) -> str | None:
        if not self.shortcut_allowed(event):
            return None
        if not self.current_scene():
            self.status.set("当前没有可操作的待复核影像。")
            return "break"
        if action == "skip":
            self.skip_current()
        elif action == "no_cloud":
            self.create_constant_mask(False)
        elif action == "all_cloud":
            self.create_constant_mask(True)
        else:
            self.accept_method(action)
        return "break"

    def validate_database(self) -> tuple[bool, str]:
        value = self.config_data.database_dir.strip()
        if not value:
            return False, "未配置入库实体路径"
        path = Path(value).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".cloud_review_write_test"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
        except Exception as exc:
            self.mysql_store = None
            return False, f"入库实体路径不可写：{exc}"
        try:
            mysql_store = MySQLDatasetStore(self.config_data)
            mysql_ok, mysql_message = mysql_store.test_connection()
            if not mysql_ok:
                self.mysql_store = None
                return False, f"入库实体路径可写；MySQL 连接失败：{mysql_message}"
            self.mysql_store = mysql_store
            return True, f"入库实体路径：{path}；真实数据库：{mysql_message}"
        except Exception as exc:
            self.mysql_store = None
            return False, f"入库实体路径可写；MySQL 配置无效：{exc}"

    def check_environment(self, show_dialog: bool = True) -> bool:
        roots = [Path(value) for value in self.config_data.watch_roots if value]
        valid_roots = [path for path in roots if path_is_dir(path)]
        self.database_ready, database_message = self.validate_database()
        self.environment_ready = bool(valid_roots)
        if self.database_ready and self.environment_ready:
            message = (
                f"环境已就绪 | 盘阵 {len(valid_roots)} 个 | 数据库可写"
            )
        elif self.environment_ready:
            message = "测试模式 | 盘阵可读，但未配置可写成果数据库"
        else:
            message = "环境未就绪 | 请先设置有效盘阵路径"
        self.environment_status.set(message)
        self.status.set(f"{message}。确认后点击“扫描一次”或“开始监控”。")
        self.update_action_states()
        if show_dialog:
            details = [
                message,
                "",
                "有效盘阵路径：",
                *([str(path) for path in valid_roots] or ["无"]),
                "",
                f"数据库：{database_message}",
            ]
            messagebox.showinfo("环境检查", "\n".join(details))
        return self.environment_ready

    def update_action_states(self) -> None:
        database_state = tk.NORMAL if self.database_ready else tk.DISABLED
        for method in getattr(self, "accept_buttons", {}).values():
            method.configure(state=database_state)
        for button in getattr(self, "database_action_buttons", []):
            button.configure(state=database_state)
        scan_state = (
            tk.NORMAL if self.environment_ready and not self.scan_running else tk.DISABLED
        )
        if hasattr(self, "scan_button"):
            self.scan_button.configure(state=scan_state)
        if hasattr(self, "monitor_button"):
            self.monitor_button.configure(
                state=tk.NORMAL if self.environment_ready else tk.DISABLED
            )
        if hasattr(self, "export_dataset_button"):
            self.export_dataset_button.configure(
                state=tk.NORMAL
                if self.database_ready and not self.dataset_export_running
                else tk.DISABLED
            )

    def toggle_monitoring(self) -> None:
        if self.monitor_enabled.get():
            self.monitor_enabled.set(False)
            self.monitor_button.configure(text="开始监控")
            self.status.set("持续监控已停止；正在运行的本次扫描会自行完成。")
            return
        if not self.environment_ready:
            messagebox.showwarning("环境未就绪", "请先在“设置”中配置有效盘阵路径。")
            return
        self.monitor_enabled.set(True)
        self.monitor_session_id = f"monitor_{datetime.now().strftime('%Y%m%d%H%M%S')}_{time.time_ns()}"
        self.monitor_session_path = None
        self.monitor_button.configure(text="停止监控")
        self.status.set("持续监控已启动。")
        self.scan_now(silent=True)

    def schedule_next_monitor_scan(self, wait_until_next_day: bool = False) -> None:
        if not self.monitor_enabled.get():
            return
        if wait_until_next_day:
            now = datetime.now()
            next_time = datetime(now.year, now.month, now.day) + timedelta(days=1, minutes=5)
            delay = max(60000, int((next_time - now).total_seconds() * 1000))
        else:
            delay = 1000
        self.after(delay, self.monitor_scan_due)

    def monitor_scan_due(self) -> None:
        if self.monitor_enabled.get() and not self.scan_running:
            self.scan_now(silent=True)

    def scan_now(self, silent: bool = False) -> None:
        if self.scan_running:
            return
        if not self.environment_ready:
            if not silent:
                messagebox.showwarning(
                    "环境未就绪",
                    "请先配置路径并点击“检查环境”。未配置成果数据库时可扫描测试，但不能入库。",
                )
            return
        self.scan_running = True
        self.update_action_states()
        self.status.set("正在扫描盘阵并计算掩膜差异...")
        self.current_scan_run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{time.time_ns()}"
        cache_dir = Path(self.config_data.cache_dir).expanduser().resolve()
        self.scan_result_path = cache_dir / f"scan_result_{self.current_scan_run_id}.json"
        self.scan_progress_path = cache_dir / f"scan_progress_{self.current_scan_run_id}.json"
        self.scan_config_path = cache_dir / f"scan_config_{self.current_scan_run_id}.json"
        self.scan_log_path = cache_dir / f"scan_{self.current_scan_run_id}.log"
        self.scan_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.scan_config_path.write_text(
            json.dumps(asdict(self.config_data), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.scan_result_path.unlink(missing_ok=True)
        self.scan_progress_path.unlink(missing_ok=True)
        self.scan_log_path = self.resolve_scan_log_path()
        self.scan_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.scan_log_path.write_text("", encoding="utf-8")
        self.scan_log_text.set(f"扫描日志：{self.scan_log_path}")
        self.last_review_queue_refresh_count = 0
        command = [
            sys.executable,
            "-m",
            "cloud_review.scan_worker",
            "--config",
            str(self.scan_config_path),
            "--state",
            str(STATE_PATH),
            "--result",
            str(self.scan_result_path),
            "--progress",
            str(self.scan_progress_path),
            "--log",
            str(self.scan_log_path),
            "--run-id",
            self.current_scan_run_id,
        ]
        command.extend(["--session-id", self.review_batch_id])
        try:
            self.scan_process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            self.scan_failed(exc)
            return
        self.after(250, lambda: self.poll_scan_process(silent))

    def poll_scan_process(self, silent: bool) -> None:
        if not self.scan_process:
            return
        self.update_scan_progress()
        if self.scan_process.poll() is None:
            self.after(600, lambda: self.poll_scan_process(silent))
            return
        try:
            payload = json.loads(self.scan_result_path.read_text(encoding="utf-8"))
            if payload.get("run_id") != self.current_scan_run_id:
                raise RuntimeError("忽略了非本轮扫描结果，请重新扫描。")
            if not payload.get("ok"):
                raise RuntimeError(payload.get("error", "未知扫描错误"))
            self.scan_finished(
                int(payload["count"]),
                int(payload["removed"]),
                int(payload["freed"]),
                silent,
                payload,
            )
        except Exception as exc:
            self.scan_failed(exc)

    def read_scan_progress(self) -> dict[str, object] | None:
        try:
            if self.scan_progress_path.exists():
                payload = json.loads(self.scan_progress_path.read_text(encoding="utf-8"))
                if payload.get("run_id") != self.current_scan_run_id:
                    return None
                return payload
        except Exception:
            return None
        return None

    def update_scan_progress(self) -> None:
        payload = self.read_scan_progress()
        if not payload or not payload.get("ok"):
            return
        count = int(payload.get("count") or 0)
        review_count = int(payload.get("review_count") or 0)
        date_dirs = int(payload.get("date_dirs_scanned") or 0)
        satellite_dirs = int(payload.get("satellite_dirs_scanned") or 0)
        scene_dirs = int(payload.get("scene_dirs_scanned") or payload.get("directories_scanned") or 0)
        known = int(payload.get("known_skipped") or 0)
        groups = int(payload.get("candidate_groups") or 0)
        emitted = int(payload.get("emitted") or 0)
        elapsed = float(payload.get("elapsed_seconds") or 0.0)
        avg_scene = float(payload.get("avg_scene_seconds") or 0.0)
        scene_per_second = float(payload.get("scene_per_second") or 0.0)
        workers = int(payload.get("scan_workers") or 1)
        session_seen = int(payload.get("session_seen") or 0)
        session_written = int(payload.get("session_written") or 0)
        current_scene = str(payload.get("current_scene") or "")
        current_decision = str(payload.get("current_decision") or "")
        current_difference = str(payload.get("current_difference") or "")
        group_date = str(payload.get("scan_group_date") or "")
        group_satellite = str(payload.get("scan_group_satellite") or "")
        last_log = str(payload.get("last_log") or "")
        log_path = str(payload.get("log_path") or self.scan_log_path)
        if last_log:
            self.scan_log_text.set(f"扫描日志：{last_log}    | 文件：{log_path}")
        scene_text = current_scene or "暂无"
        decision_text = current_decision or "等待结果"
        difference_text = f" | {current_difference}" if current_difference else ""
        self.status.set(
            f"扫描中：分组 {group_date}/{group_satellite}，本轮已扫描景目录 {scene_dirs} 个，本轮新增待复核 {review_count} 景，"
            f"跳过已处理 {known} 景，日期目录 {date_dirs}，卫星目录 {satellite_dirs}；"
            f"{workers}线程，耗时 {elapsed:.1f}s，平均 {avg_scene:.2f}s/景，"
            f"{scene_per_second:.2f} 景/秒；本次监控已记录 {session_seen} 景，"
            f"本轮新增游标 {session_written} 景；当前景 {scene_text} | {decision_text}{difference_text}"
        )
        if review_count > self.last_review_queue_refresh_count:
            self.last_review_queue_refresh_count = review_count
            self.refresh_queue_preserve_current()
            self.update_summary()

    def open_scan_log_folder(self) -> None:
        path = self.scan_log_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path.parent))
        except Exception as exc:
            messagebox.showerror("无法打开日志目录", str(exc))

    def scan_finished(
        self,
        count: int,
        removed: int,
        freed: int,
        silent: bool,
        scan_stats: dict[str, object] | None = None,
    ) -> None:
        self.scan_running = False
        self.scan_process = None
        self.update_action_states()
        self.update_summary()
        self.refresh_queue_preserve_current()
        scan_stats = scan_stats or {}
        if scan_stats.get("all_groups_completed"):
            if self.monitor_enabled.get():
                self.status.set("当前扫描窗口内所有日期/卫星分组已完成，等待到明天 00:05 后继续。")
                self.schedule_next_monitor_scan(wait_until_next_day=True)
            else:
                self.status.set("当前扫描窗口内所有日期/卫星分组已经扫描完成。")
            return
        limited_text = "；达到本轮上限，将在下轮继续" if scan_stats.get("limited") else ""
        self.status.set(
            f"扫描完成：分组 {scan_stats.get('scan_group_date', '')}/{scan_stats.get('scan_group_satellite', '')}，"
            f"本轮已扫描景目录 {scan_stats.get('scene_dirs_scanned', scan_stats.get('directories_scanned', 0))} 个，"
            f"本轮新增待复核 {scan_stats.get('review_count', 0)} 景，跳过已处理 {scan_stats.get('known_skipped', 0)} 景，"
            f"日期目录 {scan_stats.get('date_dirs_scanned', 0)} 个，"
            f"卫星目录 {scan_stats.get('satellite_dirs_scanned', 0)} 个{limited_text}；"
            f"{scan_stats.get('scan_workers', 1)}线程，耗时 {float(scan_stats.get('elapsed_seconds', 0)):.1f}s，"
            f"平均 {float(scan_stats.get('avg_scene_seconds', 0)):.2f}s/景；"
            f"本次监控已记录 {scan_stats.get('session_seen', 0)} 景；"
            f"清理缓存 {removed} 个文件 / {freed / 1024 / 1024:.1f} MB"
        )
        if not silent and count == 0 and not self.queue:
            messagebox.showinfo("扫描完成", "未发现完整的“影像 + 综合法 + RDC”数据组合。")
        self.schedule_next_monitor_scan()

    def scan_failed(self, exc: Exception) -> None:
        self.scan_running = False
        self.scan_process = None
        self.update_action_states()
        self.status.set(f"扫描失败：{exc}")
        messagebox.showerror("扫描失败", str(exc))
        self.schedule_next_monitor_scan()

    def refresh_queue(self) -> None:
        self.config_data.queue_strategy = self.strategy.get()
        self.config_data.daily_review_limit = max(1, self.limit.get())
        current_id = self.current_scene().scene_id if self.current_scene() else ""
        raw_queue = self.store.review_queue(self.strategy.get(), self.review_batch_id)
        self.queue, hidden_missing = self.filter_loadable_queue(raw_queue)
        if hasattr(self, "queue_frame"):
            suffix = f"，隐藏 {hidden_missing} 景" if hidden_missing else ""
            self.queue_frame.configure(text=f"待复核队列（显示 {len(self.queue)} 景{suffix}）")
        if current_id:
            self.index = next(
                (position for position, scene in enumerate(self.queue) if scene.scene_id == current_id),
                0,
            )
        else:
            self.index = 0
        self.tree.delete(*self.tree.get_children())
        for index, scene in enumerate(self.queue):
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                text=scene.scene_id,
                values=(
                    scene.acquired_date,
                    f"{scene.difference:.1%}",
                    f"{scene.difference_percent:.1%}",
                    f"{scene.anomaly_score:.2f}",
                    f"{scene.iou:.2f}",
                ),
            )
        self.update_summary()
        self.progress.set(f"本次新增待选图 {len(self.queue)} 景")
        return
        if hidden_missing:
            self.status.set(
                f"数据库待复核 {len(raw_queue)} 景，当前可加载显示 {len(self.queue)} 景；"
                f"已隐藏 {hidden_missing} 景原图路径不存在的旧记录，请确认盘阵路径是否在线。"
            )
        if self.queue:
            self.tree.selection_set("0")
            self.show_scene(0)
        else:
            self.clear_scene()

    def refresh_queue_preserve_current(self) -> None:
        current_id = self.current_scene().scene_id if self.current_scene() else ""
        self.config_data.queue_strategy = self.strategy.get()
        self.config_data.daily_review_limit = max(1, self.limit.get())
        raw_queue = self.store.review_queue(self.strategy.get(), self.review_batch_id)
        self.queue, hidden_missing = self.filter_loadable_queue(raw_queue)
        self.index = next(
            (position for position, scene in enumerate(self.queue) if scene.scene_id == current_id),
            min(self.index, max(0, len(self.queue) - 1)),
        )
        self.rebuild_tree(hidden_missing)
        self.update_summary()
        self.progress.set(f"本次新增待选图 {len(self.queue)} 景")
        return
        if hidden_missing:
            self.status.set(
                f"数据库待复核 {len(raw_queue)} 景，当前可加载显示 {len(self.queue)} 景；"
                f"已隐藏 {hidden_missing} 景原图路径不存在的旧记录，请确认盘阵路径是否在线。"
            )

    def filter_loadable_queue(self, scenes: list[Scene]) -> tuple[list[Scene], int]:
        loadable: list[Scene] = []
        hidden = 0
        for scene in scenes:
            cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
            cache_ok = bool(cache_path) and path_is_dir(cache_path)
            image_ok = path_exists(scene.image_path)
            if cache_ok and image_ok:
                loadable.append(scene)
            else:
                hidden += 1
                self.store.mark_cache_missing(scene.scene_id)
        return loadable, hidden

    def show_queue_context_menu(self, event: tk.Event) -> None:
        item_id = self.tree.identify_row(event.y)
        if item_id:
            self.tree.selection_set(item_id)
            try:
                index = int(item_id)
                if index != self.index:
                    self.index = index
            except ValueError:
                pass
        scene = self.current_scene()
        if not scene:
            return
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label="复制景号",
            command=lambda value=scene.scene_id: self.copy_to_clipboard(value),
        )
        menu.add_command(
            label="复制原图路径",
            command=lambda value=scene.image_path: self.copy_to_clipboard(value),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def copy_to_clipboard(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status.set(f"已复制：{value}")

    def update_summary(self) -> None:
        counts = self.store.counts()
        pending = (
            counts.get("pending", 0)
            + counts.get("manual", 0)
        )
        batch_pending = self.store.review_batch_count(self.review_batch_id)
        self.summary.set(
            f"本次新增待复核 {batch_pending} | 全部待复核 {pending} | 待入库 {counts.get('pending_import', 0)} | "
            f"已入库 {counts.get('accepted', 0)} | 数据集 {counts.get('dataset', 0)}"
        )
        if hasattr(self, "pending_button"):
            self.pending_button.configure(
                text=f"待入库管理 ({counts.get('pending_import', 0)})"
            )

    def show_scan_progress_records_window(self, month_filter: str = "") -> None:
        records = self.store.scan_group_records()
        dialog = tk.Toplevel(self)
        dialog.title("扫描分组进度统计")
        dialog.geometry("1180x720")
        dialog.transient(self)

        outer = ttk.Frame(dialog, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        sync_status = tk.StringVar(value=f"本地进度记录 {len(records)} 组")
        ttk.Label(toolbar, textvariable=sync_status).pack(side=tk.LEFT)
        month_values = sorted(
            {
                str(row.get("group_date") or "")[:7]
                for row in records
                if str(row.get("group_date") or "")
            },
            reverse=True,
        )
        selected_month = tk.StringVar(
            value=month_filter or (month_values[0] if month_values else "")
        )
        ttk.Label(toolbar, text="月份").pack(side=tk.LEFT, padx=(16, 3))
        month_combo = ttk.Combobox(
            toolbar,
            textvariable=selected_month,
            state="readonly",
            values=month_values,
            width=10,
        )
        month_combo.pack(side=tk.LEFT, padx=3)

        def sync_groups_async() -> None:
            sync_status.set("正在后台同步盘阵日期/卫星分组...")

            def worker() -> None:
                try:
                    groups = discover_scan_groups(self.config_data)
                    self.store.sync_scan_groups(groups)
                    self.after(
                        0,
                        lambda: (
                            dialog.destroy(),
                            self.show_scan_progress_records_window(),
                        ),
                    )
                except Exception as exc:
                    self.after(
                        0,
                        lambda error=exc: messagebox.showerror(
                            "进度分组同步失败", str(error), parent=dialog
                        ),
                    )

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(toolbar, text="同步盘阵分组", command=sync_groups_async).pack(
            side=tk.RIGHT
        )
        ttk.Label(
            outer,
            text="绿色=完成，黄色=进行中，红色=未完成/待扫描；右键单元格可清空该组历史，下次重新扫描。",
        ).pack(fill=tk.X, pady=(0, 8))

        canvas = tk.Canvas(outer, highlightthickness=0, background="#ffffff")
        xbar = ttk.Scrollbar(outer, orient=tk.HORIZONTAL, command=canvas.xview)
        ybar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        xbar.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        grid = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=grid, anchor=tk.NW)

        display_records = [
            row
            for row in records
            if not selected_month.get()
            or str(row.get("group_date") or "").startswith(selected_month.get())
        ]
        dates = sorted({str(row["group_date"]) for row in display_records})
        satellites = sorted({str(row["satellite"]) for row in display_records})
        by_key = {
            (str(row["group_date"]), str(row["satellite"])): row
            for row in display_records
        }
        colors = {
            "completed": "#92d050",
            "in_progress": "#ffd966",
            "pending": "#ff9999",
            "failed": "#ff9999",
        }
        names = {
            "completed": "完成",
            "in_progress": "进行中",
            "pending": "未完成",
            "failed": "失败",
        }

        def reset_group_record(record: dict[str, object]) -> None:
            group_date = str(record.get("group_date") or "")
            satellite = str(record.get("satellite") or "")
            path = str(record.get("path") or "")
            if not messagebox.askyesno(
                "清空扫描历史",
                f"确定清空 {group_date}/{satellite} 的扫描历史吗？\n下次扫描会重新检查这一组。",
                parent=dialog,
            ):
                return
            count = self.store.reset_scan_group_record(group_date, satellite, path)
            self.status.set(f"已清空 {group_date}/{satellite} 扫描历史 {count} 条")
            dialog.destroy()
            self.show_scan_progress_records_window()

        def show_group_menu(event: tk.Event, record: dict[str, object]) -> None:
            menu = tk.Menu(dialog, tearoff=False)
            menu.add_command(
                label="清空此分组记录，下次重新扫描",
                command=lambda item=record: reset_group_record(item),
            )
            menu.tk_popup(event.x_root, event.y_root)

        def select_group(record: dict[str, object]) -> None:
            self.status.set(
                f"进度分组：{record.get('group_date')}/{record.get('satellite')} | "
                f"状态 {record.get('status')} | "
                f"总 {record.get('total_scenes')} / 待 {record.get('review_scenes')} | "
                f"{record.get('path')}"
            )

        def make_cell(
            row: int,
            column: int,
            text: str,
            bg: str,
            record: dict[str, object] | None = None,
        ) -> None:
            label = tk.Label(
                grid,
                text=text,
                bg=bg,
                fg="#111111",
                relief=tk.RIDGE,
                borderwidth=1,
                padx=6,
                pady=5,
                justify=tk.CENTER,
                width=18 if column else 12,
            )
            label.grid(row=row, column=column, sticky="nsew")
            if record:
                label.bind("<Button-1>", lambda _event, item=record: select_group(item))
                label.bind("<Button-3>", lambda event, item=record: show_group_menu(event, item))
                label.bind("<Control-Button-1>", lambda event, item=record: show_group_menu(event, item))

        make_cell(0, 0, "日期", "#d9e2f3")
        for column, satellite in enumerate(satellites, start=1):
            make_cell(0, column, satellite, "#d9e2f3")
        for row_index, group_date in enumerate(dates, start=1):
            make_cell(row_index, 0, group_date, "#eef2f8")
            for column, satellite in enumerate(satellites, start=1):
                record = by_key.get((group_date, satellite))
                if not record:
                    make_cell(row_index, column, "-", "#f2f2f2")
                    continue
                status = str(record.get("status") or "pending")
                total = int(record.get("total_scenes") or 0)
                review = int(record.get("review_scenes") or 0)
                text = f"{names.get(status, status)}\n总{total} / 待{review}"
                make_cell(row_index, column, text, colors.get(status, "#ff9999"), record)

        def update_scrollregion(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_width(event: tk.Event) -> None:
            if not satellites:
                canvas.itemconfigure(window_id, width=event.width)

        grid.bind("<Configure>", update_scrollregion)
        canvas.bind("<Configure>", fit_width)

        def reload_month(_event: tk.Event | None = None) -> None:
            dialog.destroy()
            self.show_scan_progress_records_window(selected_month.get())

        month_combo.bind("<<ComboboxSelected>>", reload_month)

    def show_scan_progress_window(self) -> None:
        self.show_scan_progress_records_window()
        return
        try:
            groups = discover_scan_groups(self.config_data)
            self.store.sync_scan_groups(groups)
        except Exception as exc:
            messagebox.showerror("进度统计失败", str(exc))
            return
        records = self.store.scan_group_records()
        dialog = tk.Toplevel(self)
        dialog.title("扫描分组进度统计")
        dialog.geometry("1180x720")
        dialog.transient(self)
        outer = ttk.Frame(dialog, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer,
            text="绿色=已完成，黄色=进行中，红色=未完成/待扫描；完成格显示：总景数 / 待复核景数。",
            style="Metric.TLabel",
        ).pack(fill=tk.X, pady=(0, 8))
        canvas = tk.Canvas(outer, highlightthickness=0, background="#ffffff")
        xbar = ttk.Scrollbar(outer, orient=tk.HORIZONTAL, command=canvas.xview)
        ybar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        xbar.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        grid = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=grid, anchor=tk.NW)
        dates = sorted({str(row["group_date"]) for row in records})
        satellites = sorted({str(row["satellite"]) for row in records})
        by_key = {
            (str(row["group_date"]), str(row["satellite"])): row
            for row in records
        }

        def reset_group_record(record: dict[str, object]) -> None:
            group_date = str(record.get("group_date") or "")
            satellite = str(record.get("satellite") or "")
            path = str(record.get("path") or "")
            if not messagebox.askyesno(
                "清空扫描历史",
                f"确定清空 {group_date}/{satellite} 的扫描历史吗？\n"
                "清空后下次扫描会重新检查这个分组。",
                parent=dialog,
            ):
                return
            count = self.store.reset_scan_group_record(group_date, satellite, path)
            self.status.set(f"已清空 {group_date}/{satellite} 扫描历史 {count} 条")
            dialog.destroy()
            self.show_progress_window()

        def show_group_menu(event: tk.Event, record: dict[str, object]) -> None:
            menu = tk.Menu(dialog, tearoff=False)
            menu.add_command(
                label="清空此分组记录，下次重新扫描",
                command=lambda item=record: reset_group_record(item),
            )
            menu.tk_popup(event.x_root, event.y_root)

        def select_group(record: dict[str, object]) -> None:
            self.status.set(
                "进度分组："
                f"{record.get('group_date')}/{record.get('satellite')} | "
                f"状态 {record.get('status')} | "
                f"总 {record.get('total_scenes')} / 待 {record.get('review_scenes')} | "
                f"{record.get('path')}"
            )

        def make_cell(
            row: int,
            column: int,
            text: str,
            bg: str,
            record: dict[str, object] | None = None,
        ) -> None:
            label = tk.Label(
                grid,
                text=text,
                bg=bg,
                fg="#111111",
                relief=tk.RIDGE,
                borderwidth=1,
                padx=6,
                pady=5,
                justify=tk.CENTER,
                width=18 if column else 12,
            )
            label.grid(row=row, column=column, sticky="nsew")
            if record:
                label.bind("<Button-1>", lambda _event, item=record: select_group(item))
                label.bind("<Button-3>", lambda event, item=record: show_group_menu(event, item))
                label.bind("<Control-Button-1>", lambda event, item=record: show_group_menu(event, item))

        make_cell(0, 0, "日期", "#d9e2f3")
        for column, satellite in enumerate(satellites, start=1):
            make_cell(0, column, satellite, "#d9e2f3")
        colors = {
            "completed": "#92d050",
            "in_progress": "#ffd966",
            "pending": "#ff9999",
            "failed": "#ff9999",
        }
        names = {
            "completed": "完成",
            "in_progress": "进行中",
            "pending": "未完成",
            "failed": "未完成",
        }
        for row_index, group_date in enumerate(dates, start=1):
            make_cell(row_index, 0, group_date, "#eef2f8")
            for column, satellite in enumerate(satellites, start=1):
                record = by_key.get((group_date, satellite))
                if not record:
                    make_cell(row_index, column, "-", "#f2f2f2")
                    continue
                status = str(record.get("status") or "pending")
                total = int(record.get("total_scenes") or 0)
                review = int(record.get("review_scenes") or 0)
                text = f"{names.get(status, status)}\n总{total} / 待{review}"
                make_cell(row_index, column, text, colors.get(status, "#ff9999"), record)

        def update_scrollregion(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_width(event: tk.Event) -> None:
            if not satellites:
                canvas.itemconfigure(window_id, width=event.width)

        grid.bind("<Configure>", update_scrollregion)
        canvas.bind("<Configure>", fit_width)

    def create_blank_mask(self, scene: Scene, method: str) -> str:
        cache_root = Path(self.config_data.cache_dir).expanduser().resolve()
        target_dir = cache_root / "manual_masks" / scene.scene_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{scene.scene_id}_{method}_blank.tif"
        with open_image_file(scene.image_path) as image:
            mask = Image.new("L", image.size, 0)
            mask.save(target, compression="tiff_lzw")
        return str(target)

    def add_scenes_to_review_queue(self, scenes: list[Scene]) -> int:
        unique: list[Scene] = []
        seen: set[str] = set()
        failures: list[str] = []
        for scene in scenes:
            if scene.scene_id in seen:
                continue
            scene.status = "manual"
            scene.review_batch = self.review_batch_id
            try:
                cache_path = cache_review_scene(scene, self.config_data, "manual")
                scene.cloud_ratios["review_cache_path"] = cache_path
            except Exception as exc:
                failures.append(f"{scene.scene_id}: {exc}")
                continue
            unique.append(scene)
            seen.add(scene.scene_id)
        if not unique:
            if failures:
                messagebox.showerror(
                    "加入待复核失败",
                    "缓存到 review 目录失败，未加入待复核：\n" + "\n".join(failures[:10]),
                    parent=self,
                )
            return 0
        self.store.upsert_scenes(unique)
        for scene in unique:
            self.store.mark_manual(scene.scene_id)
        self.refresh_queue_preserve_current()
        self.status.set(f"已将 {len(unique)} 景加入待复核队列")
        return len(unique)

    def render_scene_preview(
        self, scene: Scene, method: str | None, size: tuple[int, int]
    ) -> Image.Image:
        return self.render_scene_previews(scene, (method,), size)[method]

    def render_scene_previews(
        self, scene: Scene, methods: tuple[str | None, ...], size: tuple[int, int]
    ) -> dict[str | None, Image.Image]:
        with open_image_file(scene.image_path) as source:
            rgb = source.convert("RGB")
            rgb.thumbnail(size, Image.Resampling.LANCZOS)
            base = rgb.copy()
        previews: dict[str | None, Image.Image] = {}
        for method in methods:
            preview = base.copy()
            if method:
                mask_path = scene.masks.get(method)
                if mask_path and path_exists(mask_path):
                    with open_image_file(mask_path) as mask:
                        resized = mask.convert("L").resize(
                            preview.size, Image.Resampling.NEAREST
                        )
                    preview = overlay(preview, resized).convert("RGB")
            previews[method] = preview
        return previews

    def show_manual_scene_browser(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("手工挑图与补充复核")
        dialog.geometry("1520x920")
        dialog.transient(self)

        date_var = tk.StringVar()
        plan_var = tk.StringVar()
        path_var = tk.StringVar()
        limit_var = tk.IntVar(value=0)
        status_var = tk.StringVar(
            value="按日期、计划号或指定路径搜索产品；可勾选后加入待复核队列。"
        )
        selected_vars: dict[str, tk.BooleanVar] = {}
        scenes_by_id: dict[str, Scene] = {}
        preview_images: list[ImageTk.PhotoImage] = []
        select_all_var = tk.BooleanVar(value=False)

        outer = ttk.Frame(dialog, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        filters = ttk.LabelFrame(outer, text="搜索条件", padding=8)
        filters.pack(fill=tk.X)
        ttk.Label(filters, text="日期").grid(row=0, column=0, sticky=tk.W, padx=3, pady=4)
        ttk.Entry(filters, textvariable=date_var, width=18).grid(
            row=0, column=1, sticky=tk.W, padx=3, pady=4
        )
        ttk.Label(filters, text="计划号").grid(row=0, column=2, sticky=tk.W, padx=3, pady=4)
        ttk.Entry(filters, textvariable=plan_var, width=22).grid(
            row=0, column=3, sticky=tk.W, padx=3, pady=4
        )
        ttk.Label(filters, text="指定路径").grid(row=1, column=0, sticky=tk.W, padx=3, pady=4)
        ttk.Entry(filters, textvariable=path_var, width=88).grid(
            row=1, column=1, columnspan=5, sticky=tk.EW, padx=3, pady=4
        )
        ttk.Label(filters, text="最多显示(0=不限)").grid(row=0, column=4, sticky=tk.W, padx=3, pady=4)
        ttk.Spinbox(filters, from_=0, to=5000, textvariable=limit_var, width=8).grid(
            row=0, column=5, sticky=tk.W, padx=3, pady=4
        )

        def choose_path() -> None:
            selected = filedialog.askdirectory(parent=dialog)
            if selected:
                path_var.set(selected)

        ttk.Button(filters, text="选择路径", command=choose_path).grid(
            row=0, column=6, sticky=tk.W, padx=4, pady=4
        )
        id_button = ttk.Button(filters, text="指定影像ID")
        id_button.grid(row=1, column=6, sticky=tk.W, padx=4, pady=4)
        search_button = ttk.Button(filters, text="开始搜索")
        search_button.grid(row=1, column=7, sticky=tk.E, padx=4, pady=4)
        filters.columnconfigure(5, weight=1)

        ttk.Label(
            outer,
            textvariable=status_var,
            style="Metric.TLabel",
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(8, 6))

        canvas_frame = ttk.Frame(outer)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(canvas_frame, background="#f3f5f7", highlightthickness=0)
        ybar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=ybar.set)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cards_host = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=cards_host, anchor=tk.NW)

        def on_cards_configure(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_configure(event: tk.Event) -> None:
            canvas.itemconfigure(canvas_window, width=event.width)

        cards_host.bind("<Configure>", on_cards_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        def add_selected() -> None:
            selected = [
                scenes_by_id[scene_id]
                for scene_id, variable in selected_vars.items()
                if variable.get() and scene_id in scenes_by_id
            ]
            count = self.add_scenes_to_review_queue(selected)
            status_var.set(f"已加入当前待复核批次 {count} 景。")

        def set_all_selected(value: bool) -> None:
            select_all_var.set(value)
            for variable in selected_vars.values():
                variable.set(value)
            status_var.set(
                f"已{'全选' if value else '取消全选'} {len(selected_vars)} 景。"
            )

        def toggle_all_selected() -> None:
            set_all_selected(select_all_var.get())

        def render_results(scenes: list[Scene], search_seconds: float = 0.0) -> None:
            preview_started = time.perf_counter()
            for child in cards_host.winfo_children():
                child.destroy()
            preview_images.clear()
            selected_vars.clear()
            scenes_by_id.clear()
            select_all_var.set(False)
            if not scenes:
                status_var.set(
                    f"没有找到符合条件的产品。检索 {search_seconds:.2f} 秒。"
                )
                on_cards_configure()
                return
            for index, scene in enumerate(scenes):
                scenes_by_id[scene.scene_id] = scene
                selected_vars[scene.scene_id] = tk.BooleanVar(value=False)
                card = ttk.LabelFrame(cards_host, text=scene.scene_id, padding=6)
                card.grid(
                    row=index // 2,
                    column=index % 2,
                    sticky="nsew",
                    padx=6,
                    pady=6,
                )
                cards_host.columnconfigure(index % 2, weight=1, uniform="manual_card")
                info = (
                    f"日期：{scene.acquired_date}    计划号：{scene.plan_code or '未知'}\n"
                    f"差异差值：{scene.difference:.2%}    "
                    f"差异百分比：{scene.difference_percent:.2%}    "
                    f"IoU：{scene.iou:.3f}    "
                    f"AI：{scene.anomaly_score:.2f} {scene.anomaly_reason}"
                )
                ttk.Label(card, text=info, justify=tk.LEFT).pack(fill=tk.X, pady=(0, 6))
                thumb_row = ttk.Frame(card)
                thumb_row.pack(fill=tk.X)
                rendered_previews = self.render_scene_previews(
                    scene, (None, "combined", "rdc"), (220, 150)
                )
                for caption, method in (
                    ("原图", None),
                    ("综合法", "combined"),
                    ("RDC", "rdc"),
                ):
                    pane = ttk.Frame(thumb_row)
                    pane.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
                    rendered = rendered_previews[method]
                    image = ImageTk.PhotoImage(rendered)
                    preview_images.append(image)
                    ttk.Label(pane, image=image).pack(fill=tk.BOTH, expand=True)
                    mask_text = (
                        caption
                        if method is None or scene.masks.get(method)
                        else f"{caption}（无结果）"
                    )
                    ttk.Label(pane, text=mask_text, anchor=tk.CENTER).pack(fill=tk.X)
                actions = ttk.Frame(card)
                actions.pack(fill=tk.X, pady=(6, 0))
                ttk.Checkbutton(
                    actions,
                    text="加入待复核队列",
                    variable=selected_vars[scene.scene_id],
                ).pack(side=tk.LEFT)
                ttk.Button(
                    actions,
                    text="单景加入",
                    command=lambda item=scene: self.add_scenes_to_review_queue([item]),
                ).pack(side=tk.RIGHT, padx=2)
            on_cards_configure()
            preview_seconds = time.perf_counter() - preview_started
            status_var.set(
                f"共找到 {len(scenes)} 景。检索 {search_seconds:.2f} 秒，"
                f"预览 {preview_seconds:.2f} 秒。可勾选后加入待复核队列。"
            )

        def search_finished(scenes: list[Scene], search_seconds: float) -> None:
            search_button.configure(state=tk.NORMAL)
            id_button.configure(state=tk.NORMAL)
            render_results(scenes, search_seconds)

        def search_failed(exc: Exception) -> None:
            search_button.configure(state=tk.NORMAL)
            id_button.configure(state=tk.NORMAL)
            status_var.set(f"搜索失败：{exc}")
            messagebox.showerror("手工挑图失败", str(exc), parent=dialog)

        def open_id_dialog() -> None:
            id_dialog = tk.Toplevel(dialog)
            id_dialog.title("指定影像ID")
            id_dialog.geometry("760x430")
            id_dialog.transient(dialog)
            id_dialog.grab_set()
            ttk.Label(
                id_dialog,
                text=(
                    "每行或用逗号/中文逗号/加号分隔一个影像ID；"
                    "输入到 _L1 会自动补成 _L1_MSS。"
                ),
            ).pack(fill=tk.X, padx=10, pady=(10, 6))
            text_box = tk.Text(id_dialog, height=14, wrap=tk.NONE)
            text_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
            if path_var.get().strip():
                scope_text = f"查找范围：{path_var.get().strip()}"
            else:
                scope_text = "查找范围：当前配置的监控根目录"
            ttk.Label(id_dialog, text=scope_text).pack(fill=tk.X, padx=10, pady=(0, 6))

            actions_row = ttk.Frame(id_dialog)
            actions_row.pack(fill=tk.X, padx=10, pady=(0, 10))

            def run_id_search() -> None:
                raw_ids = text_box.get("1.0", tk.END).strip()
                if not raw_ids:
                    messagebox.showinfo("请先输入ID", "请粘贴至少一个影像ID。", parent=id_dialog)
                    return
                id_dialog.destroy()
                search_button.configure(state=tk.DISABLED)
                id_button.configure(state=tk.DISABLED)
                status_var.set("正在按影像ID定位景目录并生成预览，请稍候...")

                def worker() -> None:
                    try:
                        started = time.perf_counter()
                        scenes, missing = search_scene_candidates_by_ids(
                            self.config_data,
                            raw_ids,
                            explicit_path=path_var.get().strip(),
                            limit=max(0, int(limit_var.get())),
                        )
                        search_seconds = time.perf_counter() - started

                        def finish() -> None:
                            search_button.configure(state=tk.NORMAL)
                            id_button.configure(state=tk.NORMAL)
                            render_results(scenes, search_seconds)
                            status_var.set(
                                f"按ID找到 {len(scenes)} 景，未找到 {len(missing)} 景。"
                                + (
                                    " 未找到：" + "，".join(missing[:5])
                                    if missing
                                    else ""
                                )
                            )

                        self.after(0, finish)
                    except Exception as exc:
                        self.after(0, lambda error=exc: search_failed(error))

                threading.Thread(target=worker, daemon=True).start()

            ttk.Button(actions_row, text="开始定位", command=run_id_search).pack(
                side=tk.RIGHT, padx=4
            )
            ttk.Button(actions_row, text="取消", command=id_dialog.destroy).pack(
                side=tk.RIGHT, padx=4
            )

        def run_search() -> None:
            if not (
                date_var.get().strip()
                or plan_var.get().strip()
                or path_var.get().strip()
            ):
                messagebox.showinfo(
                    "请先输入条件",
                    "请至少输入日期、计划号或指定路径中的一个条件。",
                    parent=dialog,
                )
                return
            search_button.configure(state=tk.DISABLED)
            id_button.configure(state=tk.DISABLED)
            status_var.set("正在搜索并生成预览，请稍候...")

            def worker() -> None:
                try:
                    started = time.perf_counter()
                    scenes = search_scene_candidates(
                        self.config_data,
                        acquired_date=date_var.get().strip(),
                        plan_filter=plan_var.get().strip(),
                        explicit_path=path_var.get().strip(),
                        limit=max(0, int(limit_var.get())),
                    )
                    search_seconds = time.perf_counter() - started
                    self.after(
                        0,
                        lambda items=scenes, seconds=search_seconds: search_finished(
                            items, seconds
                        ),
                    )
                except Exception as exc:
                    self.after(0, lambda error=exc: search_failed(error))

            threading.Thread(target=worker, daemon=True).start()

        search_button.configure(command=run_search)
        id_button.configure(command=open_id_dialog)

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Checkbutton(
            actions,
            text="全选当前结果",
            variable=select_all_var,
            command=toggle_all_selected,
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(actions, text="全选", command=lambda: set_all_selected(True)).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(actions, text="全不选", command=lambda: set_all_selected(False)).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(actions, text="勾选加入当前待复核", command=add_selected).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(actions, text="刷新主队列", command=self.refresh_queue).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(actions, text="关闭", command=dialog.destroy).pack(
            side=tk.RIGHT, padx=3
        )

    def show_distribution_stats(self) -> None:
        stats = self.store.distribution_stats()
        dialog = tk.Toplevel(self)
        dialog.title("训练数据空间与时间分布")
        dialog.geometry("900x650")
        text = tk.Text(dialog, wrap=tk.WORD, font=("Consolas", 10))
        scrollbar = ttk.Scrollbar(dialog, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        indexed_total = int(stats["indexed_total"][0]["count"])
        total = int(stats["total"][0]["count"])
        lines = [
            f"盘阵候选池总量：{indexed_total}",
            f"已入库数据总量：{total}",
            "",
        ]
        if self.mysql_store is not None:
            try:
                mysql_counts = self.mysql_store.split_counts()
                lines.extend(
                    [
                        "===== 真实 MySQL 数据库 =====",
                        f"  train: {mysql_counts.get('train', 0)}",
                        f"  val:   {mysql_counts.get('val', 0)}",
                        f"  其他:  {sum(value for key, value in mysql_counts.items() if key not in {'train', 'val'})}",
                        "",
                    ]
                )
            except Exception as exc:
                lines.extend(["===== 真实 MySQL 数据库 =====", f"  查询失败：{exc}", ""])
        labels = {
            "month": "月份分布",
            "season": "季节分布",
            "geo_cell": f"地理网格分布（{self.config_data.geo_grid_degrees:g}°）",
            "satellite": "卫星分布",
            "dataset_split": "训练/验证分布",
            "label_type": "标注类型分布",
        }
        lines.append("===== 盘阵候选池 =====")
        for key in ("month", "season", "geo_cell", "satellite"):
            lines.append(labels[key])
            rows = stats[f"indexed_{key}"]
            if not rows:
                lines.append("  暂无数据")
            else:
                for row in rows[:100]:
                    count = int(row["count"])
                    percent = count / indexed_total * 100 if indexed_total else 0
                    lines.append(
                        f"  {str(row['category']):<24} {count:>7}  {percent:>6.2f}%"
                    )
            lines.append("")
        lines.append("===== 已入库训练数据 =====")
        for key in (
            "month",
            "season",
            "geo_cell",
            "satellite",
            "dataset_split",
            "label_type",
        ):
            lines.append(labels[key])
            rows = stats[key]
            if not rows:
                lines.append("  暂无数据")
            else:
                for row in rows[:100]:
                    count = int(row["count"])
                    percent = count / total * 100 if total else 0
                    lines.append(
                        f"  {str(row['category']):<24} {count:>7}  {percent:>6.2f}%"
                    )
            lines.append("")
        lines.append("时空均衡推荐会优先选择当前数据集中较少的地理网格、月份和季节。")
        text.insert("1.0", "\n".join(lines))
        text.configure(state=tk.DISABLED)

    def show_pending_imports(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("待入库候选集管理")
        dialog.geometry("1180x720")
        dialog.transient(self)

        outer = ttk.Frame(dialog, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer,
            text="候选暂存区：确认无误后再导入正式数据库；选错的数据可在此剔除。",
            style="Metric.TLabel",
        ).pack(fill=tk.X, pady=(0, 8))

        body = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)
        list_frame = ttk.Frame(body)
        preview_frame = ttk.LabelFrame(body, text="候选预览", padding=8)
        body.add(list_frame, weight=3)
        body.add(preview_frame, weight=2)

        tree = ttk.Treeview(
            list_frame,
            columns=("date", "type", "method", "season", "geo", "note"),
            show="tree headings",
            selectmode="extended",
        )
        tree.heading("#0", text="景号")
        tree.heading("date", text="日期")
        tree.heading("type", text="类别")
        tree.heading("method", text="选用结果")
        tree.heading("season", text="季节")
        tree.heading("geo", text="空间网格")
        tree.heading("note", text="备注")
        tree.column("#0", width=300)
        tree.column("date", width=90)
        tree.column("type", width=60)
        tree.column("method", width=130)
        tree.column("season", width=65)
        tree.column("geo", width=100)
        tree.column("note", width=180)
        scrollbar = ttk.Scrollbar(list_frame, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)

        preview_label = ttk.Label(
            preview_frame, text="请选择一条候选数据", anchor=tk.CENTER
        )
        preview_label.pack(fill=tk.BOTH, expand=True)
        detail = ttk.Label(preview_frame, text="", anchor=tk.W, justify=tk.LEFT)
        detail.pack(fill=tk.X, pady=(8, 0))
        preview_images: list[ImageTk.PhotoImage] = []
        items: dict[str, dict[str, object]] = {}

        def reload_items(select_scene_id: str = "") -> None:
            items.clear()
            tree.delete(*tree.get_children())
            for item in self.store.pending_imports():
                scene_id = str(item["scene_id"])
                items[scene_id] = item
                tree.insert(
                    "",
                    tk.END,
                    iid=scene_id,
                    text=scene_id,
                    values=(
                        item["acquired_date"],
                        label_name(str(item.get("label_type") or "cloud")),
                        METHOD_LABELS.get(
                            str(item["source_method"]), str(item["source_method"])
                        ),
                        item["season"],
                        item["geo_cell"],
                        item["note"],
                    ),
                )
            if select_scene_id and tree.exists(select_scene_id):
                tree.selection_set(select_scene_id)
                tree.see(select_scene_id)
            elif tree.get_children():
                first = tree.get_children()[0]
                tree.selection_set(first)
                tree.see(first)
            else:
                preview_images.clear()
                preview_label.configure(image="", text="待入库区为空")
                detail.configure(text="")
            self.update_summary()

        def show_selected(_event: tk.Event | None = None) -> None:
            selection = tree.selection()
            if not selection:
                return
            item = items.get(selection[0])
            if not item:
                return
            image_path = Path(str(item["image_path"]))
            mask_path = Path(str(item["mask_path"]))
            try:
                with open_image_file(image_path) as source:
                    source.thumbnail((500, 570), Image.Resampling.LANCZOS)
                    rgb = source.convert("RGB").copy()
                with open_image_file(mask_path) as mask:
                    mask_preview = mask.convert("L").resize(
                        rgb.size, Image.Resampling.NEAREST
                    )
                rendered = overlay(rgb, mask_preview)
                tk_image = ImageTk.PhotoImage(rendered)
                preview_images[:] = [tk_image]
                preview_label.configure(image=tk_image, text="")
                detail.configure(
                    text=(
                        f"类别：{label_name(str(item.get('label_type') or 'cloud'))}\n"
                        f"方法：{METHOD_LABELS.get(str(item['source_method']), item['source_method'])}\n"
                        f"日期：{item['acquired_date']}  季节：{item['season']}\n"
                        f"空间网格：{item['geo_cell']}\n"
                        f"备注：{item['note'] or '无'}"
                    )
                )
            except Exception as exc:
                preview_images.clear()
                preview_label.configure(image="", text=f"预览失败：{exc}")

        def remove_selected() -> None:
            selection = tree.selection()
            if not selection:
                messagebox.showinfo("待入库管理", "请先选择要剔除的数据。", parent=dialog)
                return
            if not messagebox.askyesno(
                "确认剔除",
                f"确定从待入库区剔除选中的 {len(selection)} 景吗？\n"
                "剔除后会重新回到待复核队列。",
                parent=dialog,
            ):
                return
            for scene_id in selection:
                removed = self.store.remove_pending_import(scene_id)
                if removed:
                    delete_pending_files(removed)
            reload_items()
            self.refresh_queue()

        def import_scene_ids(scene_ids: list[str]) -> None:
            if not scene_ids:
                messagebox.showinfo("待入库管理", "没有可导入的数据。", parent=dialog)
                return
            if not self.database_ready:
                messagebox.showwarning(
                    "数据库不可用",
                    "请先在主界面完成环境和数据库目录检查。",
                    parent=dialog,
                )
                return
            if not messagebox.askyesno(
                "确认导入数据库",
                f"确定将 {len(scene_ids)} 景候选数据写入正式数据库吗？",
                parent=dialog,
            ):
                return
            imported = 0
            failures: list[str] = []
            for scene_id in scene_ids:
                item = items.get(scene_id)
                scene = self.store.get_scene(scene_id)
                if not item or not scene:
                    failures.append(f"{scene_id}: 找不到候选记录或场景信息")
                    continue
                try:
                    if self.mysql_store is None:
                        raise RuntimeError("MySQL 尚未连接，请重新检查环境")
                    item_label_type = str(item.get("label_type") or "cloud")
                    split, _, final_mask = archive_import_item(
                        scene,
                        str(item["mask_path"]),
                        str(item["source_method"]),
                        self.config_data,
                        self.store,
                        external_recorder=self.mysql_store.add_record,
                        image_source_path=str(item["image_path"]),
                        label_type=item_label_type,
                    )
                    self.store.complete_pending_import(
                        scene,
                        str(item["source_method"]),
                        final_mask,
                        split,
                        str(item["note"]),
                        item_label_type,
                    )
                    delete_pending_files(item)
                    imported += 1
                except Exception as exc:
                    failures.append(f"{scene_id}: {exc}")
            reload_items()
            self.update_summary()
            message = f"成功导入 {imported} 景。"
            if failures:
                message += "\n\n以下数据未导入：\n" + "\n".join(failures[:10])
            messagebox.showinfo("导入完成", message, parent=dialog)

        tree.bind("<<TreeviewSelect>>", show_selected)
        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="剔除选中", command=remove_selected).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(
            actions,
            text="导入选中",
            command=lambda: import_scene_ids(list(tree.selection())),
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(
            actions,
            text="全部导入数据库",
            command=lambda: import_scene_ids(list(items)),
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(actions, text="关闭", command=dialog.destroy).pack(
            side=tk.RIGHT, padx=3
        )
        reload_items()
        show_selected()

    def export_dataset_from_database(self) -> None:
        if self.dataset_export_running:
            return
        if not self.database_ready or self.mysql_store is None:
            messagebox.showwarning(
                "数据库不可用",
                "请先点击“检查环境”，确认真实 MySQL 数据库可连接。",
            )
            return
        output_dir = Path(self.config_data.dataset_output_dir).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            probe = output_dir / ".cloud_review_dataset_write_test"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
        except Exception as exc:
            messagebox.showerror("数据集输出路径不可写", str(exc))
            return
        mysql_store = self.mysql_store
        self.dataset_export_running = True
        self.update_action_states()
        self.status.set(f"正在从真实数据库导出数据集：{output_dir}")

        def worker() -> None:
            try:
                records = mysql_store.dataset_records()
                stats = export_records_to_dataset(
                    records,
                    output_dir,
                    self.config_data.dataset_mask_encoding,
                )
                self.after(0, lambda payload=stats: self.dataset_export_finished(payload))
            except Exception as exc:
                self.after(0, lambda error=exc: self.dataset_export_failed(error))

        threading.Thread(target=worker, daemon=True).start()

    def dataset_export_finished(self, stats: dict[str, object]) -> None:
        self.dataset_export_running = False
        self.update_action_states()
        message = (
            f"数据集导出完成：总记录 {stats['total']}，新增 {stats['copied']}，"
            f"已存在跳过 {stats['existing']}，源文件缺失 {stats['missing']}，"
            f"失败 {stats['failed']}。\n"
            f"目录结构：train|val/rgb|label/数据库ID.tif\n"
            f"掩膜编码：{stats['mask_encoding']}\n\n"
            f"清单：{stats['manifest_path']}"
        )
        failures = stats.get("failures") or []
        if failures:
            message += "\n\n前几条问题：\n" + "\n".join(str(item) for item in failures[:10])
        self.status.set("数据集导出完成")
        messagebox.showinfo("导出数据集", message)

    def dataset_export_failed(self, exc: Exception) -> None:
        self.dataset_export_running = False
        self.update_action_states()
        self.status.set(f"数据集导出失败：{exc}")
        messagebox.showerror("导出数据集失败", str(exc))

    def on_tree_select(self, _event: tk.Event) -> None:
        # The main window is a scanner/status dashboard. Image review is handled
        # by the standalone picker so selection here must stay lightweight.
        return
        selection = self.tree.selection()
        if selection:
            selected_index = int(selection[0])
            if selected_index != self.index:
                self.show_scene(selected_index)

    def current_scene(self) -> Scene | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def show_scene(self, index: int) -> None:
        if not 0 <= index < len(self.queue):
            return
        self.index = index
        scene = self.queue[index]
        self.current_masks = dict(scene.masks)
        self.scene_title.configure(
            text=(
                f"{scene.scene_id}    卫星：{scene.satellite}    日期：{scene.acquired_date}    "
                f"计划号：{scene.plan_code or '未知'}    "
                f"季节：{scene.season}    网格：{scene.geo_cell}    "
                f"中心：{scene.center_lon if scene.center_lon is not None else '未知'}, "
                f"{scene.center_lat if scene.center_lat is not None else '未知'}    "
                f"差异差值：{scene.difference:.2%}    "
                f"差异百分比：{scene.difference_percent:.2%}    "
                f"IoU：{scene.iou:.3f}    "
                f"AI：{scene.anomaly_score:.2f} {scene.anomaly_reason}"
            )
        )
        self.progress.set(f"{index + 1} / {len(self.queue)}")
        self.preview_images.clear()
        try:
            with open_image_file(scene.image_path) as source:
                source.thumbnail((430, 610), Image.Resampling.LANCZOS)
                rgb = source.convert("RGB").copy()
        except Exception as exc:
            message = f"原图加载失败：{scene.image_path}\n{exc}"
            self.scene_title.configure(text=f"{scene.scene_id}    {message}")
            for method in self.image_labels:
                self.image_labels[method].configure(image="", text=message)
                self.metric_labels[method].configure(text="原图加载失败")
            return
        for method in self.image_labels:
            path = self.current_masks.get(method)
            if not path or not path_exists(path):
                detail = f"无结果，可人工编辑\n{path or ''}"
                self.image_labels[method].configure(image="", text=detail)
                self.metric_labels[method].configure(text="无结果")
                continue
            try:
                with open_image_file(path) as mask:
                    small_mask = mask.convert("L").resize(
                        rgb.size, Image.Resampling.NEAREST
                    )
                    preview = overlay(rgb, small_mask)
            except Exception as exc:
                self.image_labels[method].configure(
                    image="", text=f"掩膜加载失败：{path}\n{exc}"
                )
                self.metric_labels[method].configure(text="掩膜加载失败")
                continue
            tk_image = ImageTk.PhotoImage(preview)
            self.preview_images.append(tk_image)
            self.image_labels[method].configure(image=tk_image, text="")
            ratio = scene.cloud_ratios.get(method)
            ratio_text = f"云量：{ratio:.2%}" if ratio is not None else "人工编辑结果"
            if method == "omnicloudmask":
                parts = [ratio_text]
                downsample = scene.cloud_ratios.get("omnicloudmask_downsample")
                if downsample:
                    parts.append(f"下采样：{downsample:g}x")
                for key, label in (("combined", "综合法"), ("rdc", "RDC")):
                    diff = scene.cloud_ratios.get(
                        f"omnicloudmask_vs_{key}_difference"
                    )
                    iou = scene.cloud_ratios.get(f"omnicloudmask_vs_{key}_iou")
                    if diff is not None and iou is not None:
                        parts.append(f"与{label}差异：{diff:.1%} IoU：{iou:.2f}")
                ratio_text = " | ".join(parts)
            self.metric_labels[method].configure(text=ratio_text)
        item_id = str(index)
        if self.tree.selection() != (item_id,):
            self.tree.selection_set(item_id)
        self.tree.see(item_id)

    def clear_scene(self) -> None:
        self.scene_title.configure(text="当前没有符合条件的待复核影像")
        self.progress.set("0 / 0")
        self.current_masks = {}
        for method in self.image_labels:
            self.image_labels[method].configure(image="", text="暂无数据")
            self.metric_labels[method].configure(text="")

    def accept_method(self, method: str) -> None:
        if not self.database_ready:
            messagebox.showwarning(
                "测试模式", "未配置可写成果数据库目录，当前结果不能加入待入库区。"
            )
            return
        scene = self.current_scene()
        mask_path = self.current_masks.get(method)
        if not scene:
            return
        if not mask_path or not path_exists(mask_path):
            self.status.set(
                f"{scene.scene_id} 没有可加入待入库的 {METHOD_LABELS.get(method, method)} 掩膜。"
            )
            return
        try:
            label_type = self.current_label_type()
            copy_to_pending_import(
                scene,
                mask_path,
                method,
                self.note.get(),
                self.config_data,
                self.store,
                label_type,
            )
            self.status.set(
                f"{scene.scene_id} 已按“{label_name(label_type)}”加入待入库区，尚未写入正式数据库"
            )
            self.note.set("")
            self.remove_current()
        except Exception as exc:
            messagebox.showerror("暂存失败", str(exc))

    def edit_method(self, method: str) -> None:
        scene = self.current_scene()
        if not scene:
            return
        mask_path = self.current_masks.get(method)
        if not mask_path or not path_exists(mask_path):
            mask_path = self.create_blank_mask(scene, method)
            self.current_masks[method] = mask_path
            scene.masks[method] = mask_path
        label_type = self.current_label_type()
        output_dir = Path(self.config_data.cache_dir).expanduser().resolve() / "manual_masks" / scene.scene_id
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
            self.status.set(
                f"{scene.scene_id} 的 {METHOD_LABELS.get(method, method)} 已更新为人工编辑结果，尚未加入待入库。"
            )
            self.show_scene(self.index)

    def run_omnicloudmask(self) -> None:
        scene = self.current_scene()
        if not scene:
            return
        if attach_cached_omnicloudmask_result(scene, self.config_data):
            self.omni_finished(scene.masks["omnicloudmask"])
            return
        source_tif = Path(scene.source_tif_path)
        if not path_exists(source_tif):
            messagebox.showerror(
                "无法运行 OmniCloudMask",
                "当前景没有识别到同名 *_L1_MSS.tif 四波段影像。",
            )
            return
        output_root = Path(self.config_data.cache_dir) / "omnicloudmask"
        output_root.mkdir(parents=True, exist_ok=True)
        script = ROOT / "scripts" / "run_omnicloudmask_jilin1.py"
        self.status.set(f"正在检查 OmniCloudMask 运行环境：{scene.scene_id}")

        def worker() -> None:
            try:
                dependency_error = ""
                omni_python = self.omni_python_cache
                if not omni_python:
                    omni_python, dependency_error = find_omnicloudmask_python(
                        self.config_data.omnicloudmask_python
                    )
                if not omni_python:
                    raise RuntimeError(
                        "没有找到具备完整 OmniCloudMask 依赖的 Python 3。\n"
                        "至少需要 rasterio、torch、torchvision、timm、"
                        "segmentation_models_pytorch 和 safetensors。\n\n"
                        f"检查详情：\n{dependency_error}"
                    )
                self.omni_python_cache = omni_python
                model_dir, missing_models = find_omnicloudmask_model_dir(
                    self.config_data.omnicloudmask_model_dir
                )
                if not model_dir:
                    missing_text = "\n".join(f"- {name}" for name in missing_models)
                    raise RuntimeError(
                        "OmniCloudMask 运行环境已经找到，但本地模型权重不完整。\n"
                        "内网运行不会自动下载模型，请把以下文件放入设置中的"
                        "“Omni 模型目录”：\n\n"
                        f"{missing_text}"
                    )
                self.after(
                    0,
                    lambda path=omni_python: self.status.set(
                        "正在运行 OmniCloudMask：使用四波段 MSS.tif 的 "
                        f"Red/Green/NIR，宽高下采样 "
                        f"{max(1, int(self.config_data.omnicloudmask_downsample))} 倍；"
                        f"{scene.scene_id}"
                    ),
                )
                command = [
                    omni_python,
                    str(script),
                    "--input-file",
                    filesystem_path(source_tif),
                    "--out",
                    str(output_root),
                    "--model-dir",
                    model_dir,
                    "--device",
                    self.config_data.omnicloudmask_device,
                    "--downsample",
                    str(max(1, int(self.config_data.omnicloudmask_downsample))),
                ]
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=7200,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode != 0:
                    details = completed.stderr.strip() or completed.stdout.strip()
                    raise RuntimeError(details[-3000:])
                result = (
                    output_root
                    / source_tif.stem
                    / f"{source_tif.stem}_omnicloudmask_cloud_binary.tif"
                )
                if not path_exists(result):
                    raise RuntimeError(f"算法结束但未找到结果：{result}")
                self.after(0, lambda path=str(result): self.omni_finished(path))
            except Exception as exc:
                self.after(0, lambda error=exc: self.omni_failed(error))

        threading.Thread(target=worker, daemon=True).start()

    def omni_finished(self, mask_path: str) -> None:
        scene = self.current_scene()
        if not scene:
            return
        scene.masks["omnicloudmask"] = mask_path
        self.current_masks["omnicloudmask"] = mask_path
        from .core import cloud_ratio

        compare_size = max(1, int(self.config_data.mask_compare_size or 512))
        scene.cloud_ratios["omnicloudmask"] = cloud_ratio(
            mask_path, size=(compare_size, compare_size)
        )
        comparison_messages: list[str] = []
        for method, label in (("combined", "综合法"), ("rdc", "RDC")):
            reference = scene.masks.get(method)
            if not reference or not path_exists(reference):
                continue
            _omni_ratio, ref_ratio, diff, iou = compare_masks(
                mask_path,
                reference,
                target_size=(
                    compare_size,
                    compare_size,
                ),
            )
            scene.cloud_ratios[f"omnicloudmask_vs_{method}_difference"] = diff
            scene.cloud_ratios[f"omnicloudmask_vs_{method}_iou"] = iou
            scene.cloud_ratios[f"{method}_ratio_for_omni_compare"] = ref_ratio
            comparison_messages.append(f"与{label}差异{diff:.1%}/IoU{iou:.2f}")
        scene.cloud_ratios["omnicloudmask_downsample"] = float(
            max(1, int(self.config_data.omnicloudmask_downsample))
        )
        cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
        if cache_path:
            cache_path = cache_review_scene(
                scene,
                self.config_data,
                root_override=cache_path,
            )
            scene.cloud_ratios["review_cache_path"] = cache_path
        self.store.upsert_scenes([scene])
        detail = "；".join(comparison_messages) if comparison_messages else "未找到可比较掩膜"
        self.status.set(f"OmniCloudMask 完成：{scene.scene_id}；{detail}")
        self.show_scene(self.index)

    def omni_failed(self, exc: Exception) -> None:
        self.status.set("OmniCloudMask 运行失败")
        messagebox.showerror(
            "OmniCloudMask 运行失败",
            f"{exc}\n\n请检查模型权重、离线依赖和四波段 TIF。",
        )

    def accept_custom_mask(self, method: str, mask_path: str) -> None:
        scene = self.current_scene()
        if not scene:
            return
        self.current_masks[method] = mask_path
        scene.masks[method] = mask_path
        self.status.set(
            f"{scene.scene_id} 的 {METHOD_LABELS.get(method, method)} 已替换为人工编辑结果，尚未加入待入库。"
        )
        self.show_scene(self.index)

    def create_constant_mask(self, all_cloud: bool) -> None:
        scene = self.current_scene()
        if not scene:
            return
        label_type = self.current_label_type()
        with open_image_file(scene.image_path) as image:
            mask = Image.new("L", image.size, label_value(label_type) if all_cloud else 0)
        cache = Path(self.config_data.cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        method = f"manual_all_{label_type}" if all_cloud else "manual_empty"
        target = cache / f"{scene.scene_id}_{method}.tif"
        mask.save(target, compression="tiff_lzw")
        self.current_masks["combined"] = str(target)
        scene.masks["combined"] = str(target)
        scene.cloud_ratios["combined"] = 1.0 if all_cloud else 0.0
        self.store.upsert_scenes([scene])
        self.status.set(
            f"{scene.scene_id} 已显示为{'全云' if all_cloud else '无云'}结果，准备加入待入库。"
        )
        self.show_scene(self.index)
        self.update_idletasks()
        if not self.database_ready:
            messagebox.showwarning(
                "测试模式", "未配置可写成果数据库，当前结果已显示，但不能加入待入库区。"
            )
            return
        self.accept_method("combined")

    def skip_current(self) -> None:
        scene = self.current_scene()
        if not scene:
            return
        self.store.mark_skipped(scene.scene_id)
        self.status.set(f"已跳过：{scene.scene_id}")
        self.remove_current()

    def skip_day(self) -> None:
        scene = self.current_scene()
        if not scene:
            return
        day = scene.acquired_date
        scene_ids = [item.scene_id for item in self.queue if item.acquired_date == day]
        for scene_id in scene_ids:
            self.store.mark_skipped(scene_id)
        self.queue = [item for item in self.queue if item.acquired_date != day]
        self.index = min(self.index, len(self.queue) - 1)
        self.rebuild_tree()
        self.status.set(f"已跳过 {day} 的 {len(scene_ids)} 景")

    def add_typical(self) -> None:
        scene = self.current_scene()
        if not scene:
            return
        available = next(
            (path for path in self.current_masks.values() if path_exists(path)),
            None,
        )
        if not available:
            return
        target = copy_to_typical(scene, available, self.config_data)
        self.status.set(f"已加入典型数据集：{target}")

    def remove_current(self) -> None:
        if self.queue:
            self.queue.pop(self.index)
        self.index = min(self.index, len(self.queue) - 1)
        self.rebuild_tree()
        self.update_summary()

    def rebuild_tree(self, hidden_missing: int = 0) -> None:
        if hasattr(self, "queue_frame"):
            self.queue_frame.configure(text=f"待复核队列（显示 {len(self.queue)} 景）")
        self.tree.delete(*self.tree.get_children())
        for index, scene in enumerate(self.queue):
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                text=scene.scene_id,
                values=(
                    scene.acquired_date,
                    f"{scene.difference:.1%}",
                    f"{scene.difference_percent:.1%}",
                    f"{scene.anomaly_score:.2f}",
                    f"{scene.iou:.2f}",
                ),
            )
        if self.queue:
            self.show_scene(max(0, self.index))
        else:
            self.clear_scene()

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("监控与存储设置")
        dialog.geometry("900x980")
        dialog.transient(self)
        dialog.grab_set()
        fields = {
            "盘阵根路径（分号分隔）": tk.StringVar(value=";".join(self.config_data.watch_roots)),
            "卫星前缀（逗号分隔）": tk.StringVar(value=",".join(self.config_data.satellite_filters)),
            "入库实体路径": tk.StringVar(value=self.config_data.database_dir),
            "数据集输出路径": tk.StringVar(value=self.config_data.dataset_output_dir),
            "数据集标签编码（binary255/class_values/preserve）": tk.StringVar(
                value=self.config_data.dataset_mask_encoding
            ),
            "缓存目录（建议放到非C盘）": tk.StringVar(value=self.config_data.cache_dir),
            "扫描日志文件（留空=缓存目录\\scan.log）": tk.StringVar(
                value=self.config_data.scan_log_path
            ),
            "MySQL 主机": tk.StringVar(value=self.config_data.mysql_host),
            "MySQL 端口": tk.StringVar(value=str(self.config_data.mysql_port)),
            "MySQL 用户名": tk.StringVar(value=self.config_data.mysql_user),
            "MySQL 密码": tk.StringVar(value=self.config_data.mysql_password),
            "MySQL 数据库名": tk.StringVar(value=self.config_data.mysql_database),
            "MySQL 表名": tk.StringVar(value=self.config_data.mysql_table),
            "典型数据集文件夹": tk.StringVar(value=self.config_data.typical_dir),
            "差异阈值（0-1）": tk.StringVar(value=str(self.config_data.minimum_difference)),
            "启用差异差值（0/1）": tk.StringVar(
                value="1" if self.config_data.enable_difference_delta else "0"
            ),
            "差异百分比阈值（0-1）": tk.StringVar(
                value=str(self.config_data.minimum_difference_percent)
            ),
            "百分比规则最小差异差值（0-1）": tk.StringVar(
                value=str(self.config_data.minimum_difference_for_percent)
            ),
            "复核/入库缓存尺寸（像素）": tk.StringVar(
                value=str(self.config_data.review_cache_size)
            ),
            "掩膜比较尺寸（像素）": tk.StringVar(
                value=str(self.config_data.mask_compare_size)
            ),
            "启用差异百分比（0/1）": tk.StringVar(
                value="1" if self.config_data.enable_difference_percent else "0"
            ),
            "Omni 模型目录": tk.StringVar(value=self.config_data.omnicloudmask_model_dir),
            "Omni 设备（cpu/cuda）": tk.StringVar(value=self.config_data.omnicloudmask_device),
            "Omni 推理 Python（留空自动）": tk.StringVar(
                value=self.config_data.omnicloudmask_python
            ),
            "Omni 下采样倍数（1=原始，8=快速）": tk.StringVar(
                value=str(self.config_data.omnicloudmask_downsample)
            ),
            "扫描回看天数（0=全部）": tk.StringVar(
                value=str(self.config_data.scan_lookback_days)
            ),
            "扫描线程数": tk.StringVar(value=str(self.config_data.scan_workers)),
            "地理网格大小（度）": tk.StringVar(
                value=str(self.config_data.geo_grid_degrees)
            ),
            "启用离线云雾辅助判别（0/1）": tk.StringVar(
                value="1" if self.config_data.enable_anomaly_detector else "0"
            ),
        }
        shell = ttk.Frame(dialog)
        shell.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(shell, highlightthickness=0)
        scrollbar = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        frame = ttk.Frame(canvas, padding=12)
        window_id = canvas.create_window((0, 0), window=frame, anchor=tk.NW)

        def resize_settings(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_settings_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        def on_settings_wheel(event: tk.Event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        frame.bind("<Configure>", resize_settings)
        canvas.bind("<Configure>", fit_settings_width)
        dialog.bind("<MouseWheel>", on_settings_wheel)
        for row, (label, variable) in enumerate(fields.items()):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=6)
            if "（0/1）" in label or "(0/1)" in label:
                ttk.Checkbutton(frame, variable=variable, onvalue="1", offvalue="0").grid(
                    row=row, column=1, sticky=tk.W, pady=6
                )
            elif "数据集标签编码" in label:
                ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=("binary255", "class_values", "preserve"),
                    state="readonly",
                    width=24,
                ).grid(row=row, column=1, sticky=tk.W, pady=6)
            elif "Omni 设备" in label:
                ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=("cpu", "cuda"),
                    state="readonly",
                    width=12,
                ).grid(row=row, column=1, sticky=tk.W, pady=6)
            else:
                ttk.Entry(frame, textvariable=variable, width=72).grid(
                    row=row, column=1, sticky=tk.EW, pady=6
                )
        frame.columnconfigure(1, weight=1)

        def save() -> None:
            try:
                values = list(fields.values())
                self.config_data.watch_roots = [item.strip() for item in values[0].get().split(";") if item.strip()]
                self.config_data.satellite_filters = [item.strip() for item in values[1].get().split(",") if item.strip()]
                self.config_data.database_dir = values[2].get().strip()
                self.config_data.dataset_output_dir = values[3].get().strip()
                self.config_data.dataset_mask_encoding = values[4].get().strip()
                self.config_data.cache_dir = values[5].get().strip()
                self.config_data.scan_log_path = values[6].get().strip()
                self.config_data.mysql_host = values[7].get().strip()
                self.config_data.mysql_port = int(values[8].get())
                self.config_data.mysql_user = values[9].get().strip()
                self.config_data.mysql_password = values[10].get()
                self.config_data.mysql_database = values[11].get().strip()
                self.config_data.mysql_table = values[12].get().strip()
                self.config_data.typical_dir = values[13].get().strip()
                self.config_data.minimum_difference = float(values[14].get())
                self.config_data.enable_difference_delta = values[15].get().strip() not in {
                    "",
                    "0",
                    "false",
                    "False",
                    "FALSE",
                }
                self.config_data.minimum_difference_percent = float(values[16].get())
                self.config_data.minimum_difference_for_percent = float(values[17].get())
                self.config_data.review_cache_size = max(1, int(values[18].get()))
                self.config_data.mask_compare_size = max(1, int(values[19].get()))
                self.config_data.enable_difference_percent = values[20].get().strip() not in {
                    "",
                    "0",
                    "false",
                    "False",
                    "FALSE",
                }
                self.config_data.omnicloudmask_model_dir = values[21].get().strip()
                self.config_data.omnicloudmask_device = values[22].get().strip() or "cpu"
                self.config_data.omnicloudmask_python = values[23].get().strip()
                self.config_data.omnicloudmask_downsample = max(1, int(values[24].get()))
                self.config_data.scan_lookback_days = max(0, int(values[25].get()))
                self.config_data.scan_workers = max(1, min(32, int(values[26].get())))
                self.config_data.geo_grid_degrees = max(
                    0.1, float(values[27].get())
                )
                self.config_data.enable_anomaly_detector = values[28].get().strip() not in {
                    "",
                    "0",
                    "false",
                    "False",
                    "FALSE",
                }
                self._resolve_config_paths()
                self.scan_result_path = Path(self.config_data.cache_dir) / "scan_result.json"
                self.scan_progress_path = Path(self.config_data.cache_dir) / "scan_progress.json"
                self.scan_config_path = Path(self.config_data.cache_dir) / "scan_config.json"
                self.scan_log_path = self.resolve_scan_log_path()
                self.omni_python_cache = None
                self.mysql_store = None
                self.config_data.save(CONFIG_PATH)
                self.environment_ready = False
                self.database_ready = False
                self.environment_status.set("配置已保存，环境尚未检查")
                self.status.set("请点击“检查环境”，确认后再开始扫描。")
                self.update_action_states()
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror("设置无效", str(exc), parent=dialog)

        ttk.Button(frame, text="保存配置", command=save).grid(row=len(fields), column=1, sticky=tk.E, pady=15)


def main() -> None:
    app = CloudReviewApp()
    app.mainloop()


if __name__ == "__main__":
    main()
