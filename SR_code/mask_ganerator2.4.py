import sys
import os
import gc
import re
import time
import json
from osgeo import gdal
import numpy as np
import cv2
import datetime
import logging
from logging.handlers import TimedRotatingFileHandler
from PyQt5.QtWidgets import (QApplication, QMainWindow, QFileDialog, QPushButton,
                             QVBoxLayout, QHBoxLayout, QWidget, QLabel, QStatusBar,
                             QMessageBox, QGraphicsLineItem, QSizePolicy, QTabWidget, QProgressDialog,
                             QToolBar, QAction, QDialog, QGraphicsEllipseItem, QGraphicsTextItem,
                             QSpinBox, QLineEdit, QWidgetAction, QGraphicsView, QGraphicsScene)
from PyQt5.QtGui import (QImage, QPixmap, QPen, QColor, QBrush, QIcon, QPolygonF, QPainter, QFont)
from PyQt5.QtCore import Qt, QObject, QRunnable, pyqtSlot, QThreadPool, pyqtSignal, QTimer, QThread, QPointF, QRectF, QSizeF


class Worker(QRunnable):
    """线程池工作线程，用于执行耗时任务"""

    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            result = self.function(*self.args, **self.kwargs)
            self.signals.result.emit(result)
        except Exception as e:
            self.signals.error.emit((type(e), str(e), None))
        finally:
            self.signals.finished.emit()


class WorkerSignals(QObject):
    """定义工作线程的信号"""
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)
    progress = pyqtSignal(int)


