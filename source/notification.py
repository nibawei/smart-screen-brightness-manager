from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout, QGraphicsDropShadowEffect
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QPoint
from PyQt5.QtGui import QMouseEvent, QColor
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
    """确保QApplication在正确的线程中运行"""
    app = get_app()
    # 检查是否在主线程中
    if threading.current_thread() is threading.main_thread():
        # 在主线程中，可以安全处理事件
        app.processEvents()
    else:
        # 在非主线程中，使用定时器异步处理
        QTimer.singleShot(0, app.processEvents)

class Notification(QWidget):
    # 类变量，记录所有通知实例
    notifications = []
    
    def __init__(self, message, position='top', parent=None):
        try:
            # 确保QApplication实例存在
            self.q_app = get_app()
            super().__init__(parent)
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground)
            
            # 进一步简化样式
            # 获取屏幕DPI缩放比例
            screen = QApplication.primaryScreen()
            dpi_scale = screen.logicalDotsPerInch() / 96.0
            
            # 更醒目的样式
            self.label = QLabel(message)
            self.label.setAlignment(Qt.AlignCenter)
            self.label.setStyleSheet(f"""
                background-color: rgba(30, 144, 255, 220);
                color: white;
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
            
            screen = QApplication.primaryScreen().geometry()
            if position == 'top':
                self.move(screen.center().x() - self.width()//2, 20)
            else:
                # 修正右下角位置计算
                self.move(screen.width() - self.width() - 20, 
                         screen.height() - self.height() - 120)  # 从原来的-20改为-60
            
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
        screen = QApplication.primaryScreen().geometry()
        top_notifications = [n for n in Notification.notifications 
                            if n.position == 'top']
        bottom_notifications = [n for n in Notification.notifications 
                              if n.position == 'bottom']
        
        # 更新顶部通知位置
        for i, notif in enumerate(top_notifications):
            notif.move(screen.center().x() - notif.width()//2, 
                      20 + i * (notif.height() + 10))
        
        # 更新底部通知位置
        for i, notif in enumerate(bottom_notifications):
            notif.move(screen.width() - notif.width() - 20,
                      screen.height() - notif.height() - 120 - i * (notif.height() + 10))
    
    def mousePressEvent(self, event: QMouseEvent):
        # 点击任意位置都可以关闭通知
        self.close_notification()
        event.accept()

if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    
    # 测试右下角通知
    notif = Notification("右下角通知测试1", position='bottom')
    notif.show()
    time.sleep(1)

    notif = Notification("右下角通知测试2", position='bottom')
    notif.show()
    time.sleep(1)
    
    # 测试顶部通知
    notif_top = Notification("顶部通知测试1", position='top')
    notif_top.show()
    time.sleep(1)

    notif_top2 = Notification("顶部通知测试2", position='top')
    notif_top2.show()
    time.sleep(1)
    notif_top2.close_notification()
    sys.exit(app.exec_())
    
    
