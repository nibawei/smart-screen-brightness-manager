from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout, QGraphicsDropShadowEffect
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QPoint
from PyQt5.QtGui import QMouseEvent, QColor, QCursor, QGuiApplication
import time
import sys
import threading
import os

# 确保只有一个QApplication实例
_app = None
_app_lock = threading.Lock()

# 检查并获取QApplication实例
def get_app():
    global _app
    with _app_lock:
        if _app is None:
            # 尝试获取已存在的QApplication实例
            _app = QApplication.instance()
            if _app is None:
                # 在独立线程中创建QApplication，避免与Tkinter冲突
                # 使用sys.argv的副本，避免参数冲突
                fake_argv = [sys.argv[0] if sys.argv else 'notification']
                _app = QApplication(fake_argv)
                # 设置为不退出，即使所有窗口关闭
                _app.setQuitOnLastWindowClosed(False)
    return _app

def ensure_qapplication_thread():
    """确保 QApplication 在正确的线程中运行"""
    app = get_app()
    # 检查是否在主线程中
    if threading.current_thread() is threading.main_thread():
        # 在主线程中，可以安全处理事件
        app.processEvents()
    else:
        # 在非主线程中，使用定时器异步处理
        QTimer.singleShot(0, app.processEvents)

def get_available_screens():
    """获取所有可用的显示器列表
    
    Returns:
        list: 显示器信息列表，每个元素为字典，包含 'index' 和 'name'
    """
    screens = []
    try:
        q_app = get_app()
        screen_list = q_app.screens()
        for i, screen in enumerate(screen_list):
            # 获取显示器名称
            name = screen.name() if hasattr(screen, 'name') else f"显示器 {i+1}"
            # 获取显示器分辨率
            geom = screen.geometry()
            resolution = f"{geom.width()}x{geom.height()}"
            
            screens.append({
                'index': i,
                'name': f"{name} ({resolution})",
                'is_primary': screen == q_app.primaryScreen()
            })
    except Exception as e:
        print(f"获取显示器列表失败：{e}")
        # 如果失败，至少添加一个默认显示器
        screens.append({
            'index': 0,
            'name': '默认显示器',
            'is_primary': True
        })
    
    return screens


def get_screen_from_widget(widget):
    """根据窗口部件获取所在的屏幕
    
    Args:
        widget: PyQt5 窗口部件
        
    Returns:
        screen: 所在的屏幕对象，如果无法获取则返回主屏幕
        screen_index: 屏幕索引
    """
    try:
        q_app = get_app()
        screens = q_app.screens()
        
        # 尝试从窗口获取屏幕
        if widget is not None:
            screen = widget.screen()
            if screen and screen in screens:
                screen_index = screens.index(screen)
                return screen, screen_index
        
        # 如果无法获取，返回主屏幕
        return q_app.primaryScreen(), 0
    except Exception as e:
        print(f"获取窗口所在屏幕失败：{e}")
        return QApplication.primaryScreen(), 0


def get_screen_from_cursor():
    """根据鼠标光标位置获取所在的屏幕
    
    Returns:
        screen: 光标所在的屏幕对象，如果无法获取则返回主屏幕
        screen_index: 屏幕索引
    """
    try:
        q_app = get_app()
        screens = q_app.screens()
        
        # 获取光标位置
        cursor_pos = QCursor.pos()
        
        # 查找光标所在的屏幕
        for i, screen in enumerate(screens):
            geom = screen.geometry()
            if geom.contains(cursor_pos):
                return screen, i
        
        # 如果没有找到，返回主屏幕
        return q_app.primaryScreen(), 0
    except Exception as e:
        print(f"获取光标所在屏幕失败：{e}")
        return QApplication.primaryScreen(), 0


def get_all_screens_indices():
    """获取所有屏幕的索引列表
    
    Returns:
        list: 所有屏幕的索引列表
    """
    try:
        q_app = get_app()
        return list(range(len(q_app.screens())))
    except Exception as e:
        print(f"获取所有屏幕索引失败：{e}")
        return [0]