class ImageLoadThread(QThread):
    """图像加载线程，支持超大图像（>2GB）分块读取，避免内存溢出"""
    loadFinished = pyqtSignal(bool)
    thread_count_updated = pyqtSignal(int)

    def __init__(self, image_path=None, config_manager=None, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger("MaskGenerator.ImageLoadThread")
        self.image_path = image_path
        self.config_manager = config_manager
        self.image = None           # 显示用缩略图
        self.full_w = 0             # 原始宽度
        self.full_h = 0             # 原始高度
        self.scale = 1.0            # 缩放比例
        self.is_16bit = False
        self.thread_count = 1

        if self.config_manager:
            self.MAX_DISPLAY_SIZE = self.config_manager.get("image_processing.max_display_size", 20032)
        else:
            self.MAX_DISPLAY_SIZE = 20032

    def run(self):
        try:
            from osgeo import gdal
            # 子线程自己的 GDAL 配置（完全独立）
            gdal.UseExceptions()
            gdal.SetConfigOption("GDAL_MAX_DATASET_SIZE", "10737418240")
            gdal.SetConfigOption("CHECK_DISK_FREE_SPACE", "FALSE")
            gdal.SetConfigOption("GDAL_CACHE_MAX", "128")
            gdal.SetConfigOption("GDAL_SINGLE_THREAD", "YES")

            dataset = gdal.Open(self.image_path, gdal.GA_ReadOnly)
            if dataset is None:
                self.logger.error(f"图像加载失败: GDAL无法打开 - {self.image_path}")
                self.loadFinished.emit(False)
                return

            self.full_w = dataset.RasterXSize
            self.full_h = dataset.RasterYSize
            dtype = dataset.GetRasterBand(1).DataType
            self.is_16bit = (dtype == gdal.GDT_UInt16)

            # 计算显示缩放
            if max(self.full_w, self.full_h) > self.MAX_DISPLAY_SIZE:
                self.scale = self.MAX_DISPLAY_SIZE / max(self.full_w, self.full_h)
            new_w = int(self.full_w * self.scale)
            new_h = int(self.full_h * self.scale)

            # 加载并显示缩略图
            self.image = dataset.ReadAsArray(buf_xsize=new_w, buf_ysize=new_h, resample_alg=gdal.GRIORA_Bilinear)

            dataset = None
            del dataset
            gc.collect()

            # 通道处理
            if len(self.image.shape) == 3:
                self.image = np.moveaxis(self.image, 0, -1)
                self.image = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)

            # 动态线程数
            img_size = new_w * new_h
            if img_size < 1100 * 1100:
                self.thread_count = max(1, os.cpu_count() // 8)
            elif img_size < 5500 * 5500:
                self.thread_count = max(1, os.cpu_count() // 4)
            else:
                self.thread_count = max(1, os.cpu_count() // 2)
            cv2.setNumThreads(self.thread_count)
            self.thread_count_updated.emit(self.thread_count)

            self.loadFinished.emit(True)

        except Exception as e:
            self.logger.error(f"图像加载线程异常: {str(e)}", exc_info=True)
            self.loadFinished.emit(False)


class ImageViewer(QGraphicsView):
    """基于QGraphicsView的增强型图像查看器，支持缩放、平移和绘制"""

    zoomChanged = pyqtSignal(float)
    imageDropped = pyqtSignal(str)
    posChanged = pyqtSignal(QPointF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger("MaskGenerator.ImageViewer")
        # 初始化配置管理器
        self.app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.config_manager = ConfigManager(self.app_dir)
        # 初始化场景
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.main_window = None

        # 设置渲染选项
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        # 交互设置
        self.setDragMode(QGraphicsView.ScrollHandDrag)  # 默认为滚动模式
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setAcceptDrops(True)   # 启用拖放功能
        self._left_button_pressed = False

        # 图像和图层
        self.image = None  # 原始图像数据
        self.image_item = None  # 图像显示项
        self._drag_start = None
        self.current_vertices = []  # 当前多边形的顶点
        self.polygon_items = []  # 多边形绘制项
        self.current_polygon = None  # 当前正在绘制的多边形
        
        # 框选功能相关变量
        self.box_state = 0      # 0: 未选择点, 1: 已选择第一个点
        self.box_start = None  # 框选起始点
        self.box_item = None  # 框选矩形项
        self.temp_box_item = None
        self.selected_polygons = []  # 选中的多边形
        self.highlighted_polygons = []    # 高亮显示的多边形

        # 状态控制
        self.box_select_mode = False  # 框选模式开关
        self.drawing_mode = False  # 绘图模式开关
        self.scale_factor = 1.0  # 缩放因子
        self.scene_pos = QPointF(0, 0)
        self.is_16bit = False  # 图像位深度标记
        self.is_equalized = False  # 图像是否拉伸

        # 样式设置
        self.polygon_pen = QPen(QColor(255, 0, 0), 2, Qt.SolidLine)
        self.polygon_brush = QBrush(QColor(255, 0, 0, 50))  # 半透明红色填充
        self.vertex_pen = QPen(QColor(255, 0, 0), 1, Qt.SolidLine)
        self.vertex_brush = QBrush(QColor(255, 0, 0))
        self.highlight_pen = QPen(QColor(255, 255, 0), 3, Qt.SolidLine)
        self.highlight_brush = QBrush(QColor(255, 255, 0, 70))  # 半透明黄色

        # 状态栏标签
        self.status_label = None

    def setStatusLabel(self, label):
        """设置状态栏标签，用于显示信息"""
        self.status_label = label

    def dragEnterEvent(self, event):
        """处理拖放进入事件，正确显示拖放图标"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                # 检查是否为支持的图像格式
                if self.is_supported_image(file_path):
                    event.acceptProposedAction()
                    return
                else:
                    self.logger.warning(f"拖放图像为无效格式")
        event.ignore()
        self.setCursor(Qt.ForbiddenCursor)

    def dragLeaveEvent(self, event):
        """处理拖放离开事件，恢复默认状态"""
        self.unsetCursor()
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event):
        """处理拖放移动事件"""
        pass

    def dropEvent(self, event):
        """处理拖放释放事件"""
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if self.is_supported_image(file_path):
                    # 发送信号通知主窗口加载图像
                    self.imageDropped.emit(file_path)
                event.accept()
        else:
            super().dropEvent(event)

    def is_supported_image(self, path):
        """检查是否为支持的图像格式"""
        supported_extensions = ['.tif', '.tiff', '.png', '.jpg', '.jpeg']
        ext = os.path.splitext(path.lower())[1]
        return ext in supported_extensions and os.path.exists(path)

    def wheelEvent(self, event):
        """处理滚轮事件（缩放）"""
        if event.modifiers() & Qt.ControlModifier:
            # 计算缩放因子
            # 从配置中读取缩放参数
            zoom_in_factor = self.config_manager.get("ui.zoom_in_factor", 1.1)
            zoom_out_factor = self.config_manager.get("ui.zoom_out_factor", 0.9)
            max_scale = self.config_manager.get("ui.zoom_max_scale", 2.0)
            min_scale = self.config_manager.get("ui.zoom_min_scale", 0.01)

            if event.angleDelta().y() > 0:
                # 放大
                new_scale = self.scale_factor * zoom_in_factor if (self.scale_factor * zoom_in_factor) <= max_scale else max_scale
                if new_scale > max_scale:  # 限制最大缩放
                    return
            else:
                # 缩小
                new_scale = self.scale_factor * zoom_out_factor if (self.scale_factor * zoom_out_factor) > min_scale else min_scale
                if new_scale < min_scale:  # 限制最小缩放
                    return

            # 应用新的缩放因子
            self.scale_factor = new_scale

            self.scale(self.scale_factor / self.transform().m11(), self.scale_factor / self.transform().m11())

            # 发出缩放变化信号
            self.zoomChanged.emit(self.scale_factor)

            # 更新状态栏
            if self.status_label:
                self.status_label.showMessage(f"缩放: {int(self.scale_factor * 100)}%")

    def mousePressEvent(self, event):
        """处理鼠标按下事件
            绘制模式下：
            1.左键点击画点 2.中键按住拖动  3.右键点击封闭多边形
            框选模式下：
            1. 左键点击两次生成矩形框
            非绘制模式下：
            1. 左键按住拖动
        """

        if self.drawing_mode:
            self.setCursor(Qt.CrossCursor)

        if not self.drawing_mode and self.box_select_mode:
            # 右键点击：取消框选 + 恢复之前状态
            if event.button() == Qt.RightButton:
                self.clear_box_selection()
                self.clear_highlight()
                return

        if not self.drawing_mode and self.box_select_mode and event.button() == Qt.LeftButton:
            # 第一次点击：记录起始点
            if self.box_state == 0:
                self.box_start = self.mapToScene(event.pos())
                self.box_state = 1
                # 创建临时框选矩形
                self.temp_box_item = self.scene.addRect(
                    QRectF(self.box_start, QSizeF(0, 0)),
                    QPen(QColor(0, 255, 0), 2, Qt.DashLine),
                    QBrush(QColor(0, 255, 0, 30))
                )
                self.temp_box_item.setZValue(5)  # 置于顶层
            # 第二次点击：记录结束点并完成框选
            elif self.box_state == 1:
                self.box_state = 0
                end_pos = self.mapToScene(event.pos())

                # 创建最终框选矩形并检测选中的多边形
                rect = QRectF(self.box_start, end_pos).normalized()
                if self.temp_box_item:
                    self.scene.removeItem(self.temp_box_item)
                    self.temp_box_item = None

                self.box_item = self.scene.addRect(
                    rect,
                    QPen(QColor(0, 255, 0), 2, Qt.DashLine),
                    QBrush(QColor(0, 255, 0, 30))
                )
                self.box_item.setZValue(5)  # 置于顶层

                # 检测并删除选中的多边形
                self.detect_selected_polygons(rect)
                # 框选后判断是否有选中，无则恢复状态
                if not self.selected_polygons:
                    self.clear_highlight()
                else:
                    self.highlight_selected_polygons()

        if self.drawing_mode and event.button() == Qt.LeftButton:
            # 获取场景坐标
            scene_pos = self.mapToScene(event.pos())

            # 仅当点击位置在图像范围内时添加顶点
            if self.image_item and self.image_item.boundingRect().contains(scene_pos):
                # 添加顶点
                self.current_vertices.append(scene_pos)
                # 更新当前多边形
                self.update_current_polygon()
                # 记录拖动开始位置（用于平移）
                self._drag_start = event.pos()

        # 右键点击结束当前多边形
        elif self.drawing_mode and event.button() == Qt.RightButton and self.current_vertices:
            if len(self.current_vertices) >= 3:  # 至少3个顶点才能形成多边形
                self.finalize_current_polygon()
            self.current_vertices = []
            self.update_current_polygon()

        # 绘制模式下中键平移
        if self.drawing_mode and event.button() == Qt.MiddleButton:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._drag_start = event.pos()

        # 非绘制模式、非框选删除模式下左键拖动平移
        if not self.drawing_mode and not self.box_select_mode and event.button() == Qt.LeftButton:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._drag_start = event.pos()  # 记录拖动起点
            self._left_button_pressed = True

        # 框选删除模式下中键平移
        if self.box_select_mode and event.button() == Qt.MiddleButton:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._drag_start = event.pos()

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """处理鼠标移动事件"""
        # 显示当前鼠标位置
        if self.image_item:
            self.scene_pos = self.mapToScene(event.pos())
            self.posChanged.emit(self.scene_pos)

        if self.box_select_mode and self.box_state == 1 and self.box_start:
            # 完成框选
            current_pos = self.mapToScene(event.pos())
            if not self.temp_box_item:
                self.temp_box_item = self.scene.addRect(
                    QRectF(self.box_start, QSizeF(0, 0)),
                    QPen(QColor(0, 255, 0), 2, Qt.DashLine),
                    QBrush(QColor(0, 255, 0, 30))
                )
                self.temp_box_item.setZValue(5)
            rect = QRectF(self.box_start, current_pos).normalized()
            self.temp_box_item.setRect(rect)
            self.detect_selected_polygons(rect)
            self.highlight_selected_polygons()

        # 绘制模式下，非拖动情况下，Cursor为十字
        if self.drawing_mode and not self.dragMode() == QGraphicsView.ScrollHandDrag:
            self.setCursor(Qt.CrossCursor)

        if self.box_select_mode and not self.dragMode() == QGraphicsView.ScrollHandDrag:
            self.setCursor(Qt.CrossCursor)

        # 绘制模式下更新当前多边形
        if self.drawing_mode and self.current_vertices:
            # 获取场景坐标
            scene_pos = self.mapToScene(event.pos())

            # 如果点击位置在图像范围内，处理顶点
            if self.image_item and self.image_item.boundingRect().contains(scene_pos):
                # 更新当前多边形的最后一个点（预览效果）
                if len(self.current_vertices) > 0:
                    # 确保至少有一个顶点
                    if len(self.current_vertices) == 1:
                        # 如果只有一个顶点，添加一个临时顶点用于预览
                        self.current_vertices.append(scene_pos)
                    else:
                        # 更新最后一个顶点位置
                        self.current_vertices[-1] = scene_pos
                    self.update_current_polygon()

        # 中键拖动平移
        if self.drawing_mode and self.dragMode() == QGraphicsView.ScrollHandDrag and event.buttons() & Qt.MiddleButton:
            # 绘制模式下中键拖动平移setCursor
            self.setCursor(Qt.ClosedHandCursor)
            delta = event.pos() - self._drag_start
            self._drag_start = event.pos()
            # 调整滚动条位置
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

        # 中键拖动平移
        if self.box_select_mode and self.dragMode() == QGraphicsView.ScrollHandDrag and event.buttons() & Qt.MiddleButton:
            # 绘制模式下中键拖动平移setCursor
            self.setCursor(Qt.ClosedHandCursor)
            delta = event.pos() - self._drag_start
            self._drag_start = event.pos()
            # 调整滚动条位置
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

        # 非绘制模式、框选删除模式下，左键拖动平移
        if not self.drawing_mode and not self.box_select_mode and self._left_button_pressed:
            self.setCursor(Qt.ClosedHandCursor)
            delta = event.pos() - self._drag_start
            self._drag_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """处理鼠标释放事件"""
        if self.box_select_mode and self.box_item:
            self.delete_selected_polygons()
            self.clear_box_selection()

        if self.drawing_mode and event.button() == Qt.MiddleButton:
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)

        # 绘制模式下，左键释放
        elif self.drawing_mode and event.button() == Qt.LeftButton:
            self._drag_start = None

        # 非绘制模式下，左键释放
        elif not self.drawing_mode and event.button() == Qt.LeftButton:
            self._left_button_pressed = False
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.OpenHandCursor)

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        """处理键盘事件"""
        # if self.box_select_mode and event.key() == Qt.Key_Return:
        #     self.delete_selected_polygons()
        #     self.clear_box_selection()
        if self.drawing_mode and event.key() == Qt.Key_Delete and self.current_vertices:
            # 删除最后一个顶点
            if len(self.current_vertices) > 0:
                self.current_vertices.pop()
                self.update_current_polygon()
        if self.drawing_mode and event.key() == Qt.Key_Escape:
            # 取消当前绘制
            self.current_vertices = []
            self.update_current_polygon()

        super().keyPressEvent(event)

    def leaveEvent(self, event):
        """处理鼠标离开视图事件"""
        if self.status_label and self.main_window and self.main_window.is_loading:
            self.status_label.showMessage("正在加载图像...")
        else:
            self.status_label.showMessage("")
        super().leaveEvent(event)

    def load_image(self, image):
        """加载图像"""
        # 保存原始图像数据
        self.image = image.copy()  # 确保创建副本
        self.is_16bit = (image.dtype == np.uint16)

        # 清除现有图像和所有场景项
        self.clear_scene()

        # 转换为8位用于显示
        display_image = self.prepare_display_image(image)

        # 转换为QImage并确保数据深拷贝
        qimage = self.create_qimage_from_numpy(display_image)

        # 转换为QPixmap
        pixmap = QPixmap.fromImage(qimage)

        # 添加新图像
        self.image_item = self.scene.addPixmap(pixmap)
        self.image_item.setZValue(0)  # 底层

        # 设置场景边界略大于图像项的边界矩形
        self.fit_image_rect()

        # 重置视图
        self.fit_to_window()

        # 确保场景更新
        self.scene.update()

    def fit_image_rect(self):
        """设置场景边界略大于图像项的边界矩形"""

        image_rect = self.image_item.boundingRect()

        image_rect_factor = self.config_manager.get("ui.image_rect_factor", 0.1)
        # 计算额外的边距（使用图像宽度和高度的10%作为边距）
        margin_x = image_rect.width() * image_rect_factor
        margin_y = image_rect.height() * image_rect_factor

        # 创建一个略大的矩形作为场景边界
        scene_rect = QRectF(
            image_rect.left() - margin_x / 2,
            image_rect.top() - margin_y / 2,
            image_rect.width() + margin_x,
            image_rect.height() + margin_y
        )

        self.scene.setSceneRect(scene_rect)

    def clear_scene(self):
        """清除场景中的所有项"""
        # 移除所有场景项
        for item in list(self.scene.items()):
            self.scene.removeItem(item)

        # 重置图像项引用
        self.image_item = None

        # 重置当前多边形数据
        self.current_vertices = []
        if hasattr(self, 'current_polygon'):
            self.current_polygon = None

        self.polygon_items.clear()

        gc.collect()

    def prepare_display_image(self, image):
        """准备用于显示的图像"""
        # 复制图像数据
        display_image = image.copy()

        # 转换为8位用于显示
        if self.is_16bit:
            # 归一化16位图像到8位
            min_val, max_val = np.min(display_image), np.max(display_image)
            if max_val > min_val:
                display_image = ((display_image - min_val) / (max_val - min_val) * 255).astype(np.uint8)
            else:
                display_image = np.zeros_like(display_image, dtype=np.uint8)

        # 确保图像数据是连续的
        display_image = np.ascontiguousarray(display_image)

        return display_image

    def create_qimage_from_numpy(self, display_image):
        """从numpy数组创建QImage，确保数据深拷贝"""
        if len(display_image.shape) == 2:  # 灰度图
            h, w = display_image.shape
            # 创建QImage并确保深拷贝
            qimage = QImage(display_image.data, w, h, w, QImage.Format_Grayscale8)
            qimage = qimage.copy()  # 确保QImage拥有数据的所有权
        else:  # 彩色图
            h, w, ch = display_image.shape
            # 转换为RGB格式（如果需要）
            if ch == 3:  # BGR转RGB
                display_image = cv2.cvtColor(display_image, cv2.COLOR_BGR2RGB)
            elif ch == 4:  # BGRA转RGBA
                display_image = cv2.cvtColor(display_image, cv2.COLOR_BGRA2RGBA)

            # 创建QImage并确保深拷贝
            qimage = QImage(display_image.data, w, h, w * ch, QImage.Format_RGB888)
            qimage = qimage.copy()  # 确保QImage拥有数据的所有权

        return qimage

    def toggle_drawing_mode(self, enable):
        """切换绘图模式"""
        self.drawing_mode = enable
        self.setCursor(Qt.CrossCursor if enable else Qt.ArrowCursor)

        # 如果关闭绘图模式，清除当前多边形
        if not enable:
            self.current_vertices = []
            self.update_current_polygon()
            self.setDragMode(QGraphicsView.NoDrag)

    def toggle_box_select_mode(self, enable):
        """切换框选模式"""
        self.box_select_mode = enable
        self.setCursor(Qt.CrossCursor if enable else Qt.ArrowCursor)

        # 清除现有框选
        if not enable:
            self.clear_box_selection()

    def update_current_polygon(self):
        """更新当前正在绘制的多边形"""
        if self.current_polygon:
            self.scene.removeItem(self.current_polygon)
            self.current_polygon = None

        # 创建新的当前多边形（如果有足够的顶点）
        if len(self.current_vertices) >= 2:
            polygon = QPolygonF(self.current_vertices)
            self.current_polygon = self.scene.addPolygon(polygon, self.polygon_pen, self.polygon_brush)
            self.current_polygon.setZValue(2)  # 顶层

    def finalize_current_polygon(self):
        """完成当前多边形的绘制并保存"""
        if len(self.current_vertices) >= 3:
            # 自动闭合多边形（如果需要）
            if not self.current_vertices[0].toPoint() == self.current_vertices[-1].toPoint():
                self.current_vertices.append(self.current_vertices[0])

            # 创建多边形项
            polygon = QPolygonF(self.current_vertices)
            polygon_item = self.scene.addPolygon(polygon, self.polygon_pen, self.polygon_brush)
            polygon_item.setZValue(1)  # 中间层

            polygon_item.vertex_markers = []  # 保存顶点标记
            polygon_item.text_labels = []  # 保存顶点编号
            # 添加顶点标记和编号
            for i, vertex in enumerate(self.current_vertices[:-1]):  # 不包括闭合点
                # 绘制顶点标记
                vertex_marker = self.scene.addEllipse(vertex.x() - 3, vertex.y() - 3, 6, 6, self.vertex_pen, self.vertex_brush)
                vertex_marker.polygon_ref = polygon_item
                polygon_item.vertex_markers.append(vertex_marker)
                # 添加顶点编号
                text = self.scene.addText(str(i + 1), QFont("Arial", 8))
                text.polygon_ref = polygon_item
                polygon_item.text_labels.append(text)
                text.setPos(vertex.x() + 5, vertex.y() - 10)
                text.setDefaultTextColor(QColor(255, 0, 0))
                text.setZValue(3)  # 最高层

            # 保存多边形
            self.polygon_items.append(polygon_item)

            # 清除当前多边形
            self.current_vertices = []
            if self.current_polygon:
                self.scene.removeItem(self.current_polygon)
                self.current_polygon = None

    def clear_polygons(self):
        """清除所有多边形"""
        self.current_vertices = []

        for item in self.polygon_items:
            self.scene.removeItem(item)
        self.polygon_items = []

        if self.current_polygon:
            self.scene.removeItem(self.current_polygon)
            self.current_polygon = None

        for item in self.scene.items():
            if isinstance(item, QGraphicsEllipseItem) or isinstance(item, QGraphicsTextItem):
                self.scene.removeItem(item)
        # 更新场景
        self.scene.update()

    def detect_selected_polygons(self, rect):
        """检测框选区域内的多边形"""
        self.selected_polygons = []
        for item in self.polygon_items:
            if item.boundingRect().intersects(rect):
                self.selected_polygons.append(item)
            if not self.selected_polygons:
                self.clear_highlight()

    def highlight_selected_polygons(self):
        """高亮显示选中的多边形"""
        self.clear_highlight()  # 先清除之前的高亮

        for item in self.selected_polygons:
            # 设置高亮样式
            item.setPen(self.highlight_pen)
            item.setBrush(self.highlight_brush)

            self.highlighted_polygons.append(item)
        if self.status_label:
            self.status_label.showMessage(f"框选 {len(self.selected_polygons)} 个多边形, 点击左键删除，点击右键取消")

    def clear_highlight(self):
        """清除多边形高亮显示"""
        for item in self.highlighted_polygons:
            item.setPen(self.polygon_pen)
            item.setBrush(QBrush(self.polygon_brush))

        self.highlighted_polygons = []

    def delete_selected_polygons(self):
        """删除选中的多边形"""
        if not self.selected_polygons:
            return
        # 清除多边形
        for item in self.selected_polygons:
            self.scene.removeItem(item)
            if item in self.polygon_items:
                self.polygon_items.remove(item)

        # 清除多边形对应的顶点标记和编号
        for polygon in self.selected_polygons:
            # 直接删除关联的顶点标记和编号
            for marker in polygon.vertex_markers:
                self.scene.removeItem(marker)
            for text in polygon.text_labels:
                self.scene.removeItem(text)
        if self.status_label:
            self.status_label.showMessage(f"框选删除 {len(self.selected_polygons)} 个多边形")
        self.logger.info(f"框选删除 {len(self.selected_polygons)} 个多边形")

    def clear_box_selection(self):
        """清除框选状态"""
        if self.box_item:
            self.scene.removeItem(self.box_item)
            self.box_item = None
        if self.temp_box_item:
            self.scene.removeItem(self.temp_box_item)
            self.temp_box_item = None
        self.selected_polygons = []
        self.box_state = 0
        self.box_start = None
        self.clear_highlight()

    def zoom_in(self):
        """放大图像"""
        zoom_step = self.config_manager.get("ui.zoom_step", 1.1)
        zoom_step_max_scale = self.config_manager.get("ui.zoom_step_max_scale", 5.0)
        if self.image_item and self.scale_factor < zoom_step_max_scale:
            self.scale_factor = min(self.scale_factor * zoom_step, zoom_step_max_scale)  # 限制最大缩放
            self.scale(zoom_step, zoom_step)
            self.zoomChanged.emit(self.scale_factor)

            # 更新状态栏
            if self.status_label:
                self.status_label.showMessage(f"缩放: {int(self.scale_factor * 100)}%")
        else:
            return

    def zoom_out(self):
        """缩小图像"""
        zoom_step = self.config_manager.get("ui.zoom_step")
        zoom_step_max_scale = self.config_manager.get("ui.zoom_step_min_scale")
        if self.image_item and self.scale_factor > zoom_step_max_scale:
            self.scale_factor = max(self.scale_factor / zoom_step, zoom_step_max_scale)  # 限制最小缩放
            self.scale(1 / zoom_step, 1 / zoom_step)
            self.zoomChanged.emit(self.scale_factor)

            # 更新状态栏
            if self.status_label:
                self.status_label.showMessage(f"缩放: {int(self.scale_factor * 100)}%")
        else:
            return

    def zoom_to_original(self):
        """恢复原始大小"""
        if self.image_item:
            self.resetTransform()
            self.scale_factor = 1.0
            self.zoomChanged.emit(self.scale_factor)

            # 更新状态栏
            if self.status_label:
                self.status_label.showMessage("缩放: 100%")

    def fit_to_window(self):
        """适应窗口"""
        if self.image_item:
            self.resetTransform()
            self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)
            self.scale_factor = self.transform().m11()  # 获取当前缩放因子
            self.zoomChanged.emit(self.scale_factor)

            # 更新状态栏
            if self.status_label:
                self.status_label.showMessage(f"缩放: {int(self.scale_factor * 100)}%")

    def get_polygons(self):
        """获取所有多边形的顶点坐标（图像坐标）"""
        polygons = []

        for item in self.polygon_items:
            polygon = item.polygon()
            points = []

            for i in range(polygon.size()):
                point = polygon[i]
                # 转换为图像坐标
                points.append((int(point.x()), int(point.y())))

            polygons.append(points)

        return polygons


class MaskViewer(QDialog):
    """使用QGraphicsView实现的掩码查看器"""
    saveRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("掩码预览")
        self.setMinimumSize(1200, 800)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 创建图形视图组件
        self.graphics_view = QGraphicsView()
        self.scene = QGraphicsScene()
        self.graphics_view.setScene(self.scene)
        self.graphics_view.setRenderHint(QPainter.Antialiasing)
        self.graphics_view.setDragMode(QGraphicsView.ScrollHandDrag)  # 支持平移
        self.graphics_view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.graphics_view.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

        # 设置视图属性
        self.graphics_view.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.graphics_view.setOptimizationFlags(QGraphicsView.DontAdjustForAntialiasing)
        self.graphics_view.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)

        # 允许视图扩展以填充可用空间
        self.graphics_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 布局设置
        layout = QVBoxLayout(self)
        layout.addWidget(self.graphics_view, 1)

        # 按钮和缩放控制
        button_layout = QHBoxLayout()

        self.save_btn = QPushButton("保存掩码")
        self.save_btn.clicked.connect(self.emit_save_request)

        self.zoom_in_btn = QPushButton("放大")
        self.zoom_in_btn.clicked.connect(lambda: self.zoom(1.2))
        self.zoom_out_btn = QPushButton("缩小")
        self.zoom_out_btn.clicked.connect(lambda: self.zoom(1 / 1.2))
        self.fit_window_btn = QPushButton("适应窗口")
        self.fit_window_btn.clicked.connect(self.fit_mask_to_view)

        button_layout.addWidget(self.save_btn)
        button_layout.addWidget(self.zoom_in_btn)
        button_layout.addWidget(self.zoom_out_btn)
        button_layout.addWidget(self.fit_window_btn)

        layout.addLayout(button_layout)

        # 状态变量
        self.mask_item = None  # 掩码图像项
        self.original_mask = None  # 原始掩码数据
        self.current_scale = 1.0
        self.mask_width = 0
        self.mask_height = 0

    def set_mask(self, mask):
        """设置掩码图像"""
        if mask is None or mask.size == 0:
            self.original_mask = None
            self.reset_scene()
            return

        self.original_mask = mask.copy() 
        self.reset_scene()

        h, w = mask.shape
        max_preview_size = 8192  # 预览图最大尺寸，不会卡
        scale = 1.0
        if max(h, w) > max_preview_size:
            scale = max_preview_size / max(h, w)
            new_w = int(w * scale)
            new_h = int(h * scale)
            show_mask = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        else:
            show_mask = mask

        self.render_mask(show_mask)  # 只渲染缩略图
        self.fit_mask_to_view()
        self.mask_width, self.mask_height = w, h

    def get_mask(self):
        """返回当前掩码数据"""
        return self.original_mask

    def reset_scene(self):
        """重置场景"""
        self.scene.clear()
        self.mask_item = None
        self.graphics_view.resetTransform()
        self.current_scale = 1.0

    def render_mask(self, mask):
        """渲染掩码到图形场景"""
        if mask is None or mask.size == 0:
            self.scene.addText("没有有效的掩码数据")
            return

        # 创建单色掩码（白色区域）
        color_mask = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
        color_mask[mask > 0] = [255, 255, 255]

        # 转换为QImage
        height, width, channel = color_mask.shape
        bytes_per_line = channel * width
        q_image = QImage(color_mask.data, width, height, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_image)

        # 添加到场景
        self.mask_item = self.scene.addPixmap(pixmap)
        self.mask_item.setToolTip("掩码区域")

        # 设置场景边界
        self.scene.setSceneRect(QRectF(pixmap.rect()))

        # 存储掩码尺寸
        self.mask_width = self.mask_item.boundingRect().width()
        self.mask_height = self.mask_item.boundingRect().height()

    def fit_mask_to_view(self):
        """自适应视图显示"""
        if self.mask_item:
            # 确保场景更新
            self.scene.update()

            # 获取掩码边界
            mask_rect = self.mask_item.boundingRect()

            # 如果掩码尺寸远小于视图，考虑放大显示
            view_rect = self.graphics_view.viewport().rect()
            view_width, view_height = view_rect.width(), view_rect.height()

            # 计算适应窗口的缩放比例
            scale_x = view_width / mask_rect.width()
            scale_y = view_height / mask_rect.height()
            scale = min(scale_x, scale_y)

            # 应用变换
            self.graphics_view.resetTransform()
            self.graphics_view.scale(scale, scale)

            # 居中显示
            self.graphics_view.centerOn(mask_rect.center())

            # 更新当前缩放因子
            self.current_scale = scale

            # 强制重绘
            self.graphics_view.viewport().update()

    def zoom(self, factor):
        """缩放视图"""
        if self.mask_item:
            self.graphics_view.scale(factor, factor)
            self.current_scale *= factor

    def resizeEvent(self, event):
        """窗口大小改变时自动适应"""
        super().resizeEvent(event)
        if self.mask_item:
            QTimer.singleShot(100, self.fit_mask_to_view)

    def emit_save_request(self):
        self.saveRequested.emit()
        self.close()


class ImageReplacer:
    """图像替换器 """

    def __init__(self, config_manager=None, logger=None):
        """初始化图像替换器，从配置中获取smooth_radius"""
        self.config_manager = config_manager
        self.logger = logger or logging.getLogger("MaskGenerator.ImageReplacer")
        self.smooth_radius = self.get_smooth_radius_from_config()

    def get_smooth_radius_from_config(self):
        """从配置中获取smooth_radius，若无则使用默认值"""
        if self.config_manager:
            # 从配置中获取smooth_radius，默认值为100
            return self.config_manager.get("image_processing.smooth_radius", 100)
        self.logger.warning("配置管理器未初始化，使用默认smooth_radius: 100")
        return 100  # 无配置管理器时使用默认值

    def update_smooth_radius(self):
        """更新smooth_radius值（当配置变更时调用）"""
        old_radius = self.smooth_radius
        self.smooth_radius = self.get_smooth_radius_from_config()
        if self.smooth_radius != old_radius:
            self.logger.info(f"smooth_radius从 {old_radius} 更新为: {self.smooth_radius}")

    def replace_region_python(self, original_path, mask_path, target_path, result_path):
        """分块完成替换版 """
        ds_ori = gdal.Open(original_path)
        ds_mask = gdal.Open(mask_path)
        ds_tar = gdal.Open(target_path)

        if ds_ori is None or ds_mask is None or ds_tar is None:
            error_msg = "无法打开输入影像文件，请检查文件路径！"
            # 关闭资源
            if ds_ori: del ds_ori
            if ds_mask: del ds_mask
            if ds_tar: del ds_tar
            gc.collect()
            raise Exception(error_msg)

            # 获取原始影像尺寸
        w = ds_ori.RasterXSize
        h = ds_ori.RasterYSize

        if w != ds_tar.RasterXSize or h != ds_tar.RasterYSize:
            error_msg = (f"图像尺寸错误！ \n原始图像：({w},{h})  \n目标图像：({ds_tar.RasterXSize},{ds_tar.RasterYSize})\n\n必须一致才能替换！")
            # 关闭资源
            del ds_ori, ds_mask, ds_tar
            gc.collect()
            raise Exception(error_msg)

        block = 4096
        result_data = np.zeros((h, w), dtype=np.uint16)
        driver = gdal.GetDriverByName("GTiff")

        # 分块统计
        sum_mask = 0.0
        sum_ori = 0.0
        sum_tar = 0.0
        for y in range(0, h, block):
            bh = min(block, h - y)
            ori = ds_ori.ReadAsArray(0, y, w, bh).astype(np.float32)
            mask = ds_mask.ReadAsArray(0, y, w, bh).astype(np.float32) / 255.0
            tar = ds_tar.ReadAsArray(0, y, w, bh).astype(np.float32)
            sum_mask += np.sum(mask)
            sum_ori += np.sum(ori * mask)
            sum_tar += np.sum(tar * mask)
            del ori, mask, tar

        delta = (sum_ori - sum_tar) / sum_mask if sum_mask != 0 else 0

        # 分块处理
        ksize = self.smooth_radius * 2 + 1
        for y in range(0, h, block):
            bh = min(block, h - y)
            ori = ds_ori.ReadAsArray(0, y, w, bh).astype(np.float32)
            mask = ds_mask.ReadAsArray(0, y, w, bh).astype(np.float32) / 255.0
            tar = ds_tar.ReadAsArray(0, y, w, bh).astype(np.float32)

            weight = cv2.GaussianBlur(mask, (ksize, ksize), 0)
            weight = np.clip(weight, 0, 1)
            res = ori * (1 - weight) + (tar + delta) * weight
            res = np.clip(res, 0, 65535).astype(np.uint16)

            # 写入内存
            result_data[y:y + bh, :] = res

            del ori, mask, tar, weight, res
            gc.collect()

            # 关闭所有输入
        del ds_ori, ds_mask, ds_tar
        gc.collect()

        self.writeTiff(result_data, result_path)
        del result_data
        gc.collect()

    def get_original_image_path(self, mask_path):
        """根据掩码路径推导原始图像路径"""
        base_name = os.path.splitext(os.path.basename(mask_path))[0]
        dir_name = os.path.dirname(mask_path)
        original_name = f"{base_name.replace('_mask', '_ori')}.tif"
        return os.path.join(dir_name, original_name)

    def get_target_image_path(self, mask_path):
        """根据掩码路径推导目标图像路径"""
        base_name = os.path.splitext(os.path.basename(mask_path))[0]
        dir_name = os.path.dirname(mask_path)
        clean_name = base_name.replace('_mask', '')
        pattern = re.compile(f'{re.escape(clean_name)}_\\d{{6}}.tif')

        # 遍历目录查找符合条件的文件
        for filename in os.listdir(dir_name):
            if pattern.match(filename):
                return os.path.join(dir_name, filename)
        default_name = f"{clean_name}_250410.tif"
        return os.path.join(dir_name, default_name)

    def get_result_image_path(self, mask_path):
        """根据掩码路径推导生成图像路径"""
        base_name = os.path.splitext(os.path.basename(mask_path))[0]
        dir_name = os.path.dirname(mask_path)
        clean_name = base_name.replace('_mask', '') + '.tif'
        return os.path.join(dir_name, clean_name)

    def writeTiff(self, im_data, path):
        """写出tif文件"""
        # 避免删除rpc.txt文件，改名输出
        if os.path.exists(path):
            old_file_path = path[:-4] + '_old2.tif'
            if os.path.exists(old_file_path):
                os.remove(old_file_path)
                os.rename(path, old_file_path)
            else:
                os.rename(path, old_file_path)
        im_height, im_width = np.shape(im_data)
        driver = gdal.GetDriverByName("GTiff")
        dataset = driver.Create(path, im_width, im_height, 1, gdal.GDT_UInt16)
        dataset.GetRasterBand(1).WriteArray(im_data)
        del dataset

class ConfigManager:
    """配置文件管理类，负责读取、保存和管理软件配置"""

    def __init__(self, app_dir):
        """初始化配置管理器"""
        self.app_dir = app_dir
        self.config_dir = os.path.join(app_dir, "config")
        self.config_file = os.path.join(self.config_dir, "settings.json")
        self.default_config = {
            "image_processing": {
                "thread_count": max(1, os.cpu_count() - 2),
                "smooth_radius": 100,
                "max_display_size": 20032,
                "equalization_enabled": True
            },
            "ui": {
                "zoom_step": 1.1,
                "zoom_in_factor": 1.1,
                "zoom_out_factor": 0.9,
                "zoom_max_scale": 2.0,
                "zoom_min_scale": 0.01,
                "zoom_step_max_scale": 5.0,
                "zoom_step_min_scale": 0.01,
                "image_rect_factor": 0.2,
                "toolbar_visibility": True,
                "statusbar_visibility": True
            },
            "paths": {
                "last_open_dir": "",
                "last_save_dir": "",
                "default_mask_dir": ""
            }
        }
        self.config = self.load_config()

    def load_config(self):
        """加载配置文件，如果不存在则创建默认配置"""
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                # 合并默认配置和用户配置
                return self._merge_configs(self.default_config, config)
            except Exception as e:
                print(f"加载配置文件失败: {str(e)}")
                return self.default_config
        else:
            self.save_config()
            return self.default_config

    def save_config(self):
        """保存配置文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"保存配置文件失败: {str(e)}")
            return False

    def _merge_configs(self, default, user):
        """合并默认配置和用户配置"""
        result = default.copy()
        for key in user:
            if key in default and isinstance(default[key], dict) and isinstance(user[key], dict):
                result[key] = self._merge_configs(default[key], user[key])
            else:
                result[key] = user[key]
        return result

    def get(self, key, default_value=None):
        """获取配置项"""
        keys = key.split('.')
        current = self.config
        for k in keys:
            if k in current:
                current = current[k]
            else:
                return default_value
        return current

    def set(self, key, value):
        """设置配置项"""
        keys = key.split('.')
        current = self.config
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
        self.save_config()

class SettingsDialog(QDialog):
    """参数设置对话框，用于修改软件配置"""

    settings_changed = pyqtSignal()

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.init_ui()

    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("参数设置")
        self.setMinimumSize(1000, 900)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        main_layout = QVBoxLayout(self)

        tab_widget = QTabWidget()

        # 图像处理设置选项卡
        processing_tab = QWidget()
        processing_layout = QVBoxLayout(processing_tab)

        # 线程数设置
        thread_layout = QHBoxLayout()
        thread_layout.addWidget(QLabel("图像处理线程数:"))
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, os.cpu_count())
        thread_count = self.config_manager.get("image_processing.thread_count", max(1, os.cpu_count() - 2))
        self.thread_spin.setValue(thread_count)
        thread_layout.addWidget(self.thread_spin)
        processing_layout.addLayout(thread_layout)

        # 平滑半径设置
        smooth_layout = QHBoxLayout()
        smooth_layout.addWidget(QLabel("掩码边缘平滑半径:"))
        self.smooth_spin = QSpinBox()
        self.smooth_spin.setRange(1, 500)
        smooth_radius = self.config_manager.get("image_processing.smooth_radius", 100)
        self.smooth_spin.setValue(smooth_radius)
        smooth_layout.addWidget(self.smooth_spin)
        processing_layout.addLayout(smooth_layout)

        # 最大显示尺寸设置
        max_display_layout = QHBoxLayout()
        max_display_layout.addWidget(QLabel("图像最大显示尺寸:"))
        self.max_display_spin = QSpinBox()
        self.max_display_spin.setRange(2048, 32768)
        max_display_size = self.config_manager.get("image_processing.max_display_size", 8192)
        self.max_display_spin.setValue(max_display_size)
        max_display_layout.addWidget(self.max_display_spin)
        processing_layout.addLayout(max_display_layout)

        # 直方图均衡化设置
        eq_layout = QHBoxLayout()
        eq_layout.addWidget(QLabel("默认启用直方图均衡化:"))
        self.eq_checkbox = QPushButton("关")
        self.eq_checkbox.setCheckable(True)
        eq_enabled = self.config_manager.get("image_processing.equalization_enabled", True)
        self.eq_checkbox.setChecked(eq_enabled)
        self.update_eq_button_text(eq_enabled)
        self.eq_checkbox.toggled.connect(self.toggle_eq_check)
        eq_layout.addWidget(self.eq_checkbox)
        processing_layout.addLayout(eq_layout)

        processing_layout.addStretch()
        tab_widget.addTab(processing_tab, "图像处理")

        # 路径设置选项卡
        paths_tab = QWidget()
        paths_layout = QVBoxLayout(paths_tab)

        # 上次打开目录
        open_layout = QHBoxLayout()
        open_layout.addWidget(QLabel("默认打开目录:"))
        self.open_dir_edit = QLineEdit()
        open_dir = self.config_manager.get("paths.last_open_dir", "")
        self.open_dir_edit.setText(open_dir)
        open_btn = QPushButton("浏览...")
        open_btn.clicked.connect(lambda: self.browse_dir(self.open_dir_edit))
        open_layout.addWidget(self.open_dir_edit)
        open_layout.addWidget(open_btn)
        paths_layout.addLayout(open_layout)

        # 上次保存目录
        save_layout = QHBoxLayout()
        save_layout.addWidget(QLabel("默认保存目录:"))
        self.save_dir_edit = QLineEdit()
        save_dir = self.config_manager.get("paths.last_save_dir", "")
        self.save_dir_edit.setText(save_dir)
        save_btn = QPushButton("浏览...")
        save_btn.clicked.connect(lambda: self.browse_dir(self.save_dir_edit))
        save_layout.addWidget(self.save_dir_edit)
        save_layout.addWidget(save_btn)
        paths_layout.addLayout(save_layout)

        # 默认掩码目录
        mask_layout = QHBoxLayout()
        mask_layout.addWidget(QLabel("默认掩码保存目录:"))
        self.mask_dir_edit = QLineEdit()
        mask_dir = self.config_manager.get("paths.default_mask_dir", "")
        self.mask_dir_edit.setText(mask_dir)
        mask_btn = QPushButton("浏览...")
        mask_btn.clicked.connect(lambda: self.browse_dir(self.mask_dir_edit))
        mask_layout.addWidget(self.mask_dir_edit)
        mask_layout.addWidget(mask_btn)
        paths_layout.addLayout(mask_layout)

        paths_layout.addStretch()
        tab_widget.addTab(paths_tab, "路径设置")

        # 添加选项卡到主布局
        main_layout.addWidget(tab_widget)

        # 按钮布局
        button_layout = QHBoxLayout()
        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self.save_settings)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        main_layout.addLayout(button_layout)

    def browse_dir(self, line_edit):
        """浏览目录并设置到输入框"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择目录")
        if dir_path:
            line_edit.setText(dir_path)

    def update_eq_button_text(self, checked):
        """根据按钮状态更新显示文本"""
        self.eq_checkbox.setText("开" if checked else "关")

    def toggle_eq_check(self, checked):
        """切换直方图均衡化状态"""
        self.update_eq_button_text(checked)
        # 更新配置
        self.config_manager.set("image_processing.equalization_enabled", checked)

    def save_settings(self):
        """保存设置到配置文件"""
        # 图像处理设置
        self.config_manager.set("image_processing.thread_count", self.thread_spin.value())
        self.config_manager.set("image_processing.smooth_radius", self.smooth_spin.value())
        self.config_manager.set("image_processing.max_display_size", self.max_display_spin.value())
        self.config_manager.set("image_processing.equalization_enabled", self.eq_checkbox.isChecked())

        # 路径设置
        self.config_manager.set("paths.last_open_dir", self.open_dir_edit.text())
        self.config_manager.set("paths.last_save_dir", self.save_dir_edit.text())
        self.config_manager.set("paths.default_mask_dir", self.mask_dir_edit.text())

        QMessageBox.information(self, "成功", "设置已保存")
        self.settings_changed.emit()
        self.accept()


class MainWindow(QMainWindow):
    """主窗口逻辑"""
    def __init__(self):
        super().__init__()
        # 初始化日志
        self.init_logging()

        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(os.cpu_count())
        self.start_time = datetime.datetime.now()
        self.statusBar = QStatusBar()
        self.logger.info(f"软件启动: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # 初始化图像查看器
        self.image_viewer = ImageViewer()
        self.image_viewer.status_label = self.statusBar
        self.image_viewer.main_window = self

        # 初始化配置管理器
        self.app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.config_manager = ConfigManager(self.app_dir)

        # 连接信号
        self.image_viewer.zoomChanged.connect(self.update_zoom_status)
        self.image_viewer.posChanged.connect(self.update_coordinate_status)
        self.image_viewer.imageDropped.connect(self.load_image_via_drag)
        self.image_viewer.zoomChanged.connect(self.adjust_cross_position)

        # 添加图标
        self.setWindowIcon(QIcon('Mask.ico'))

        # 创建UI
        self.init_ui()
        self.setup_layout()

        # 初始化图像替换器
        self.init_image_replacer()

        # 掩码查看器
        self.mask_viewer = None
        self.thread = None

        # 初始化状态
        self.is_drawing = False
        self.box_select_mode = False
        self.is_loading = False
        self.default_equalization = self.config_manager.get('image_processing.equalization_enabled')
        self.update_ui_state()

        #十字叉
        self.cross_item = None
        self.cross_timer = QTimer(self)
        self.cross_timer.timeout.connect(self.hide_cross)

        # 添加信号连接
        self.connect_mask_signals()

    def init_logging(self):
        """初始化日志配置"""
        # 创建日志记录器
        self.logger = logging.getLogger("MaskGenerator")
        self.logger.setLevel(logging.DEBUG)

        # 日志格式
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        # 控制台处理器（显示INFO及以上级别）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        # 日期轮转文件处理器（每天生成一个新文件）
        if getattr(sys, 'frozen', False):
            app_path = os.path.dirname(sys.executable)  
        else:
            app_path = os.path.dirname(os.path.abspath(__file__))

        log_dir = os.path.join(app_path, "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except:
            pass

        if not getattr(sys, 'frozen', False):
            console = logging.StreamHandler()
            console.setLevel(logging.INFO)
            console.setFormatter(formatter)
            self.logger.addHandler(console)

            # 文件日志
        try:
            file_handler = TimedRotatingFileHandler(
                os.path.join(log_dir, "mask_generator.log"),
                when="midnight",
                interval=1,
                backupCount=7,
                encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            file_handler.suffix = "%Y%m%d.log"
            self.logger.addHandler(file_handler)
        except:
            pass

    def init_image_replacer(self):
        # 初始化图像替换器
        self.image_replacer = ImageReplacer(config_manager=self.config_manager)
        # 最后保存的掩码路径
        self.last_saved_mask_path = None

    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("卫星图像掩码生成工具")
        self.setGeometry(200, 200, 2200, 1600)
        # 创建菜单栏
        self.create_menus()
        # 创建工具栏
        self.create_toolbars()
        # 左侧状态栏
        self.setStatusBar(self.statusBar)
        self.statusBar.setStyleSheet("""
              QStatusBar {
                  padding-left: 8px;   /* 左侧内容距左边界8px */
                  padding-right: 8px;  /* 右侧内容距右边界8px */
              }
          """)
        # 右侧永久标签用于显示当前文件名称
        self.current_file_label = QLabel()
        self.current_file_label.setAlignment(Qt.AlignRight)
        self.current_file_label.setMinimumWidth(200)
        self.statusBar.addPermanentWidget(self.current_file_label)

        self.statusBar.showMessage("就绪")


    def create_menus(self):
        """创建菜单栏"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件")

        self.load_action = QAction("加载卫星图像", self)
        self.load_action.setShortcut("Ctrl+O")
        self.load_action.triggered.connect(lambda checked: self.load_image())
        file_menu.addAction(self.load_action)

        self.save_mask_action = QAction("保存掩码", self)
        self.save_mask_action.setShortcut("Ctrl+S")
        self.save_mask_action.triggered.connect(self.save_mask)
        self.save_mask_action.setEnabled(False)
        file_menu.addAction(self.save_mask_action)

        file_menu.addSeparator()

        self.exit_action = QAction("退出", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        # 编辑菜单
        edit_menu = menubar.addMenu("编辑")

        self.clear_lines_action = QAction("清除线条", self)
        self.clear_lines_action.setShortcut("Ctrl+L")
        self.clear_lines_action.triggered.connect(self.clear_polygons)
        # self.clear_lines_action.setEnabled(False)
        edit_menu.addAction(self.clear_lines_action)

        self.clear_image_action = QAction("清除图像", self)
        self.clear_image_action.setShortcut("Ctrl+D")
        self.clear_image_action.triggered.connect(self.clear_current_image)
        self.clear_image_action.setEnabled(False)
        edit_menu.addAction(self.clear_image_action)

        self.draw_action = QAction("绘制模式", self)
        self.draw_action.setCheckable(True)
        self.draw_action.setShortcut("Ctrl+X")
        self.draw_action.triggered.connect(self.toggle_drawing_mode)
        self.draw_action.setEnabled(False)
        edit_menu.addAction(self.draw_action)

        self.box_select_action = QAction("框选删除", self)
        self.box_select_action.setCheckable(True)
        self.box_select_action.setShortcut("Ctrl+C")
        self.box_select_action.triggered.connect(self.toggle_box_select_mode)
        self.box_select_action.setEnabled(False)
        edit_menu.addAction(self.box_select_action)

        self.undo_mode_action = QAction("取消当前模式", self)
        self.box_select_action.setCheckable(True)
        self.undo_mode_action.setShortcut("Ctrl+Z")
        self.undo_mode_action.triggered.connect(self.undo_current_mode)
        edit_menu.addAction(self.undo_mode_action)

        # 视图菜单
        view_menu = menubar.addMenu("视图")

        self.equalize_action = QAction("直方图均衡化", self)
        self.equalize_action.setShortcut("Ctrl+E")
        self.equalize_action.triggered.connect(self.apply_image_equalization)
        self.equalize_action.setEnabled(False)
        view_menu.addAction(self.equalize_action)

        self.zoom_in_action = QAction("放大", self)
        self.zoom_in_action.setShortcut("Ctrl++")
        self.zoom_in_action.triggered.connect(self.image_viewer.zoom_in)
        view_menu.addAction(self.zoom_in_action)

        self.zoom_out_action = QAction("缩小", self)
        self.zoom_out_action.setShortcut("Ctrl+-")
        self.zoom_out_action.triggered.connect(self.image_viewer.zoom_out)
        view_menu.addAction(self.zoom_out_action)

        self.zoom_original_action = QAction("原始大小", self)
        self.zoom_original_action.setShortcut("Ctrl+0")
        self.zoom_original_action.triggered.connect(self.image_viewer.zoom_to_original)
        view_menu.addAction(self.zoom_original_action)

        view_menu.addSeparator()

        self.fit_window_action = QAction("适应窗口", self)
        self.fit_window_action.setShortcut("Ctrl+F")
        self.fit_window_action.triggered.connect(self.image_viewer.fit_to_window)
        view_menu.addAction(self.fit_window_action)

        thread_menu = menubar.addMenu("线程数")
        thread_action = QWidgetAction(self)
        # 创建一个包含标签和spinbox的小部件
        thread_widget = QWidget()
        thread_layout = QHBoxLayout(thread_widget)
        thread_layout.setContentsMargins(5, 0, 5, 0)

        thread_label = QLabel("线程数设置为:")
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, os.cpu_count())
        self.thread_spin.setValue(max(1, os.cpu_count() - 2))
        self.thread_spin.setFixedWidth(100)  # 设置固定宽度，避免菜单过宽
        self.thread_spin.valueChanged.connect(self.on_thread_count_changed)

        thread_layout.addWidget(thread_label)
        thread_layout.addWidget(self.thread_spin)
        thread_action.setDefaultWidget(thread_widget)
        thread_menu.addSeparator()
        thread_menu.addAction(thread_action)

        # 添加设置菜单
        settings_menu = self.menuBar().addMenu("设置")

        self.settings_action = QAction("参数设置", self)
        self.settings_action.setShortcut("Ctrl+P")
        self.settings_action.triggered.connect(self.show_settings_dialog)
        settings_menu.addAction(self.settings_action)

        self.import_config_action = QAction("导入配置", self)
        self.import_config_action.triggered.connect(self.import_config)
        settings_menu.addAction(self.import_config_action)

        self.export_config_action = QAction("导出配置", self)
        self.export_config_action.triggered.connect(self.export_config)
        settings_menu.addAction(self.export_config_action)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助")

        self.about_action = QAction("关于...", self)
        self.about_action.triggered.connect(self.show_about)
        help_menu.addAction(self.about_action)

    def create_toolbars(self):
        """创建工具栏"""
        # 文件工具栏
        file_toolbar = QToolBar("文件", self)
        self.addToolBar(file_toolbar)

        file_toolbar.addAction(self.load_action)
        file_toolbar.addAction(self.save_mask_action)

        # 编辑工具栏
        edit_toolbar = QToolBar("编辑", self)
        self.addToolBar(edit_toolbar)
        edit_toolbar.addAction(self.draw_action)
        edit_toolbar.addAction(self.box_select_action)
        edit_toolbar.addAction(self.clear_lines_action)
        edit_toolbar.addAction(self.clear_image_action)
        edit_toolbar.addAction(self.equalize_action)

        # 视图工具栏
        view_toolbar = QToolBar("视图", self)
        self.addToolBar(view_toolbar)
        view_toolbar.addAction(self.zoom_in_action)
        view_toolbar.addAction(self.zoom_out_action)
        view_toolbar.addAction(self.zoom_original_action)
        view_toolbar.addAction(self.fit_window_action)
        view_toolbar.addSeparator()

        # 创建坐标输入框工具栏
        coord_toolbar = QToolBar("坐标跳转", self)
        coord_toolbar.addWidget(QLabel("坐标:"))
        self.addToolBar(coord_toolbar)
        self.coord_input = QLineEdit()
        self.coord_input.setMinimumWidth(300)
        self.coord_input.setMaximumWidth(800)
        self.coord_input.setPlaceholderText("输入坐标 (X,Y): 123.456,456.123")
        self.coord_input.setClearButtonEnabled(True)
        self.coord_input.returnPressed.connect(self.jump_to_coordinate)  # 回车触发

        self.jump_btn = QPushButton("跳转")
        self.jump_btn.clicked.connect(self.jump_to_coordinate)
        coord_toolbar.addWidget(self.coord_input)
        coord_toolbar.addWidget(self.jump_btn)
        coord_toolbar.addSeparator()

        # 生成掩码按钮
        mask_toolbar = QToolBar("掩码生成", self)
        self.addToolBar(mask_toolbar)
        self.generate_btn = QPushButton("生成掩码")
        self.generate_btn.clicked.connect(self.generate_mask)
        self.generate_btn.setEnabled(False)
        mask_toolbar.addWidget(self.generate_btn)
        mask_toolbar.addSeparator()

        # 替换工具栏
        replace_toolbar = QToolBar("图像替换", self)
        self.addToolBar(replace_toolbar)

        self.replace_btn = QPushButton("替换掩码区域")
        self.replace_btn.clicked.connect(self.trigger_region_replacement)
        self.replace_btn.setEnabled(False)  # 初始禁用
        replace_toolbar.addWidget(self.replace_btn)
        replace_toolbar.addSeparator()

    def setup_layout(self):
        """设置布局"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)

        # 图像查看器
        main_layout.addWidget(self.image_viewer)

    def show_settings_dialog(self):
        """显示参数设置对话框"""
        dialog = SettingsDialog(self.config_manager, self)
        dialog.settings_changed.connect(self.on_settings_changed)
        dialog.exec_()

    def on_settings_changed(self):
        """处理设置变更事件"""
        thread_count = self.config_manager.get("image_processing.thread_count")
        cv2.setNumThreads(thread_count)
        self.thread_spin.setValue(thread_count)
        self.logger.info(f"设置变更: 线程数更新为 {thread_count}")

        # 更新平滑半径设置
        smooth_radius = self.config_manager.get("image_processing.smooth_radius")
        # 确保平滑半径是奇数（高斯模糊要求）
        if smooth_radius % 2 == 0:
            smooth_radius += 1
            if hasattr(self, 'image_replacer'):
                self.image_replacer.update_smooth_radius()
            self.config_manager.set("image_processing.smooth_radius", smooth_radius)
        self.logger.info(f"设置变更: 平滑半径更新为 {smooth_radius}")

        # 更新默认进行直方图均衡化设置
        equalization_enabled = self.config_manager.get("image_processing.equalization_enabled")
        self.default_equalization = equalization_enabled
        self.logger.info(f"设置变更: 直方图均衡化 {'启用' if equalization_enabled else '禁用'}")

        # 更新其他受影响的UI元素
        self.update_ui_state()

    def import_config(self):
        """导入配置文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入配置文件", self.app_dir, "JSON文件 (*.json)"
        )
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    imported_config = json.load(f)

                # 合并导入的配置
                for key in imported_config:
                    self.config_manager.config[key] = imported_config[key]

                self.config_manager.save_config()
                QMessageBox.information(self, "成功", "配置文件导入成功")
                self.on_settings_changed()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导入配置文件失败: {str(e)}")

    def export_config(self):
        """导出配置文件"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出配置文件", os.path.join(self.app_dir, "settings_export.json"), "JSON文件 (*.json)"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(self.config_manager.config, f, ensure_ascii=False, indent=4)
                QMessageBox.information(self, "成功", "配置文件导出成功")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出配置文件失败: {str(e)}")

    def on_thread_count_changed(self, value):
        """线程数改变时更新设置"""
        # 检查是否为有效线程数
        if not isinstance(value, int) or value < 1 or value > os.cpu_count():
            self.logger.warning(f"无效的线程数设置: {value}")
            return

        # 设置线程数
        cv2.setNumThreads(value)

        status_msg = f"图像处理线程数已设置为: {value}"
        self.statusBar.showMessage(status_msg, 3000)  # 显示3秒后自动消失
        self.logger.info(f"用户调整线程数: {value}")

        self.update_ui_state()

    def load_image_via_drag(self, path):
        """通过拖放加载图像"""
        self.logger.info(f"通过拖放加载图像: {path}")
        self.load_image(path)  # 复用原有的加载逻辑

    def toggle_drawing_mode(self, enable):
        """绘图模式切换"""
        # 检查是否有图像加载
        if not self.image_viewer.scene.items():
            QMessageBox.information(self, "提示", "请先加载图像")
            if self.draw_action.isChecked():
                self.draw_action.setChecked(False)
            return

        self.is_drawing = enable
        self.image_viewer.toggle_drawing_mode(enable)
        self.draw_action.setChecked(enable)
        self.toggle_box_select_mode(False)

        # 置拖动模式和光标
        if not enable:
            self.image_viewer.setDragMode(QGraphicsView.NoDrag)  # 禁用拖动模式
            self.image_viewer.setCursor(Qt.ArrowCursor)  # 恢复默认光标

        self.statusBar.showMessage("绘图模式: 开启" if enable else "绘图模式: 关闭")
        self.logger.info(f"绘图模式切换: {'开启' if enable else '关闭'}")
        self.update_ui_state()

    def toggle_box_select_mode(self, enable):
        """切换框选模式"""
        # 检查是否有图像和多边形
        if not self.image_viewer.scene.items() or not self.image_viewer.polygon_items:
            if enable:
                QMessageBox.information(self, "提示", "请先加载图像并绘制多边形")
                if self.box_select_action.isChecked():
                    self.box_select_action.setChecked(False)
                return

        self.box_select_mode = enable
        self.image_viewer.toggle_box_select_mode(enable)
        self.box_select_action.setChecked(enable)

        # 重置拖动模式和光标
        if not enable:
            self.image_viewer.setDragMode(QGraphicsView.NoDrag)
            self.image_viewer.setCursor(Qt.ArrowCursor)
        self.statusBar.showMessage("框选模式: 开启，点击选择两个对角点" if enable else "框选模式: 关闭")
        self.logger.info(f"框选模式切换: {'开启' if enable else '关闭'}")

        self.update_ui_state()

    def undo_current_mode(self):
        """取消当前模式"""
        if not self.box_select_mode and not self.is_drawing:
            return

        self.is_drawing = False
        self.box_select_mode = False
        self.image_viewer.toggle_drawing_mode(False)
        self.image_viewer.toggle_box_select_mode(False)
        self.draw_action.setChecked(False)
        self.box_select_action.setChecked(False)

        self.statusBar.showMessage("已取消当前模式")
        self.logger.info("用户取消当前模式")
        self.update_ui_state()

    def update_ui_state(self):
        """更新 UI按钮状态"""
        #判定条件
        has_image = self.image_viewer.image is not None
        has_lines = len(self.image_viewer.polygon_items) > 0
        has_mask = self.mask_viewer is not None and self.mask_viewer.mask is not None
        has_replacer = self.image_replacer is not None
        has_polygons = len(self.image_viewer.polygon_items) > 0

        # self.clear_lines_action.setEnabled(has_lines)
        self.clear_image_action.setEnabled(has_image)
        self.equalize_action.setEnabled(has_image and not self.image_viewer.is_equalized)
        # 生成掩码按钮仅在以下条件满足时启用：
        # 1. 有图像加载；2. 有绘制的线条；3. 绘图模式未开启
        self.generate_btn.setEnabled(has_image and has_lines and not self.is_drawing)
        self.save_mask_action.setEnabled(has_mask)
        self.draw_action.setEnabled(has_image)  # 确保绘图模式按钮仅在有图像时可用
        # 仅当有掩码且替换器可用时启用
        self.replace_btn.setEnabled(has_mask and has_replacer)
        # 仅当有封闭框且非绘制模式下启用
        self.box_select_action.setEnabled(has_polygons and not self.is_drawing)

    def update_zoom_status(self, scale_factor):
        """更新状态栏中的缩放信息"""
        zoom_percent = int(scale_factor * 100)
        self.statusBar.showMessage(f"缩放: {zoom_percent}%")

    def update_coordinate_status(self, scene_pos):
        """更新状态栏中的缩放信息"""
        try:
            scale = self.thread.scale
            # 计算原图真实坐标
            real_x = scene_pos.x() / scale
            real_y = scene_pos.y() / scale

            # 判断是否在图片范围内
            if self.image_viewer.image_item and self.image_viewer.image_item.boundingRect().contains(scene_pos):
                self.statusBar.showMessage(f"坐标:  {int(real_x), int(real_y)}")
            else:
                self.update_zoom_status(self.image_viewer.scale_factor)
        except:
            pass

    def update_current_file_path(self, path=None):
        """更新当前文件路径显示"""
        if path:
            file_name = os.path.basename(path)
            self.current_file_label.setText(f"文件: {file_name}")
        else:
            self.current_file_label.setText("")

    def clear_polygons(self):
        """清除当多边形"""
        # 检查是否有图像正在显示
        if not self.image_viewer.scene.items():
            QMessageBox.information(self, "提示", "当前没有加载图像")
            return

        # 关闭掩码查看器（如果已打开）
        if self.mask_viewer and self.mask_viewer.isVisible():
            self.mask_viewer.close()
        self.mask_viewer = None

        # 清除线条
        self.image_viewer.clear_polygons()
        if self.box_select_mode:
            self.toggle_box_select_mode(False)

        # 更新UI状态
        self.update_ui_state()

        self.statusBar.showMessage(f"已清除所有绘制的多边形")
        self.logger.info(f"清除所有绘制的多边形")

    def clear_current_image(self):
        """清除当前显示的图像和所有绘制内容"""
        # 检查是否有图像正在显示
        if not self.image_viewer.scene.items():
            QMessageBox.information(self, "提示", "当前没有加载图像")
            return

        # 关闭掩码查看器（如果已打开）
        if self.mask_viewer and self.mask_viewer.isVisible():
            self.mask_viewer.close()
        self.mask_viewer = None

        # 安全地停止图像加载线程
        self.safe_stop_thread()

        # 清除线条
        self.image_viewer.clear_polygons()
        self.undo_current_mode()

        # 重置图像查看器状态
        self.reset_view_state()

        # 释放OpenCV资源
        if hasattr(self, 'thread') and self.thread:
            # 安全释放图像数据
            self.thread.image = None
            self.thread = None

        # 更新UI状态
        self.update_ui_state()

        # 强制垃圾回收
        if hasattr(self, 'thread'):
            self.thread = None
        gc.collect()

        # 更新状态栏
        self.update_current_file_path()
        self.statusBar.showMessage("图像已清除")
        self.logger.info("图像已清除，资源已释放")

    def safe_stop_thread(self):
        """安全地停止线程，避免访问冲突"""
        if self.thread and self.thread.isRunning():
            try:
                # 设置标志让线程优雅退出（如果线程支持）
                if hasattr(self.thread, 'stop_requested'):
                    self.thread.stop_requested = True

                # 等待线程完成（最多3秒）
                if not self.thread.wait(3000):
                    self.logger.warning("线程无法正常结束，强制终止")
                    self.thread.terminate()
                    self.thread.wait(2000)  # 等待终止完成

                self.thread = None
                self.logger.info("图像加载线程已安全停止")
            except Exception as e:
                self.logger.error(f"停止图像加载线程时出错: {str(e)}")

    def load_image(self, path=None):
        """加载图像（清除原有线条和掩码）"""
        self.logger.info("图像加载...")

        if hasattr(self, 'thread') and self.thread:
            self.thread.image = None
            self.thread = None
        gc.collect()

        # 检查当前是否有图像，若有则先清除
        if self.image_viewer.image is not None:
            self.clear_current_image()

        # 强制重置场景和视图状态
        self.reset_view_state()
        # 安全地停止之前的图像加载线程
        self.safe_stop_thread()

        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "选择卫星图像", "", "TIF文件 (*.tif *.tiff);;PNG/JPG文件 (*.png *.jpg)")
        if path:
            self.logger.debug(f"选择的图像路径: {path}")
            # 检查路径中是否包含中文字符
            contains_chinese = any('\u4e00' <= char <= '\u9fff' for char in path)

            if contains_chinese:
                # 显示警告信息
                QMessageBox.critical(
                    self,
                    "错误：不支持中文文件名和路径",
                    "路径中包含中文字符，请使用英文路径文件名！",
                    QMessageBox.Ok
                )
                self.logger.warning(f"用户尝试加载中文路径图像: {path}，已阻止")
                return

            self.draw_action.setChecked(False)
            self.draw_action.setEnabled(True)

            self.is_loading = True
            self.statusBar.showMessage("正在加载图像...")
            self.thread = ImageLoadThread(path, self.config_manager)
            self.thread.thread_count_updated.connect(self.on_thread_count_updated)
            self.thread.loadFinished.connect(self.on_image_loaded)
            self.thread.start()

    def reset_view_state(self):
        """重置视图状态，清除场景并恢复初始设置"""
        # 清除场景所有项（包括残留的图像和多边形）
        self.image_viewer.scene.clear()
        # 重置均衡化状态
        self.image_viewer.is_equalized = False
        # 绘制模式关闭
        self.is_drawing = False
        self.image_viewer.toggle_drawing_mode(False)

        # 重置缩放因子和变换
        self.image_viewer.resetTransform()
        self.image_viewer.scale_factor = 1.0

        # 关闭滚动条
        self.image_viewer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.image_viewer.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 释放旧图像引用
        self.image_viewer.image = None
        self.image_viewer.image_item = None
        self.cross_item = None
        # self.image_viewer.scale(self.image_viewer.scale_factor, self.image_viewer.scale_factor)

    def on_image_loaded(self, success):
        """处理图像加载完成事件"""
        if not success:
            QMessageBox.warning(self, "错误", "无法加载图像，请检查文件路径和格式")
            self.statusBar.showMessage("图像加载失败")
            self.logger.info("图像加载失败")
            return

        # 检查线程是否仍在运行（防止信号延迟）
        if self.thread and self.thread.isRunning():
            self.logger.info("等待图像加载线程完成...")
            if not self.thread.wait(5000):  # 最多等待5秒
                self.logger.warning("图像加载线程超时，强制终止")
                self.thread.terminate()
                self.thread.wait()  # 确保资源释放

        if self.thread and self.thread.isRunning():
            self.logger.error("图像加载线程无法终止，可能存在资源泄漏")
            return

        self.logger.info("图像加载成功，开始更新界面")

        # 更新图像查看器
        self.image_viewer.load_image(self.thread.image)

        # 打开滚动条
        self.image_viewer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.image_viewer.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        # 强制适配新图像尺寸
        self.image_viewer.fitInView(
            self.image_viewer.scene.itemsBoundingRect(),
            Qt.KeepAspectRatio
        )

        if self.default_equalization:
            self.apply_image_equalization()

        self.image_viewer.fit_to_window()

        self.is_loading = False
        self.statusBar.showMessage("图像加载完成")

        # 更新UI状态
        if self.thread and self.thread.image_path:
            self.update_current_file_path(self.thread.image_path)  # 加载成功时显示文件名
        else:
            self.update_current_file_path()
        self.update_ui_state()

    def on_thread_count_updated(self, thread_count):
        self.logger.info(f"根据图像大小调整线程数: {thread_count}")

    def apply_image_equalization(self):
        """触发图像均衡化处理"""
        if self.image_viewer.image is None:
            QMessageBox.warning(self, "提示", "请先加载图像")
            return

        if self.image_viewer.is_equalized:
            QMessageBox.information(self, "提示", "当前图像已进行过直方图均衡化")
            return

        # 显示进度条
        self.progress = QProgressDialog("正在均衡化处理图像...", None, 0, 0, self)
        self.progress.setWindowTitle("处理中")
        self.progress.resize(500, 300)
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.show()

        # 创建工作线程
        worker = Worker(self._do_equalization)
        worker.signals.result.connect(self._on_equalization_done)
        worker.signals.finished.connect(self.progress.close)
        worker.signals.error.connect(self._on_error)

        # 提交到线程池
        self.threadpool.start(worker)

    def _do_equalization(self):
        """在线程中执行均衡化处理"""
        # 复制图像数据以避免多线程问题
        image = self.image_viewer.image.copy()

        # 执行均衡化
        if len(image.shape) == 3:
            gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray_image = image.copy()

        if gray_image.dtype == np.uint16:
            min_val = np.min(gray_image)
            max_val = np.max(gray_image)
            if max_val == min_val:
                return None
            gray_8bit = ((gray_image - min_val) / (max_val - min_val) * 255).astype(np.uint8)
        else:
            gray_8bit = gray_image

        equalized = cv2.equalizeHist(gray_8bit)
        return equalized

    def _on_equalization_done(self, result):
        """处理均衡化完成的回调"""
        if result is None:
            QMessageBox.warning(self, "提示", "图像无对比度，无法均衡化")
            return

        # 在主线程中更新UI
        self.image_viewer.load_image(result)
        self.image_viewer.is_equalized = True
        self.statusBar.showMessage("已应用直方图均衡化")
        self.logger.info("已应用直方图均衡化")
        self.update_ui_state()

    def _on_error(self, error_tuple):
        """处理错误"""
        error_type, error_msg, _ = error_tuple
        QMessageBox.critical(self, "错误", f"处理过程中发生错误:\n{str(error_msg)}")
        self.logger.error(f"错误: {str(error_msg)}")

    def connect_mask_signals(self):
        if self.mask_viewer:
            self.mask_viewer.saveRequested.connect(self.save_mask)

    def generate_mask(self):
        """生成多区域掩码并在新窗口中显示"""
        self.logger.info("生成掩码")

        if self.is_drawing:
            QMessageBox.warning(self, "操作限制", "请先关闭绘图模式再生成掩码")
            self.logger.warning("生成掩码失败: 绘图模式处于开启状态")
            return

        if not self.image_viewer.polygon_items:
            QMessageBox.warning(self, "提示", "请先绘制至少一个闭合区域")
            self.logger.warning("生成掩码失败: 没有绘制闭合区域")
            return

        if not self.thread:
            QMessageBox.warning(self, "错误", "图像未正确加载")
            return

        h = self.thread.full_h
        w = self.thread.full_w
        scale = self.thread.scale

        # 获取绘制的多边形
        polygons = self.image_viewer.get_polygons()
        polygons = [[(int(x / scale), int(y / scale)) for x, y in poly] for poly in polygons]

        if not polygons:
            QMessageBox.warning(self, "提示", "没有有效的闭合区域")
            return

        # 显示进度条
        self.progress = QProgressDialog("正在生成掩码...", None, 0, 0, self)
        self.progress.setWindowTitle("处理中")
        self.progress.resize(500, 300)
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.show()
        QApplication.processEvents()

        # 创建工作线程
        worker = Worker(self._do_generate_mask, h, w, polygons)
        worker.signals.result.connect(self._on_mask_generated)
        worker.signals.finished.connect(self.progress.close)
        worker.signals.error.connect(self._on_error)

        self.threadpool.start(worker)

    def _do_generate_mask(self, h, w, polygons):
        """在线程中生成掩码"""
        try:
        # 创建与原图同尺寸的8位掩码
            mask = np.zeros((h, w), dtype=np.uint8)
            mask_value = 255
            total = len(polygons)
            if total == 0:
                return None, 0

            ## 优化多边形填充：使用OpenCV的并行处理能力
            cv2.setNumThreads(self.config_manager.get("image_processing.thread_count", 4))

            # 填充所有区域
            for polygon in polygons:
                if len(polygon) >= 3:  # 至少需要3个点才能形成一个闭合区域
                    cv2.fillPoly(mask, [np.array(polygon, dtype=np.int32)], mask_value)

            # 检查掩码是否有效
            if np.count_nonzero(mask) == 0:
                return None, 0

            # 计算连通区域数量（实际掩码区域数）
            num_labels, _, _, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            effective_regions = num_labels - 1

            return mask, effective_regions

        except Exception as e:
            self.logger.error(f"掩码生成异常: {str(e)}")
            return None, 0

    def _on_mask_generated(self, result):
        """处理掩码生成完成的回调"""
        if result is None or result[0] is None:
            QMessageBox.warning(self, "警告", "生成的掩码不包含任何有效区域")
            return

        mask, effective_regions = result

        # 在新窗口中显示掩码
        if self.mask_viewer is None or not self.mask_viewer.isVisible():
            self.mask_viewer = MaskViewer(self)
            self.connect_mask_signals()

        self.mask_viewer.set_mask(mask)
        self.mask_viewer.show()

        self.statusBar.showMessage(f"生成掩码成功，有效区域数: {effective_regions}")
        self.logger.info(f"生成掩码成功，有效区域数: {effective_regions}")
        self.update_ui_state()

    def save_mask(self):
        """保存掩码为 8位图像 """
        self.logger.info("保存掩码")
        if not self.mask_viewer or self.mask_viewer.get_mask() is None:
            QMessageBox.warning(self, "提示", "没有掩码图像可保存")
            self.logger.warning("保存掩码失败: 没有有效的掩码数据")
            return

        # 获取原始图像文件名
        default_name = ""
        if self.thread and self.thread.image_path:
            original_name = os.path.basename(self.thread.image_path)
            default_name = os.path.splitext(original_name)[0] + "_mask"

        # 设置默认保存路径
        default_dir = os.path.dirname(self.thread.image_path) if self.thread else ""
        default_path = os.path.join(default_dir, default_name)

        # 打开保存对话框
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存掩码",
            default_path,
            "TIF文件 (*.tif);;PNG文件 (*.png)"
        )

        if path:
            # 保存掩码图像
            self.last_saved_mask_path = path
            mask = self.mask_viewer.get_mask()
            # 确保掩码是有效的NumPy数组
            if not isinstance(mask, np.ndarray) or mask.size == 0:
                raise ValueError("掩码数据无效")

            self.progress = QProgressDialog("正在保存掩码文件...", None, 0, 0, self)
            self.progress.setWindowTitle("处理中")
            self.progress.resize(500, 300)
            self.progress.setWindowModality(Qt.WindowModal)
            self.progress.show()

            worker = Worker(self._save_mask_thread, mask, path)
            worker.signals.result.connect(self._on_mask_saved)
            worker.signals.finished.connect(self.progress.close)
            worker.signals.error.connect(self._on_error)
            self.threadpool.start(worker)

    def _save_mask_thread(self, mask, path):
        """线程里保存，不卡界面"""
        cv2.imwrite(path, mask)

        # 保存中心点
        txt_path = os.path.splitext(path)[0] + ".txt"
        self.save_centroid_coordinates(txt_path)
        return path

    def _on_mask_saved(self, path):
        """保存完成回调"""
        self.last_saved_mask_path = path
        self.statusBar.showMessage(f"掩码已保存至: {path}")
        QMessageBox.information(self, "成功", f"掩码已保存！\n{path}")

    def save_centroid_coordinates(self, txt_path):
        """
        计算并保存所有掩膜的中心点坐标，方便查找
        """
        if not self.image_viewer.polygon_items:
            return

        try:
            with open(txt_path, 'w') as f:
                f.write("# 掩膜中心点坐标 (X, Y)\n")
                f.write("# 掩膜编号, X坐标, Y坐标\n")

                scale = self.thread.scale

                for i, polygon in enumerate(self.image_viewer.polygon_items, 1):
                    # 获取多边形点
                    points = polygon.polygon()

                    # 计算多边形中心点
                    x_sum = sum(point.x() for point in points)
                    y_sum = sum(point.y() for point in points)
                    centroid_x = (x_sum / points.size()) / scale
                    centroid_y = (y_sum / points.size()) / scale

                    # 写入文件
                    f.write(f"{i}, {centroid_x:.2f}, {centroid_y:.2f}\n")

            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存中心点坐标时出错: {str(e)}")
            return False

    def jump_to_coordinate(self):
        """根据输入坐标跳转到对应位置"""
        text = self.coord_input.text().strip()
        if not text:
            return

        # 正则匹配坐标格式
        pattern = r'^\s*(\d+\.?\d*)\s*[,， ]\s*(\d+\.?\d*)\s*$'
        match = re.match(pattern, text)
        if not match:
            QMessageBox.warning(self, "格式错误", "请输入有效的坐标格式 (X,Y)")
            return

        try:
            x = float(match.group(1))
            y = float(match.group(2))
        except ValueError:
            QMessageBox.warning(self, "数值错误", "坐标值必须为数字")
            return

        # 检查图像是否存在
        if not self.image_viewer.image_item:
            QMessageBox.warning(self, "提示", "请先加载图像")
            return

        # 计算场景坐标（考虑当前缩放和平移）
        scale = self.thread.scale
        scene_pos = QPointF(x * scale, y * scale)
        view_pos = self.image_viewer.mapFromScene(scene_pos)  # 转换为视图坐标

        # 检查坐标是否在图像范围内
        image_rect = self.image_viewer.image_item.boundingRect()
        if not image_rect.contains(scene_pos):
            QMessageBox.warning(self, "坐标超出范围", "输入的坐标不在当前图像范围内")
            return

        # 滚动到目标位置
        self.image_viewer.horizontalScrollBar().setValue(view_pos.x())
        self.image_viewer.verticalScrollBar().setValue(view_pos.y())

        # 居中显示目标点（可选）
        self.image_viewer.centerOn(scene_pos)

        # 显示十字叉
        self.show_cross_at(scene_pos)

        self.statusBar.showMessage(f"已跳转到坐标: ({x:.2f}, {y:.2f})")
        self.logger.info(f"跳转到坐标: ({x:.2f}, {y:.2f})")

    def show_cross_at(self, pos):
        """在指定位置显示红色十字叉"""
        if self.cross_item:
            for item in self.cross_item:
                self.image_viewer.scene.removeItem(item)
            self.cross_item = None

        # 获取当前缩放因子
        scale_factor = self.image_viewer.scale_factor
        # 创建十字叉图形项，实际绘制大小（与缩放因子成反比）
        base_size = self.config_manager.get("image_processing.cross_size", 20.0)  # 十字叉长度
        wid_size = self.config_manager.get("image_processing.cross_wid_size", 2.5)
        cross_size = base_size / scale_factor if scale_factor != 0 else base_size
        # 创建十字叉图形项
        line_horizontal = QGraphicsLineItem(
            pos.x() - cross_size, pos.y(),
            pos.x() + cross_size, pos.y()
        )
        line_vertical = QGraphicsLineItem(
            pos.x(), pos.y() - cross_size,
            pos.x(), pos.y() + cross_size
        )

        # 设置样式
        pen = QPen(QColor(255, 0, 0), wid_size / scale_factor)  # 红色线条，宽度2.5px
        # pen.setCosmetic(True)
        line_horizontal.setPen(pen)
        line_vertical.setPen(pen)
        line_horizontal.setZValue(100)  # 置于顶层
        line_vertical.setZValue(100)

        # 添加到场景
        self.image_viewer.scene.addItem(line_horizontal)
        self.image_viewer.scene.addItem(line_vertical)
        self.cross_item = (line_horizontal, line_vertical)

        # 启动定时器
        self.cross_timer.stop()
        self.cross_timer.setSingleShot(True)
        self.cross_timer.start(5000)      # 5秒后隐藏

    def adjust_cross_position(self):
        """根据当前视图变换调整十字叉位置，确保其固定在原始坐标上"""
        if not self.cross_item or not hasattr(self, 'cross_center'):
            return

        # 计算十字叉的原始中心点
        center_x, center_y = self.cross_center

        # 基础十字叉大小
        base_size = self.config_manager.get("image_processing.cross_size")

        # 更新十字叉位置（使用逆变换确保固定在原始坐标）
        for item in self.cross_item:
            if isinstance(item, QGraphicsLineItem):
                # 水平线条
                if item.line().dy() == 0:
                    item.setLine(
                        center_x - base_size, center_y,
                        center_x + base_size, center_y
                    )
                # 垂直线条
                else:
                    item.setLine(
                        center_x, center_y - base_size,
                        center_x, center_y + base_size
                    )

    def hide_cross(self):
        """隐藏十字叉"""
        # 检查图像是否存在，如果不存在则不执行隐藏操作
        if self.image_viewer.image is None:
            return

        if self.cross_item:
            for item in self.cross_item:
                if item.scene() == self.image_viewer.scene:  # 确保项目在场景中
                    try:
                        self.image_viewer.scene.removeItem(item)
                    except RuntimeError:
                        pass
            self.cross_item = None
        self.cross_timer.stop()

    def trigger_region_replacement(self):
        """按钮触发的图像替换功能"""
        self.logger.info(f"开始替换操作...")
        if not self.last_saved_mask_path:
            QMessageBox.warning(self, "提示", "请先保存掩码")
            return

        # 创建进度对话框
        self.progress = QProgressDialog("正在处理图像替换...", None, 0, 0, self)
        self.progress.setWindowTitle("处理中")
        self.progress.resize(500, 300)
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.show()

        # 创建工作线程
        worker = Worker(self._do_region_replacement)
        worker.signals.result.connect(self._on_region_replacement_done)
        worker.signals.finished.connect(self._on_replace_finished)
        worker.signals.error.connect(self._on_replace_error)

        # 提交到线程池
        self.threadpool.start(worker)

    def _on_replace_finished(self):
        """替换正常结束"""
        self.replace_btn.setEnabled(True)
        if self.progress:
            self.progress.close()
        gc.collect()

    def _on_replace_error(self, err):
        """替换出错处理"""
        self.replace_btn.setEnabled(True)
        if self.progress:
            self.progress.close()
        etype, msg, _ = err
        QMessageBox.critical(self, "错误", f"替换失败：{msg}")
        self.logger.error(f"替换错误: {msg.replace(chr(10), '')}")
        gc.collect()

    def _do_region_replacement(self):
        """在线程中执行图像替换"""
        start_time = time.time()

        # 获取文件路径
        original_path = self.image_replacer.get_original_image_path(self.last_saved_mask_path)
        target_path = self.image_replacer.get_target_image_path(self.last_saved_mask_path)
        result_path = self.image_replacer.get_result_image_path(self.last_saved_mask_path)

        # 检查文件是否存在
        for path in [original_path, target_path]:
            if not os.path.exists(path):
                raise FileNotFoundError(f"文件未找到: {os.path.basename(path)}")

        # 执行替换
        self.image_replacer.replace_region_python(
            original_path,
            self.last_saved_mask_path,
            target_path,
            result_path
        )

        gc.collect()
        # 计算耗时
        elapsed_time = time.time() - start_time
        return result_path, elapsed_time

    def _on_region_replacement_done(self, result):
        """处理图像替换完成的回调"""
        result_path, elapsed_time = result
        elapsed_str = f"{elapsed_time:.2f}秒"
        self.logger.info(f"替换完成！耗时: {elapsed_str}, 加载结果图像: {result_path}")

        self.clear_current_image()
        # 加载结果图像（在主线程中）
        QTimer.singleShot(100, lambda: self.load_image(result_path))

        # 显示成功消息和耗时
        QMessageBox.information(
            self,
            "成功",
            f"图像替换完成并已加载:\n{result_path}\n\n耗时: {elapsed_str}"
        )

    def show_about(self):
        """显示关于对话框"""
        QMessageBox.about(self, "关于",
                          "卫星图像掩码生成工具\n\n版本: 2.0\n\n一款用于卫星图像处理的掩码生成工具，支持绘制多区域掩码，支持大图像加载。")

    def closeEvent(self, event):
        """处理窗口关闭事件"""
        try:
            # 安全停止所有活跃线程
            self.safe_stop_thread()

            # 关闭掩码查看器（如果已打开）
            if self.mask_viewer and self.mask_viewer.isVisible():
                self.mask_viewer.close()

            # 强制清理图像数据（关键！防止退出崩溃）
            if hasattr(self.image_viewer, "image"):
                self.image_viewer.image = None
            if hasattr(self, "thread"):
                self.thread = None

            # 清理场景
            self.image_viewer.clear_scene()
            gc.collect()

            # 记录软件运行时长
            end_time = datetime.datetime.now()
            run_time = end_time - self.start_time
            self.logger.info(f"软件退出: {end_time.strftime('%Y-%m-%d %H:%M:%S')}, 运行时长: {str(run_time)}")

            # 关闭日志处理器
            for handler in self.logger.handlers:
                handler.close()
                self.logger.removeHandler(handler)

            event.accept()

        except Exception as e:
            self.logger.error(f"关闭时异常: {str(e)}")
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