class Notification(QWidget):
    # 类变量，记录所有通知实例
    notifications = []
    
    # 不同级别的颜色配置
    LEVEL_COLORS = {
        'info': {
            'bg': 'rgba(30, 144, 255, 220)',  # 蓝色
            'text': 'white'
        },
        'success': {
            'bg': 'rgba(34, 197, 94, 220)',   # 绿色
            'text': 'white'
        },
        'warning': {
            'bg': 'rgba(251, 191, 36, 220)',  # 橙色
            'text': 'black'
        },
        'error': {
            'bg': 'rgba(239, 68, 68, 220)',   # 红色
            'text': 'white'
        }
    }
    
    def __init__(self, message, position='top', screen_index=None, screen_mode='primary', parent=None, level='info'):
        """
        初始化通知
        
        Args:
            message: 通知消息内容
            position: 通知位置，'top' 或 'bottom'
            screen_index: 指定显示器索引（可选）
            screen_mode: 屏幕选择模式
                - 'primary': 主屏幕（默认）
                - 'widget': 根据父窗口所在屏幕
                - 'cursor': 根据鼠标光标所在屏幕
                - 'all': 所有屏幕
                - 'custom': 使用 screen_index 指定的屏幕
            parent: 父窗口
            level: 通知级别，'info'/'success'/'warning'/'error'
        """
        try:
            # 确保 QApplication 实例存在
            self.q_app = get_app()
            super().__init__(parent)
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground)
            
            # 根据 screen_mode 确定要显示的屏幕
            screens = QApplication.screens()
            
            if screen_mode == 'primary':
                # 主屏幕
                screen = QApplication.primaryScreen()
                screen_index = screens.index(screen) if screen in screens else 0
            elif screen_mode == 'widget':
                # 根据父窗口所在屏幕
                screen, screen_index = get_screen_from_widget(parent)
            elif screen_mode == 'cursor':
                # 根据鼠标光标所在屏幕
                screen, screen_index = get_screen_from_cursor()
            elif screen_mode == 'custom' and screen_index is not None:
                # 使用指定的屏幕索引
                if 0 <= screen_index < len(screens):
                    screen = screens[screen_index]
                else:
                    screen = QApplication.primaryScreen()
                    screen_index = 0
            else:
                # 默认使用主屏幕
                screen = QApplication.primaryScreen()
                screen_index = 0
            
            self.screen = screen  # 保存屏幕引用
            self.screen_index = screen_index  # 保存屏幕索引
            self.screen_mode = screen_mode  # 保存屏幕模式
            
            # 进一步简化样式
            # 获取屏幕DPI缩放比例
            dpi_scale = screen.logicalDotsPerInch() / 96.0
            
            # 获取级别颜色
            colors = self.LEVEL_COLORS.get(level, self.LEVEL_COLORS['info'])
            
            # 更醒目的样式
            self.label = QLabel(message)
            self.label.setAlignment(Qt.AlignCenter)
            self.label.setStyleSheet(f"""
                background-color: {colors['bg']};
                color: {colors['text']};
                padding: {int(6 * dpi_scale)}px {int(16 * dpi_scale)}px;
                font-size: {int(16 * dpi_scale)}px;
                border-radius: {int(6 * dpi_scale)}px;
                font-weight: 600;
                border: none;
            """)
            
            # 添加更明显的阴影效果
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(15 * dpi_scale)
            shadow.setColor(QColor(0, 0, 0, 150))
            shadow.setOffset(0, 0)
            self.label.setGraphicsEffect(shadow)
            
            layout = QVBoxLayout()
            layout.addWidget(self.label)
            layout.setContentsMargins(0, 0, 0, 0)
            self.setLayout(layout)
            
            # 确保窗口大小最小化
            self.adjustSize()
            self.setMinimumSize(1, 1)
            
            self.setFixedSize(self.size())
            
            # 使用指定屏幕的几何信息
            screen_geom = screen.geometry()
            if position == 'top':
                # 顶部居中：使用屏幕的全局坐标
                self.move(screen_geom.center().x() - self.width()//2, screen_geom.y() + 20)
            else:
                # 右下角：使用屏幕的全局坐标
                self.move(screen_geom.x() + screen_geom.width() - self.width() - 20, 
                         screen_geom.y() + screen_geom.height() - self.height() - 120)
            
            # 确保窗口已经显示并更新几何信息
            self.show()
            self.q_app.processEvents()
            
            # 确保定时器正常工作
            self.timer = QTimer(self)  # 设置parent
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self.close_notification)
            self.timer.start(3000)  # 3秒后自动关闭
            
            # 添加淡入动画
            self.fade_in = QPropertyAnimation(self, b"windowOpacity")
            self.fade_in.setDuration(300)
            self.fade_in.setStartValue(0)
            self.fade_in.setEndValue(1)
            self.fade_in.start()
            
            # 添加到通知列表
            Notification.notifications.append(self)
            self.position = position
            self.update_positions()
        except Exception as e:
            # 如果PyQt5通知失败，降级为控制台输出
            print(f"通知: {message}")
            try:
                Notification.notifications.remove(self)
            except:
                pass
    
    def start(self):
        """启动通知"""
        # Qt通知已经在初始化时自动显示，不需要额外的事件循环
        pass
    
    def close_notification(self):
        # 如果已经在关闭过程中，不再重复执行
        if hasattr(self, '_is_closing') and self._is_closing:
            return
            
        self._is_closing = True
        # 停止自动关闭计时器
        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
        
        # 添加淡出动画
        self.fade_out = QPropertyAnimation(self, b"windowOpacity")
        self.fade_out.setDuration(300)
        self.fade_out.setStartValue(1)
        self.fade_out.setEndValue(0)
        self.fade_out.finished.connect(self._on_close_complete)
        self.fade_out.start()
    
    def _on_close_complete(self):
        # 从通知列表移除
        if self in Notification.notifications:
            Notification.notifications.remove(self)
        self.close()
        # 更新其他通知位置
        self.update_positions()
    
    def update_positions(self):
        # 按屏幕分组更新通知位置
        # 获取所有屏幕
        screens = QApplication.screens()
        
        # 按屏幕索引和位置分组通知
        screen_notifications = {}
        for notif in Notification.notifications:
            screen_idx = getattr(notif, 'screen_index', None)
            if screen_idx is None:
                screen_idx = 0  # 主屏幕
            
            if screen_idx not in screen_notifications:
                screen_notifications[screen_idx] = {'top': [], 'bottom': []}
            
            pos = getattr(notif, 'position', 'top')
            screen_notifications[screen_idx][pos].append(notif)
        
        # 为每个屏幕更新通知位置
        for screen_idx, positions in screen_notifications.items():
            # 获取屏幕
            if 0 <= screen_idx < len(screens):
                screen = screens[screen_idx]
            else:
                screen = QApplication.primaryScreen()
            
            screen_geom = screen.geometry()
            
            # 更新顶部通知位置
            for i, notif in enumerate(positions['top']):
                notif.move(screen_geom.center().x() - notif.width()//2, 
                          screen_geom.y() + 20 + i * (notif.height() + 10))
            
            # 更新底部通知位置
            for i, notif in enumerate(positions['bottom']):
                notif.move(screen_geom.x() + screen_geom.width() - notif.width() - 20,
                          screen_geom.y() + screen_geom.height() - notif.height() - 120 - i * (notif.height() + 10))
    
    def mousePressEvent(self, event: QMouseEvent):
        # 点击任意位置都可以关闭通知
        self.close_notification()
        event.accept()


def show_notification(message, position='top', screen_mode='primary', screen_index=None, level='info'):
    """
    便捷的通知显示函数
    
    Args:
        message: 通知消息
        position: 通知位置，'top' 或 'bottom'
        screen_mode: 屏幕选择模式
            - 'primary': 主屏幕（默认）
            - 'cursor': 根据鼠标光标所在屏幕
            - 'all': 所有屏幕
            - 'custom': 使用 screen_index 指定的屏幕
        screen_index: 自定义屏幕索引（仅在 screen_mode='custom' 时使用）
        level: 通知级别，'info'/'success'/'warning'/'error'
        
    Returns:
        list: 创建的通知实例列表
    """
    notifications = []
    
    if screen_mode == 'all':
        # 在所有屏幕上显示通知
        screen_indices = get_all_screens_indices()
        for idx in screen_indices:
            notif = Notification(
                message=message,
                position=position,
                screen_index=idx,
                screen_mode='custom',
                level=level
            )
            notifications.append(notif)
    else:
        # 在单个屏幕上显示通知
        notif = Notification(
            message=message,
            position=position,
            screen_index=screen_index,
            screen_mode=screen_mode,
            level=level
        )
        notifications.append(notif)
    
    return notifications


def show_startup_notification(message="系统启动", level='info'):
    """
    显示启动通知（在主屏幕上显示）
    
    Args:
        message: 通知消息
        level: 通知级别
    """
    return show_notification(message, position='top', screen_mode='primary', level=level)


def show_window_notification(message, position='top', level='info'):
    """
    显示窗口操作通知（在窗口所在屏幕上显示）
    
    Args:
        message: 通知消息
        position: 通知位置
        level: 通知级别
    """
    return show_notification(
        message, 
        position=position, 
        screen_mode='widget', 
        level=level
    )


def show_cursor_notification(message, position='top', level='info'):
    """
    显示鼠标位置通知（在鼠标所在屏幕上显示）
    
    Args:
        message: 通知消息
        position: 通知位置
        level: 通知级别
    """
    return show_notification(message, position=position, screen_mode='cursor', level=level)


def show_all_screens_notification(message, position='top', level='info'):
    """
    在所有屏幕上显示通知
    
    Args:
        message: 通知消息
        position: 通知位置
        level: 通知级别
    """
    return show_notification(message, position=position, screen_mode='all', level=level)

if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    
    # 获取所有可用显示器
    screens = get_available_screens()
    print(f"检测到 {len(screens)} 个显示器:")
    for screen in screens:
        primary_mark = " (主显示器)" if screen['is_primary'] else ""
        print(f"  - 索引 {screen['index']}: {screen['name']}{primary_mark}")
    
    # 测试启动通知（主屏幕）
    print("\n=== 测试启动通知（主屏幕）===")
    show_startup_notification("系统启动完成", level='success')
    time.sleep(2)
    
    # 测试窗口通知（根据父窗口所在屏幕）
    print("\n=== 测试窗口通知 ===")
    test_widget = QWidget()
    test_widget.move(100, 100)
    test_widget.resize(400, 300)
    # 不显示测试窗口，仅用于获取屏幕信息
    show_window_notification("窗口操作通知", parent=test_widget, level='info')
    time.sleep(2)
    
    # 测试鼠标位置通知
    print("\n=== 测试鼠标位置通知 ===")
    show_cursor_notification("鼠标位置通知", level='warning')
    time.sleep(2)
    
    # 测试所有屏幕通知
    print("\n=== 测试所有屏幕通知 ===")
    show_all_screens_notification("所有屏幕通知", level='error')
    time.sleep(2)
    
    # 测试自定义屏幕通知
    print("\n=== 测试自定义屏幕通知 ===")
    if len(screens) > 1:
        show_notification("第二屏幕自定义通知", screen_mode='custom', screen_index=1, level='info')
    else:
        print("只有一个屏幕，跳过自定义屏幕测试")
    
    time.sleep(5)
    
    # 关闭测试窗口并退出
    test_widget.close()
    QTimer.singleShot(10000, app.quit)
    sys.exit(app.exec_())
    
    
