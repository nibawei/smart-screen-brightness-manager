import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import pystray
import numpy as np
import cv2
import ctypes
import win32event
import win32api
import winerror
import win32gui
import win32con
import win32process
import wmi
import pythoncom
from PyQt5.QtCore import Qt

# 获取可执行文件路径
def get_executable_path():
    if getattr(sys, 'frozen', False):
        # 打包后的可执行文件路径
        return os.path.dirname(sys.executable)
    else:
        # 开发环境中的脚本路径
        return os.path.dirname(os.path.abspath(__file__))

# 获取资源文件路径（用于加载打包后的资源）
def get_resource_path(resource_name):
    """获取资源文件的正确路径，支持开发和打包环境
    
    Args:
        resource_name: 资源文件名（如 'favicon.ico'）
        
    Returns:
        str: 资源文件的完整路径
    """
    if getattr(sys, 'frozen', False):
        # 打包后，资源会被提取到_MEIPASS目录
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS, resource_name)
        else:
            # 如果没有_MEIPASS，使用exe目录
            return os.path.join(get_executable_path(), resource_name)
    else:
        # 开发环境中，直接使用当前目录
        return os.path.join(get_executable_path(), resource_name)

def is_monitor_on():
    """
    Check if the monitor is currently on by querying the power state
    Returns True if monitor is on, False if it's off/sleeping
    """
    com_initialized = False
    result = True
    try:
        # 初始化COM（每次调用都需要初始化，因为可能在不同的线程中）
        pythoncom.CoInitialize()
        com_initialized = True
        
        # Method 1: Try using WMI to check monitor status
        wmi_obj = wmi.WMI(namespace='root\\wmi')
        # Query the monitor power state
        monitor_states = wmi_obj.WmiMonitorBasicDisplayParams()
        for state in monitor_states:
            # Power state: 1=ON, 2=STANDBY, 3=SUSPEND, 4=OFF
            if hasattr(state, 'PowerState'):
                result = state.PowerState == 1
                break
    except wmi.x_wmi:
        # WMI相关异常，保持默认结果True
        pass
    except pythoncom.com_error:
        # COM相关异常，保持默认结果True
        pass
    except Exception as e:
        # Method 2: Alternative approach using Windows API
        # We can try to send a message to check if the monitor responds
        # However, since we can't reliably detect if monitor is off via API,
        # we'll return True as a fallback to prevent disabling brightness adjustments
        pass
    finally:
        # 清理COM（确保总是被调用，且只在成功初始化后清理）
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
            except pythoncom.com_error:
                pass
    
    return result

from liangdu import (BrightnessNeuralNetwork, capture_frames_from_camera, calculate_average_brightness,
                     adjust_brightness, generate_synthetic_data, get_available_cameras, get_available_monitors,
                     list_brightness_capabilities, set_brightness_targets, restore_gamma_ramps,
                     TARGET_VERSION)
import devices
import models
from config import config_manager, get_user_data_path
from utils import SystemUtils, ToastNotifier
from notification import (Notification, get_app, show_notification, show_startup_notification,
                          show_window_notification, show_cursor_notification, show_all_screens_notification)

# 全局资源变量，确保整个程序运行期间保持打开
_global_mutex = None
_instance_lock_file = None


def _normalize_display_name(name):
    """统一显示设备名：`\\.\DISPLAY2` / `DISPLAY2` 视为同一个。"""
    text = str(name or "").strip().upper()
    if text.startswith("\\\\.\\"):
        text = text[4:]
    return text

class BrightnessManagerApp:
    """亮度管理器应用类"""
    
    def __init__(self):
        """初始化应用"""
        # 创建主窗口
        self.root = tk.Tk()
        self.root.title("亮度管理器")
        self.root.geometry("700x1000")
        self.root.resizable(True, True)
        
        # 设置窗口图标
        try:
            icon_path = get_resource_path("favicon.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception as e:
            # 忽略图标加载错误，确保程序能正常运行
            print(f"加载窗口图标失败: {e}")
        
        # 绑定窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)
        
        # 初始化变量
        self.data_root = get_user_data_path()
        self.devices_registry_path = os.path.join(self.data_root, 'devices.json')
        self.legacy_samples_path = os.path.join(self.data_root, 'training_samples.npz')
        self.devices_registry = devices.load_registry(self.devices_registry_path)

        # 每台摄像头一个相机模型，每块屏幕一条曲线（按需加载并缓存）
        self.camera_models = {}
        self.screen_curves = {}
        # screen_id -> {'brightness', 'participates', 'supported', 'backend', 'manual_set'}
        self.screen_states = {}
        self.active_screen_id = None
        # 最近一次采集：{'features': [...], 'camera_id': ...}
        self.pending_capture = None

        # 兼容旧字段：nn 指向活动屏幕对应的相机模型
        self.nn = None
        self.current_brightness = 50
        self.brightness_data = []  # 仅用于日志/调试
        self.auto_brightness_enabled = config_manager.get('auto_brightness', True)  # 自动亮度调整开关，默认开启
        self.check_interval = config_manager.get('check_interval', 300)  # 循环检查间隔（秒）
        # 获取实际的开机自启动状态
        self.startup_enabled = SystemUtils.get_startup()
        # 更新配置文件
        config_manager.set('startup', self.startup_enabled)
        
        # 摄像头和显示器选择相关变量
        self.available_cameras = []  # 可用摄像头列表
        self.available_monitors = []  # 可用显示器列表
        
        # 热插拔防抖：记录上次刷新时间，避免频繁刷新
        self.last_hotplug_refresh_time = 0
        self.hotplug_debounce_interval = 3  # 防抖间隔（秒）
        
        # 初始化系统托盘
        self.tray = None
        
        # 初始化定时任务
        self.auto_adjust_timer = None
        
        # 模型加载状态
        self.model_loading = False  # 模型是否正在加载
        self.model_loaded = False  # 模型是否加载完成
        
        # 按钮点击频率限制
        self.button_click_locks = {}  # 按钮点击锁，记录每个按钮的最后点击时间
        self.button_click_interval = 2.0  # 按钮点击最小间隔（秒）
        
        # 线程安全锁
        self.nn_lock = threading.Lock()  # 神经网络模型访问锁
        self.data_lock = threading.Lock()  # 亮度数据访问锁
        self.training_lock = threading.Lock()  # 同一时间只允许一个训练任务
        self.camera_lock = threading.Lock()  # 摄像头列表访问锁
        self.monitor_lock = threading.Lock()  # 显示器列表访问锁
        self.status_lock = threading.Lock()  # 状态标志访问锁
        
        # 创建GUI
        self.create_gui()
        
        # 在后台线程中异步初始化神经网络
        self.init_neural_network_async()
        
        # 启动系统托盘
        self.setup_tray()
        
        # 隐藏主窗口，只显示系统托盘
        self.root.withdraw()
        
        # 显示启动通知
        show_startup_notification("亮度管理器已启动", level='success')
        
        # 启动显示器热插拔检测
        self.start_hotplug_detection()
        
        # 注意：自动调整定时任务将在模型加载完成后启动
    
    def create_gui(self):
        """创建GUI界面"""
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建信息展示文本框
        info_frame = ttk.LabelFrame(main_frame, text="系统状态", padding="10")
        info_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 设置文本框字体大小
        self.info_text = tk.Text(info_frame, height=10, wrap=tk.WORD, font=("Microsoft YaHei", 11))
        self.info_text.pack(fill=tk.BOTH, expand=True)
        self.info_text.config(state=tk.DISABLED)
        
        # 创建亮度控制框架
        brightness_frame = ttk.LabelFrame(main_frame, text="亮度控制", padding="10")
        brightness_frame.pack(fill=tk.X, pady=5)
        
        # 当前屏幕选择（手动调节只作用于这一块屏）
        screen_row = ttk.Frame(brightness_frame)
        screen_row.pack(fill=tk.X, pady=2)
        ttk.Label(screen_row, text="当前屏幕:").pack(side=tk.LEFT, padx=5)
        self.screen_var = tk.StringVar()
        self.screen_combo = ttk.Combobox(
            screen_row,
            textvariable=self.screen_var,
            state="readonly",
            width=32
        )
        self.screen_combo.pack(side=tk.LEFT, padx=5)
        self.screen_combo.bind("<<ComboboxSelected>>", self.on_screen_change)
        
        # 当前屏幕的支持状态（WMI / DDC / 不支持）
        self.screen_status_label = ttk.Label(brightness_frame, text="", foreground="gray")
        self.screen_status_label.pack(anchor=tk.W, padx=5)
        
        # 创建亮度滑动条
        self.brightness_var = tk.IntVar(value=self.current_brightness)
        brightness_scale = ttk.Scale(
            brightness_frame, 
            from_=0, 
            to=100, 
            orient=tk.HORIZONTAL, 
            variable=self.brightness_var,
            command=self.on_brightness_change
        )
        brightness_scale.pack(fill=tk.X, pady=5)
        
        # 创建亮度值标签
        self.brightness_label = ttk.Label(brightness_frame, text=f"亮度: {self.current_brightness}%")
        self.brightness_label.pack()
        
        # 创建按钮框架
        button_frame = ttk.Frame(brightness_frame)
        button_frame.pack(fill=tk.X, pady=5)

        # 创建获取亮度按钮
        get_brightness_button = ttk.Button(button_frame, text="获取亮度", command=self.on_get_brightness_click)
        get_brightness_button.pack(side=tk.LEFT, padx=5)

        # 创建应用按钮（只写当前屏，不生成样本）
        apply_button = ttk.Button(button_frame, text="应用", command=self.apply_brightness)
        apply_button.pack(side=tk.LEFT, padx=5)

        # 训练当前屏：记当前屏一条样本并拟合它的曲线
        train_current_button = ttk.Button(button_frame, text="训练当前屏", command=self.train_current_screen)
        train_current_button.pack(side=tk.LEFT, padx=5)

        # 训练全部勾选屏：每块参与自动调节的屏各记一条，再拟合
        train_all_button = ttk.Button(button_frame, text="训练全部勾选屏", command=self.train_all_screens)
        train_all_button.pack(side=tk.LEFT, padx=5)
        
        # 创建模型加载状态标签
        self.model_status_label = ttk.Label(
            brightness_frame, 
            text="模型状态: 加载中...", 
            foreground="orange"
        )
        self.model_status_label.pack(pady=5)
        
        # 创建设置框架
        settings_frame = ttk.LabelFrame(main_frame, text="设置", padding="10")
        settings_frame.pack(fill=tk.X, pady=5)
        
        # 创建开机自启动勾选框
        self.startup_var = tk.BooleanVar(value=self.startup_enabled)
        startup_check = ttk.Checkbutton(
            settings_frame, 
            text="开机自启动", 
            variable=self.startup_var,
            command=self.on_startup_change
        )
        startup_check.pack(anchor=tk.W)
        
        # 创建循环检查间隔框架
        interval_frame = ttk.Frame(settings_frame)
        interval_frame.pack(fill=tk.X, pady=5)
        
        # 创建间隔标签
        interval_label = ttk.Label(interval_frame, text="循环检查间隔:")
        interval_label.pack(side=tk.LEFT, padx=5)
        
        # 定义间隔选项（秒数，显示文本）
        self.interval_options = [
            (60, "1分钟"),
            (120, "2分钟"),
            (300, "5分钟"),
            (600, "10分钟"),
            (900, "15分钟"),
            (1800, "30分钟"),
            (3600, "1小时"),
            (7200, "2小时"),
            (14400, "4小时"),
            (28800, "8小时"),
            (43200, "12小时"),
            (86400, "24小时")
        ]
        
        # 创建间隔下拉框
        self.interval_var = tk.StringVar()
        interval_combo = ttk.Combobox(
            interval_frame,
            textvariable=self.interval_var,
            values=[text for _, text in self.interval_options],
            state="readonly",
            width=10
        )
        interval_combo.pack(side=tk.LEFT, padx=5)
        
        # 设置当前值
        current_interval_text = "5分钟"
        for seconds, text in self.interval_options:
            if seconds == self.check_interval:
                current_interval_text = text
                break
        self.interval_var.set(current_interval_text)
        
        # 绑定选择事件
        interval_combo.bind("<<ComboboxSelected>>", self.on_interval_change)
        
        # 创建摄像头选择框架
        camera_frame = ttk.Frame(settings_frame)
        camera_frame.pack(fill=tk.X, pady=5)
        
        # 创建摄像头选择标签
        camera_label = ttk.Label(camera_frame, text="摄像头选择:")
        camera_label.pack(side=tk.LEFT, padx=5)
        
        # 创建摄像头下拉框（单选）
        self.camera_var = tk.StringVar()
        self.camera_combo = ttk.Combobox(
            camera_frame,
            textvariable=self.camera_var,
            state="readonly",
            width=20
        )
        self.camera_combo.pack(side=tk.LEFT, padx=5)
        
        # 绑定摄像头选择变化事件
        self.camera_combo.bind("<<ComboboxSelected>>", self.on_camera_change)
        
        # 创建刷新摄像头列表按钮
        refresh_camera_button = ttk.Button(
            camera_frame,
            text="刷新",
            command=self.on_refresh_cameras
        )
        refresh_camera_button.pack(side=tk.LEFT, padx=5)
        
        # 初始化摄像头列表
        self.refresh_camera_list()
        
        # 创建设备选择框架
        monitor_frame = ttk.Frame(settings_frame)
        monitor_frame.pack(fill=tk.X, pady=5)
        
        # 创建显示器选择标签
        monitor_label = ttk.Label(monitor_frame, text="显示器选择:")
        monitor_label.pack(side=tk.LEFT, padx=5)
        
        # 创建显示器复选框框架
        self.monitor_checkboxes_frame = ttk.Frame(monitor_frame)
        self.monitor_checkboxes_frame.pack(side=tk.LEFT, padx=5)
        
        # 存储显示器复选框变量
        self.monitor_vars = {}
        
        # 创建刷新显示器列表按钮
        refresh_monitor_button = ttk.Button(
            monitor_frame,
            text="刷新",
            command=self.on_refresh_monitors
        )
        refresh_monitor_button.pack(side=tk.LEFT, padx=5)
        
        # 初始化显示器列表
        self.refresh_monitor_list()
        
        # 创建通知位置选择框架
        notification_position_frame = ttk.Frame(settings_frame)
        notification_position_frame.pack(fill=tk.X, pady=5)
        
        # 创建通知位置选择标签
        notification_position_label = ttk.Label(notification_position_frame, text="通知显示位置:")
        notification_position_label.pack(side=tk.LEFT, padx=5)
        
        # 获取保存的通知位置配置
        self.notification_position = config_manager.get('notification_position', 'bottom')
        
        # 创建通知位置下拉框
        self.notification_position_var = tk.StringVar()
        notification_position_combo = ttk.Combobox(
            notification_position_frame,
            textvariable=self.notification_position_var,
            values=["右下", "顶部"],
            state="readonly",
            width=10
        )
        notification_position_combo.pack(side=tk.LEFT, padx=5)
        
        # 设置当前值
        self.notification_position_var.set("右下" if self.notification_position == 'bottom' else "顶部")
        
        # 绑定选择事件
        notification_position_combo.bind("<<ComboboxSelected>>", self.on_notification_position_change)
        # 测试通知位置
        show_notification("通知将在此处展示", position=self.notification_position, level='info')
        
        # 已登记设备信息（摄像头 / 屏幕 / 是否训练过）
        device_info_frame = ttk.LabelFrame(main_frame, text="已登记设备", padding="10")
        device_info_frame.pack(fill=tk.BOTH, expand=False, pady=5)
        self.device_info_text = tk.Text(
            device_info_frame,
            height=6,
            wrap=tk.WORD,
            font=("Microsoft YaHei", 10)
        )
        self.device_info_text.pack(fill=tk.BOTH, expand=True)
        self.device_info_text.config(state=tk.DISABLED)
        self._refresh_device_info()
    
    def _refresh_device_info(self):
        """把 devices.json 里的登记情况显示到界面"""
        if not hasattr(self, 'device_info_text'):
            return
        lines = []
        cameras = self.devices_registry.get('cameras', {})
        screens = self.devices_registry.get('screens', {})
        for camera_id, entry in cameras.items():
            trained = '是' if entry.get('trained') else '否'
            connected = '' if entry.get('connected', True) else '（未接入）'
            lines.append(
                f"摄像头  {entry.get('name', '?')}{connected}  "
                f"训练:{trained}  样本:{entry.get('sample_count', 0)}  [{camera_id}]"
            )
        for screen_id, entry in screens.items():
            trained = '是' if entry.get('trained') else '否'
            connected = '' if entry.get('connected', True) else '（未接入）'
            params = entry.get('params') or {}
            detail = []
            if params.get('connection'):
                detail.append(str(params['connection']))
            if params.get('resolution'):
                detail.append(str(params['resolution']))
            if params.get('size_cm'):
                detail.append(f"{params['size_cm']}cm")
            detail_text = ('  ' + ' '.join(detail)) if detail else ''
            lines.append(
                f"屏幕    {entry.get('name', '?')}{connected}{detail_text}  "
                f"训练:{trained}  样本:{entry.get('sample_count', 0)}  [{screen_id}]"
            )
        if not lines:
            lines = ["（尚未登记设备）"]
        self.device_info_text.config(state=tk.NORMAL)
        self.device_info_text.delete('1.0', tk.END)
        self.device_info_text.insert(tk.END, "\n".join(lines))
        self.device_info_text.config(state=tk.DISABLED)
    def on_notification_position_change(self, event):
        """处理通知位置选择变化"""
        selected_text = self.notification_position_var.get()
        # 转换为配置值
        self.notification_position = 'bottom' if selected_text == "右下" else 'top'
        # 保存到配置
        config_manager.set('notification_position', self.notification_position)
        config_manager.save_config()
        self.log_info(f"通知位置已更改为：{selected_text}")
    
    # ------------------------------------------------------------------
    # 设备与模型
    # ------------------------------------------------------------------

    def init_neural_network_async(self):
        """在后台线程中加载当前摄像头的模型"""
        self.model_loading = True
        self.log_info("正在后台加载神经网络模型...")
        threading.Thread(target=self._load_model_in_background, daemon=True).start()

    def _prior_model_paths(self):
        """打包/用户目录里可用的相机先验模型路径"""
        model_path = config_manager.get('model_path', 'brightness_model.npz')
        return [
            os.path.join(self.data_root, model_path),
            os.path.join(get_executable_path(), model_path),
            get_resource_path(model_path),
        ]

    def get_camera_model(self, camera_id):
        """取得（必要时创建并落盘）某台摄像头的模型"""
        camera_id = camera_id or 'cam-default'
        with self.nn_lock:
            model = self.camera_models.get(camera_id)
            if model is not None:
                self.nn = model
                return model
            model_path = os.path.join(self.data_root, 'cameras', f'{camera_id}.npz')
            model, source = models.load_camera_model_from([model_path] + self._prior_model_paths())
            if model is None:
                self.log_info(f"摄像头 {camera_id} 没有可用模型，按合成先验新建")
                model = models.new_camera_model()
            elif os.path.abspath(source) != os.path.abspath(model_path):
                self.log_info(f"摄像头 {camera_id} 首次使用，已从先验复制一份独立模型")
            try:
                models.save_camera_model(model_path, model)
            except Exception as e:
                self.log_warning(f"保存相机模型失败: {e}")
            self.camera_models[camera_id] = model
            self.nn = model
            return model

    def save_camera_model(self, camera_id):
        model = self.camera_models.get(camera_id)
        if model is None:
            return
        try:
            models.save_camera_model(os.path.join(self.data_root, 'cameras', f'{camera_id}.npz'), model)
        except Exception as e:
            self.log_warning(f"保存相机模型失败: {e}")

    def get_screen_curve(self, screen_id):
        """取得（必要时创建）某块屏幕的曲线"""
        if screen_id not in self.screen_curves:
            path = os.path.join(self.data_root, 'screens', f'{screen_id}.json')
            self.screen_curves[screen_id] = models.load_screen_curve(path)
        return self.screen_curves[screen_id]

    def save_screen_curve(self, screen_id):
        curve = self.screen_curves.get(screen_id)
        if curve is None:
            return
        screen = next((item for item in self.available_monitors if item.get('id') == screen_id), None)
        device = (screen or {}).get('params')
        try:
            models.save_screen_curve(
                os.path.join(self.data_root, 'screens', f'{screen_id}.json'),
                curve,
                device=device
            )
        except Exception as e:
            self.log_warning(f"保存屏幕曲线失败: {e}")

    def predict_for_screen(self, camera_id, screen_id, features):
        """相机模型 → 参考亮度 → 屏幕曲线 → 该屏建议亮度"""
        model = self.get_camera_model(camera_id)
        reference = int(model.forward(features))
        brightness = int(round(float(np.clip(self.get_screen_curve(screen_id).evaluate(reference), 0, 100))))
        return brightness, reference

    def _load_model_in_background(self):
        """后台加载当前摄像头的模型（设备列表已在 create_gui 里刷新过）"""
        try:
            camera = self._active_camera()
            if camera:
                self.get_camera_model(camera.get('camera_id'))
                self.log_info(f"已加载摄像头模型：{camera.get('name')}")
            self.model_loading = False
            self.model_loaded = True
            self.root.after(0, self._on_model_loaded)
        except Exception as e:
            self.log_error(f"初始化神经网络失败: {e}")
            self.model_loading = False
            self.model_loaded = True
            self.root.after(0, self._on_model_loaded)

    def _on_model_loaded(self):
        """模型加载完成后的回调（在主线程中执行）"""
        if self.nn:
            self.log_info("神经网络模型加载完成")
            show_startup_notification("神经网络模型加载完成", level='success')
            self.model_status_label.config(
                text="模型状态: 已加载",
                foreground="green"
            )
            self._update_screen_status()
        else:
            self.log_error("神经网络模型加载失败")
            show_startup_notification("神经网络模型加载失败，部分功能可能不可用", level='error')
            self.model_status_label.config(
                text="模型状态: 加载失败",
                foreground="red"
            )
        
        # 如果自动亮度调整已启用，启动定时任务
        if self.auto_brightness_enabled and self.nn:
            self.log_info("启动自动亮度调整定时任务")
            self.start_auto_adjust_timer()
    
    def setup_tray(self):
        """设置系统托盘"""
        try:
            # 加载图标
            try:
                icon_path = get_resource_path("favicon.ico")
                icon = Image.open(icon_path)
            except:
                # 如果图标不存在或加载失败，创建一个简单的图标
                icon = Image.new('RGB', (64, 64), color='blue')
            
            # 创建系统托盘菜单
            menu = pystray.Menu(
                pystray.MenuItem(
                    "打开管理界面", 
                    self.show_gui
                ),
                pystray.MenuItem(
                    "自动调整亮度", 
                    self.toggle_auto_brightness,
                    checked=lambda item: self.auto_brightness_enabled
                ),
                pystray.MenuItem(
                    "立即调整亮度", 
                    self.auto_adjust_brightness
                ),
                pystray.MenuItem(
                    "退出", 
                    self.on_exit
                )
            )
            
            # 创建系统托盘
            self.tray = pystray.Icon(
                "亮度管理器", 
                icon, 
                "亮度管理器", 
                menu
            )
            
            # 设置左键点击回调
            def on_tray_clicked(icon, item):
                self.show_gui()
            
            self.tray.on_clicked = on_tray_clicked
            
            # 启动系统托盘
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception as e:
            self.log_error(f"设置系统托盘失败: {e}")
    
    def show_gui(self):
        """显示GUI界面"""
        self.root.deiconify()
        self.root.lift()
        
        # 检查模型是否正在加载
        if self.model_loading:
            self.log_info("模型正在加载中，请稍后再试")
            show_window_notification("模型正在加载中，请稍后再试", position=self.notification_position, level='info')
            return
        
        # 启动亮度获取流程
        threading.Thread(target=self.get_brightness_data, daemon=True).start()
    
    def hide_gui(self):
        """隐藏GUI界面"""
        self.root.withdraw()
    
    def on_window_close(self):
        """窗口关闭事件处理"""
        # 隐藏主窗口到系统托盘
        self.hide_gui()
    
    # ------------------------------------------------------------------
    # 亮度应用与训练
    # ------------------------------------------------------------------

    def _active_camera(self):
        """当前选中的摄像头（严格匹配，未选择或已断开时返回 None）"""
        if not self.available_cameras:
            return None
        saved_id = config_manager.get('selected_camera_id')
        selected_name = self.camera_var.get() if hasattr(self, 'camera_var') else ''
        for camera in self.available_cameras:
            if saved_id and camera.get('camera_id') == saved_id:
                return camera
        for camera in self.available_cameras:
            if selected_name and camera.get('name') == selected_name:
                return camera
        return None

    def _active_screen(self):
        if not self.available_monitors:
            return None
        for screen in self.available_monitors:
            if screen.get('id') == self.active_screen_id:
                return screen
        return self.available_monitors[0]

    def _screen_state(self, screen_id):
        state = self.screen_states.get(screen_id)
        if state is None:
            state = {
                'brightness': 50,
                'participates': True,
                'supported': False,
                'backend': 'none',
                'manual_set': False,
            }
            self.screen_states[screen_id] = state
        return state

    def on_screen_change(self, event=None):
        """切换当前屏幕：按下拉框选中的名字切换，并把该屏当前亮度读回滑条"""
        selected_name = self.screen_var.get() if hasattr(self, 'screen_var') else ''
        screen = None
        for item in self.available_monitors:
            if item.get('name') == selected_name:
                screen = item
                break
        if screen is None:
            screen = self._active_screen()
        if not screen:
            return
        self.active_screen_id = screen['id']
        config_manager.set('active_screen_id', self.active_screen_id)
        state = self._screen_state(screen['id'])
        if screen.get('current') is not None:
            state['brightness'] = int(screen['current'])
        value = int(state.get('brightness', 50))
        self.brightness_var.set(value)
        self.brightness_label.config(text=f"亮度: {value}%")
        self._update_screen_status(screen)
        self.log_info(f"当前屏幕：{screen.get('name')}")

    def _update_screen_status(self, screen=None):
        """刷新当前屏幕的后端/支持状态"""
        if not hasattr(self, 'screen_status_label'):
            return
        screen = screen or self._active_screen()
        if not screen:
            self.screen_status_label.config(text="未检测到显示器", foreground="red")
            return
        state = self._screen_state(screen['id'])
        if state.get('supported'):
            backend = str(state.get('backend', 'none')).lower()
            backend_labels = {'wmi': 'WMI', 'ddc': 'DDC/CI', 'gamma': '软件调光（伽马）'}
            text = f"调光后端: {backend_labels.get(backend, backend.upper())}    当前: {state.get('brightness', 0)}%"
            if backend == 'gamma':
                text += "（仅能压暗）"
            self.screen_status_label.config(
                text=text,
                foreground="orange" if backend == 'gamma' else "green"
            )
        else:
            self.screen_status_label.config(
                text="该屏幕不支持调光（WMI / DDC / 软件调光均不可用）",
                foreground="red"
            )

    def on_brightness_change(self, value):
        """亮度滑条变化回调"""
        brightness = int(float(value))
        self.brightness_label.config(text=f"亮度: {brightness}%")
        screen = self._active_screen()
        if screen:
            self._screen_state(screen['id'])['brightness'] = brightness

    def _apply_to_screen(self, screen, brightness, screens=None, manual=True):
        """把亮度写到指定屏幕，返回 set_brightness_targets 的单屏结果"""
        screens = screens or [screen]
        results = set_brightness_targets({screen['id']: int(brightness)}, screens=screens, verify=True)
        result = results.get(screen['id'], {})
        state = self._screen_state(screen['id'])
        state['supported'] = bool(screen.get('supported'))
        state['backend'] = result.get('backend') or screen.get('backend', 'none')
        if result.get('ok'):
            state['brightness'] = int(brightness)
            if manual:
                state['manual_set'] = True
                self._save_manual_screens()
        return result

    def _save_manual_screens(self):
        ids = [sid for sid, state in self.screen_states.items() if state.get('manual_set')]
        config_manager.set('manual_screen_ids', ids)

    def _qt_screen_index(self, screen):
        """把 `\\.\DISPLAYn` 映射到 Qt 屏幕序号；映射不到返回 None。"""
        target = _normalize_display_name((screen or {}).get('display_name'))
        if not target:
            return None
        try:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            if app is None:
                app = get_app()
            if app is None:
                return None
            for index, qt_screen in enumerate(app.screens()):
                if _normalize_display_name(qt_screen.name()) == target:
                    return index
        except Exception:
            return None
        return None

    def _notify_screen(self, screen, message, level='info'):
        """按屏幕定位通知；映射失败退回主屏，绝不把 WMI 序号当 Qt 序号。"""
        index = self._qt_screen_index(screen)
        if index is None:
            display_name = (screen or {}).get('display_name')
            if display_name:
                warned = getattr(self, '_notify_warned', None)
                if warned is None:
                    warned = self._notify_warned = set()
                if display_name not in warned:
                    warned.add(display_name)
                    self.log_warning(f"通知定位失败：{display_name} 不在 Qt 屏幕列表里，将显示在主屏")
            show_notification(message, position=self.notification_position, level=level)
        else:
            show_notification(
                message,
                position=self.notification_position,
                screen_mode='custom',
                screen_index=index,
                level=level
            )

    def apply_brightness(self):
        """应用当前屏幕的滑条值（只写入，不生成训练样本）"""
        if not self.can_click_button("apply_brightness"):
            show_window_notification("操作过于频繁，请稍后再试", position=self.notification_position, level='warning')
            return
        
        if self.model_loading:
            self.log_info("模型正在加载中，请稍后再试")
            show_window_notification("模型正在加载中，请稍后再试", position=self.notification_position, level='info')
            return
        
        screen = self._active_screen()
        if not screen:
            self.log_warning("没有可用的显示器，无法调整亮度")
            show_window_notification("没有可用的显示器", position=self.notification_position, level='warning')
            return
        
        brightness = int(self.brightness_var.get())
        try:
            if not is_monitor_on():
                self.log_info("屏幕处于息屏状态，跳过手动亮度调整")
                show_window_notification("屏幕处于息屏状态，亮度调整已跳过", position=self.notification_position, level='warning')
                return
            result = self._apply_to_screen(screen, brightness)
            if result.get('ok'):
                self.current_brightness = brightness
                self.log_info(f"{screen.get('name')} 亮度已调整为 {brightness}%")
                self._update_screen_status(screen)
                self._notify_screen(screen, f"亮度已调整为 {brightness}%", 'success')
            else:
                error_msg = result.get('error') or "调整亮度失败，显示器可能不支持亮度调节"
                self.log_error(f"{screen.get('name')} 调整失败：{error_msg}")
                self._notify_screen(screen, error_msg, 'error')
        except Exception as e:
            error_msg = f"调整亮度时出错：{e}"
            self.log_error(error_msg)
            show_window_notification(error_msg, position=self.notification_position, level='error')

    def _pending_features(self, camera):
        """最近一次采集的特征是否可用于当前摄像头（5 分钟内有效）"""
        pending = self.pending_capture
        if not pending:
            return None
        if pending.get('camera_id') != camera.get('camera_id'):
            return None
        if time.time() - pending.get('timestamp', 0) > 300:
            return None
        return pending.get('features')

    def _capture_features(self, camera):
        """抓一帧并提取特征；失败时返回 None 并写清原因"""
        index = self._camera_index(camera)
        if index is None:
            self.log_error(f"摄像头 {camera.get('name', '?')} 没有可用的打开序号")
            return None
        try:
            frames = capture_frames_from_camera(camera_index=index, num_frames=10)
        except Exception as e:
            self.log_error(f"摄像头 {camera.get('name', '?')} 打开失败：{e}")
            return None
        if not frames:
            self.log_error(f"摄像头 {camera.get('name', '?')} 未取到画面（可能被占用、已断开，或不是视频设备）")
            return None
        _, features = calculate_average_brightness(frames)
        return features

    def train_current_screen(self):
        """记录当前屏一条样本并拟合它的曲线"""
        self._start_training(fine_tune_camera=False)

    def train_all_screens(self):
        """为每块「参与自动且手动设过」的屏各记录一条样本，再拟合曲线并微调相机"""
        self._start_training(fine_tune_camera=True)

    def _start_training(self, fine_tune_camera):
        if not self.can_click_button("train_network"):
            show_window_notification("操作过于频繁，请稍后再试", position=self.notification_position, level='warning')
            return
        
        if self.model_loading:
            self.log_info("模型正在加载中，请稍后再试")
            show_window_notification("模型正在加载中，请稍后再试", position=self.notification_position, level='info')
            return
        
        camera = self._active_camera()
        if not camera:
            self.log_error("没有可用的摄像头")
            show_window_notification("没有可用的摄像头", position=self.notification_position, level='error')
            return
        
        labels = {}
        if fine_tune_camera:
            for screen in self.available_monitors:
                state = self._screen_state(screen['id'])
                if state.get('participates') and state.get('manual_set'):
                    labels[screen['id']] = int(state.get('brightness', 50))
            if not labels:
                self.log_warning("没有可记录的屏幕：请先手动设置亮度（应用），再点「训练全部勾选屏」")
                show_window_notification("没有手动设置过的屏幕，无法训练", position=self.notification_position, level='warning')
                return
        else:
            screen = self._active_screen()
            if not screen:
                self.log_warning("没有可用的显示器")
                return
            labels[screen['id']] = int(self.brightness_var.get())
        
        self.log_info("开始训练神经网络...")
        if self.training_lock.locked():
            show_window_notification("正在训练中，请稍候", position=self.notification_position, level='info')
            return
        threading.Thread(
            target=self._train_worker,
            args=(camera, labels, fine_tune_camera),
            daemon=True
        ).start()

    def _train_worker(self, camera, labels, fine_tune_camera):
        """后台训练：记录样本 → 拟合屏幕曲线 →（可选）微调相机模型"""
        if not self.training_lock.acquire(blocking=False):
            self.log_warning("已有训练任务在运行，本次训练跳过")
            return
        try:
            features = self._pending_features(camera) or self._capture_features(camera)
            if features is None:
                self.log_error("无法获取画面，训练取消")
                return
            
            camera_id = camera['camera_id']
            expected = 0
            for screen_id, label in labels.items():
                path = models.sample_path(self.data_root, camera_id, screen_id)
                samples_X, samples_y = models.append_samples(path, features, label)
                self._fit_screen(camera_id, screen_id)
                devices.mark_screen_trained(self.devices_registry, screen_id, len(samples_X))
                self.log_info(f"屏幕 {screen_id} 已记录样本并更新曲线（共 {len(samples_X)} 条）")
                expected += 1
            
            if not expected:
                return
            
            if fine_tune_camera:
                self._finetune_camera(camera_id)
            
            devices.mark_camera_trained(self.devices_registry, camera_id)
            try:
                devices.save_registry(self.devices_registry_path, self.devices_registry)
            except Exception as e:
                self.log_warning(f"保存设备登记失败: {e}")
            
            self.root.after(0, lambda: self._update_screen_status())
            self.root.after(0, self._refresh_device_info)
            self.log_info("神经网络训练完成并保存")
        except Exception as e:
            self.log_error(f"训练神经网络失败: {e}")
        finally:
            self.training_lock.release()

    def _fit_screen(self, camera_id, screen_id):
        """用该屏的全部样本（含 legacy）拟合屏幕曲线"""
        model = self.get_camera_model(camera_id)
        path = models.sample_path(self.data_root, camera_id, screen_id)
        features, labels = models.load_samples(path)
        if not features:
            return
        legacy_X, legacy_y = models.legacy_weighted(*models.load_legacy_samples(self.legacy_samples_path))
        all_features = list(features) + list(legacy_X)
        all_labels = list(labels) + list(legacy_y)
        references = [model.forward(item) for item in all_features]
        curve = self.get_screen_curve(screen_id)
        curve.fit(references, all_labels, ridge=1.0, sample_count=len(features))
        self.save_screen_curve(screen_id)

    def _finetune_camera(self, camera_id):
        """用本摄像头所有屏幕的样本微调相机模型（样本不足时跳过）"""
        model = self.get_camera_model(camera_id)
        reference_samples = []
        total = 0
        for screen in self.available_monitors:
            path = models.sample_path(self.data_root, camera_id, screen['id'])
            features, labels = models.load_samples(path)
            if not features:
                continue
            curve = self.get_screen_curve(screen['id'])
            references = curve.inverse(np.asarray(labels, dtype=np.float64))
            reference_samples.extend(zip(features, references.tolist()))
            total += len(features)
        
        if total < 20:
            self.log_info(f"真实样本仅 {total} 条，跳过相机模型微调（避免过拟合）")
            return
        
        with self.nn_lock:
            models.finetune_camera_model(
                model,
                reference_samples,
                prior_samples=max(200, total),
                epochs=300,
                learning_rate=0.0005
            )
        self.save_camera_model(camera_id)
        self.log_info(f"相机模型已用 {total} 条样本微调")
        # 相机模型变了，重新拟合各屏曲线
        for screen in self.available_monitors:
            path = models.sample_path(self.data_root, camera_id, screen['id'])
            if models.load_samples(path)[0]:
                self._fit_screen(camera_id, screen['id'])
    
    def can_click_button(self, button_name):
        """检查按钮是否可以点击（频率限制）
        
        Args:
            button_name: 按钮名称标识
            
        Returns:
            bool: 是否可以点击
        """
        import time
        current_time = time.time()
        
        # 检查该按钮是否有点击记录
        if button_name not in self.button_click_locks:
            self.button_click_locks[button_name] = current_time
            return True
        
        # 检查距离上次点击的时间间隔
        last_click_time = self.button_click_locks[button_name]
        if current_time - last_click_time >= self.button_click_interval:
            self.button_click_locks[button_name] = current_time
            return True
        
        # 点击频率过高，拒绝本次点击
        remaining_time = self.button_click_interval - (current_time - last_click_time)
        self.log_info(f"按钮 [{button_name}] 点击频率过高，请稍后再试")
        return False
    
    def on_get_brightness_click(self):
        """获取亮度按钮点击回调"""
        if not self.can_click_button("get_brightness"):
            show_window_notification("操作过于频繁，请稍后再试", position=self.notification_position, level='warning')
            return
        
        threading.Thread(target=self.get_brightness_data, daemon=True).start()

    def get_brightness_data(self):
        """采集一次环境画面，为每块屏计算建议亮度（只更新界面，不写硬件）"""
        self.log_info("正在获取亮度数据...")
        self.root.after(0, lambda: show_window_notification("正在获取亮度...", position=self.notification_position, level='info'))
        try:
            camera = self._active_camera()
            if not camera:
                self.log_error("未选择摄像头或摄像头已断开，无法获取亮度")
                self.root.after(0, lambda: show_window_notification("未选择摄像头，请先在设置中选择", position=self.notification_position, level='error'))
                return
            
            index = self._camera_index(camera)
            if index is None:
                self.log_error(f"摄像头 {camera.get('name', '?')} 没有可用的打开序号")
                self.root.after(0, lambda: show_window_notification("摄像头配置无效，请刷新后重试", position=self.notification_position, level='error'))
                return
            
            frames = capture_frames_from_camera(camera_index=index, num_frames=10)
            if not frames:
                self.log_error(f"摄像头 {camera.get('name', '?')} 未取到画面（可能被占用、已断开或不是视频设备）")
                self.root.after(0, lambda: show_window_notification("无法获取摄像头图像，请检查连接", position=self.notification_position, level='error'))
                return
            
            _, features = calculate_average_brightness(frames)
            self.pending_capture = {
                'features': features,
                'camera_id': camera['camera_id'],
                'timestamp': time.time(),
            }
            with self.data_lock:
                self.brightness_data.append(features)
                if len(self.brightness_data) > 100:
                    self.brightness_data = self.brightness_data[-100:]
            
            predictions = {}
            for screen in self.available_monitors:
                predicted, reference = self.predict_for_screen(camera['camera_id'], screen['id'], features)
                predictions[screen['id']] = (predicted, reference)
                self._screen_state(screen['id'])['predicted'] = predicted
                self.log_info(f"{screen.get('name')}：参考亮度 {reference}% → 建议 {predicted}%")
            
            screen = self._active_screen()
            if screen and screen['id'] in predictions:
                predicted, reference = predictions[screen['id']]
                self.current_brightness = predicted
                self.root.after(0, lambda v=predicted: self._show_predicted(v))
                self.log_info(f"当前环境亮度（参考）: {reference}")
                self.log_info(f"神经网络推荐亮度: {predicted}%")
        except Exception as e:
            self.log_error(f"获取亮度数据失败：{e}")
            self.root.after(0, lambda: show_window_notification(f"获取亮度失败：{e}", position=self.notification_position, level='error'))
    
    def _show_predicted(self, brightness):
        """把建议亮度写回滑条（主线程执行）"""
        self.brightness_var.set(int(brightness))
        self.brightness_label.config(text=f"亮度: {int(brightness)}%")
    
    def auto_adjust_brightness(self):
        """自动调整：对「勾选参与自动」且支持调光的屏幕逐块写入"""
        try:
            if not is_monitor_on():
                self.log_info("屏幕处于息屏状态，跳过亮度调整")
                return
            
            self.root.after(0, lambda: show_window_notification("正在自动调整亮度...", position=self.notification_position, level='info'))
            
            def run_with_timeout():
                try:
                    camera = self._active_camera()
                    if not camera:
                        self._handle_auto_adjust_failure("未选择摄像头或摄像头已断开，已跳过本次调整")
                        return
                    
                    index = self._camera_index(camera)
                    if index is None:
                        self._handle_auto_adjust_failure(f"摄像头 {camera.get('name', '?')} 没有可用的打开序号")
                        return
                    
                    frames = capture_frames_from_camera(camera_index=index, num_frames=10)
                    if not frames:
                        self._handle_auto_adjust_failure(
                            f"摄像头 {camera.get('name', '?')} 未取到画面（可能被占用、已断开或不是视频设备）"
                        )
                        return
                    
                    _, features = calculate_average_brightness(frames)
                    self.pending_capture = {
                        'features': features,
                        'camera_id': camera['camera_id'],
                        'timestamp': time.time(),
                    }
                    
                    targets = {}
                    for screen in self.available_monitors:
                        state = self._screen_state(screen['id'])
                        if not state.get('participates'):
                            continue
                        if not state.get('supported'):
                            continue
                        predicted, _ = self.predict_for_screen(camera['camera_id'], screen['id'], features)
                        targets[screen['id']] = predicted
                    
                    if not targets:
                        self._handle_auto_adjust_failure("没有参与自动调节且支持调光的屏幕")
                        return
                    
                    results = set_brightness_targets(targets, screens=self.available_monitors, verify=True)
                    applied = []
                    for screen in self.available_monitors:
                        result = results.get(screen['id'])
                        if not result:
                            continue
                        if result.get('ok'):
                            self._screen_state(screen['id'])['brightness'] = targets[screen['id']]
                            applied.append((screen, targets[screen['id']]))
                        else:
                            self.log_warning(f"{screen.get('name')} 自动调整失败：{result.get('error')}")
                    
                    if applied:
                        for screen, value in applied:
                            self.root.after(0, lambda s=screen, v=value: self._notify_screen(
                                s, f"亮度已自动调整为 {v}%", 'success'
                            ))
                        names = ', '.join(f"{s.get('name')}={v}%" for s, v in applied)
                        self.log_info(f"自动调整完成：{names}")
                        self.root.after(0, lambda: self._update_screen_status())
                    else:
                        self._handle_auto_adjust_failure("自动调整亮度失败，显示器可能不支持亮度调节")
                except Exception as e:
                    self._handle_auto_adjust_failure(f"自动调整亮度线程执行出错: {e}")
            
            thread = threading.Thread(target=run_with_timeout, daemon=True)
            thread.start()
            thread.join(timeout=30)
            
            if thread.is_alive():
                error_msg = "自动调整亮度线程超时"
                self.log_error(error_msg)
                self.root.after(0, lambda: show_window_notification(error_msg, position=self.notification_position, level='error'))
        except Exception as e:
            error_msg = f"自动调整亮度时出错：{e}"
            self.log_error(error_msg)
            self.root.after(0, lambda: show_window_notification(error_msg, position=self.notification_position, level='error'))
    
    def toggle_auto_brightness(self):
        """切换自动亮度调整"""
        self.auto_brightness_enabled = not self.auto_brightness_enabled
        config_manager.set('auto_brightness', self.auto_brightness_enabled)
        
        if self.auto_brightness_enabled:
            self.log_info("已启用自动亮度调整")
            self.start_auto_adjust_timer()
        else:
            self.log_info("已禁用自动亮度调整")
            self.stop_auto_adjust_timer()
    
    def start_auto_adjust_timer(self):
        """启动自动调整定时任务"""
        # 取消之前的定时任务
        self.stop_auto_adjust_timer()
        
        # 创建新的定时任务
        def auto_adjust():
            # 在独立线程中执行亮度调整
            def run_adjust():
                try:
                    self.auto_adjust_brightness()
                finally:
                    # 重新启动定时任务
                    self.start_auto_adjust_timer()
            
            thread = threading.Thread(target=run_adjust, daemon=True)
            thread.start()
        
        self.auto_adjust_timer = threading.Timer(self.check_interval, auto_adjust)
        self.auto_adjust_timer.daemon = True
        self.auto_adjust_timer.start()
    
    def stop_auto_adjust_timer(self):
        """停止自动调整定时任务"""
        if self.auto_adjust_timer:
            self.auto_adjust_timer.cancel()
            self.auto_adjust_timer = None
    
    def _check_devices_available(self):
        """检查设备（摄像头和显示器）是否可用
        
        Returns:
            bool: 设备是否可用
        """
        # 在检查前，先检测是否有热插拔变化（只在需要时检测）
        self.check_camera_hotplug_if_needed()
        self.check_monitor_hotplug_if_needed()
        
        # 检查摄像头
        if not self.available_cameras:
            self.log_warning("没有可用的摄像头，无法获取环境亮度")
            return False
        
        # 检查选中的摄像头是否仍然可用
        selected_camera = self.camera_var.get()
        if not selected_camera or selected_camera not in [c['name'] for c in self.available_cameras]:
            self.log_warning(f"选中的摄像头 '{selected_camera}' 不可用")
            return False
        
        # 检查显示器
        if not self.available_monitors:
            self.log_warning("没有可用的显示器，无法调整亮度")
            return False
        
        return True
    
    def _handle_auto_adjust_failure(self, error_msg):
        """处理自动亮度调整失败
        
        Args:
            error_msg: 错误信息
        """
        self.log_error(error_msg)
        self.root.after(0, lambda msg=error_msg: show_window_notification(msg, position=self.notification_position, level='error'))
    
    def on_startup_change(self):
        """开机自启动设置变化回调"""
        self.startup_enabled = self.startup_var.get()
        config_manager.set('startup', self.startup_enabled)
        
        # 设置开机自启动
        success = SystemUtils.set_startup(self.startup_enabled)
        if success:
            status = "已启用" if self.startup_enabled else "已禁用"
            self.log_info(f"{status}开机自启动")
        else:
            self.log_error("设置开机自启动失败")
            # 使用 Tkinter 的 after 方法在主线程显示通知
            self.root.after(0, lambda: show_window_notification("设置开机自启动失败", position=self.notification_position, level='error'))
    
    def on_interval_change(self, event):
        """循环检查间隔变化回调"""
        selected_text = self.interval_var.get()
        for seconds, text in self.interval_options:
            if text == selected_text:
                self.check_interval = seconds
                config_manager.set('check_interval', self.check_interval)
                self.log_info(f"循环检查间隔已设置为：{text}")
                # 如果自动亮度调整已启用，重启定时任务
                if self.auto_brightness_enabled:
                    self.start_auto_adjust_timer()
                break
    
    # ------------------------------------------------------------------
    # 设备刷新与选择
    # ------------------------------------------------------------------

    def _register_devices(self):
        """把当前设备列表合并进 devices.json"""
        try:
            self.devices_registry = devices.register(
                self.devices_registry, self.available_cameras, self.available_monitors
            )
            devices.save_registry(self.devices_registry_path, self.devices_registry)
        except Exception as e:
            self.log_warning(f"保存设备登记失败: {e}")
        self._refresh_device_info()

    def refresh_camera_list(self, restore_selection=True, force_refresh=False):
        """刷新摄像头列表（按 camera_id 恢复选择）"""
        try:
            self.available_cameras = get_available_cameras(force_refresh=force_refresh)
            camera_names = [camera['name'] for camera in self.available_cameras]
            self.camera_combo['values'] = camera_names
            
            saved_id = config_manager.get('selected_camera_id')
            saved_name = config_manager.get('selected_camera_name')
            if restore_selection:
                chosen = devices.choose_default_camera(self.available_cameras, saved_id, saved_name)
            else:
                chosen = devices.choose_default_camera(self.available_cameras)
            
            if chosen:
                self.camera_var.set(chosen['name'])
                config_manager.set('selected_camera_id', chosen['camera_id'])
                config_manager.set('selected_camera_name', chosen['name'])
                if devices.is_virtual_camera(chosen):
                    self.log_warning(
                        f"当前选择的是虚拟摄像头（{chosen['name']}），画面可能来自手机投屏，"
                        "建议在设置中改用物理摄像头"
                    )
            else:
                # 保守策略：保存的摄像头不在线时不自动切到别的设备（尤其不能悄悄切到手机虚拟摄像头）
                self.camera_var.set('')
                config_manager.set('selected_camera_id', None)
                if camera_names:
                    self.log_warning("上次使用的摄像头未接入，已暂停自动调节；请在设置中重新选择摄像头")
                    self.root.after(1000, lambda: show_window_notification(
                        "上次使用的摄像头未接入，请重新选择摄像头",
                        position=self.notification_position,
                        level='warning'
                    ))
                else:
                    self.log_warning("没有检测到可用的摄像头")
            
            self._register_devices()
            self.log_info(f"已刷新摄像头列表，找到 {len(self.available_cameras)} 个摄像头")
        except Exception as e:
            self.log_error(f"刷新摄像头列表失败：{e}")
            self.camera_var.set('')
    
    def on_refresh_cameras(self):
        """手动刷新摄像头列表"""
        if not self.can_click_button("refresh_cameras"):
            show_window_notification("操作过于频繁，请稍后再试", position=self.notification_position, level='warning')
            return
        
        self.log_info("正在刷新摄像头列表...")
        self.refresh_camera_list(force_refresh=True)
        show_window_notification("摄像头列表已刷新", position=self.notification_position, level='success')
    
    def _rebuild_monitor_checkboxes(self):
        """重建「参与自动调节」复选框（按 screen_id）"""
        for widget in self.monitor_checkboxes_frame.winfo_children():
            widget.destroy()
        self.monitor_vars = {}
        for screen in self.available_monitors:
            state = self._screen_state(screen['id'])
            var = tk.BooleanVar(value=bool(state.get('participates', True)))
            self.monitor_vars[screen['id']] = var
            var.trace_add('write', lambda *args, sid=screen['id']: self.on_monitor_change(sid))
            text = screen['name']
            if not screen.get('supported'):
                text += "（不支持调光）"
            ttk.Checkbutton(
                self.monitor_checkboxes_frame,
                text=text,
                variable=var
            ).pack(anchor=tk.W)
    
    def _refresh_screen_combo(self):
        """刷新当前屏幕下拉框，并把该屏亮度同步到滑条"""
        values = [screen['name'] for screen in self.available_monitors]
        self.screen_combo['values'] = values
        
        if not self.available_monitors:
            self.active_screen_id = None
            self.screen_var.set('')
            return
        
        ids = [screen['id'] for screen in self.available_monitors]
        saved_active = config_manager.get('active_screen_id')
        if saved_active in ids:
            self.active_screen_id = saved_active
        elif self.active_screen_id not in ids:
            self.active_screen_id = ids[0]
        
        screen = self._active_screen()
        if not screen:
            return
        self.screen_var.set(screen['name'])
        state = self._screen_state(screen['id'])
        if screen.get('current') is not None and not state.get('manual_set'):
            state['brightness'] = int(screen['current'])
        value = int(state.get('brightness', 50))
        self.brightness_var.set(value)
        self.brightness_label.config(text=f"亮度: {value}%")
        self._update_screen_status(screen)
    
    def refresh_monitor_list(self, restore_selection=True, force_refresh=False):
        """刷新显示器列表（WMI 身份 + DDC 回退）"""
        try:
            self.available_monitors = list_brightness_capabilities(force_refresh=force_refresh)
            
            saved_ids = config_manager.get('selected_screen_ids') or []
            manual_ids = set(config_manager.get('manual_screen_ids') or [])
            for screen in self.available_monitors:
                state = self._screen_state(screen['id'])
                state['supported'] = bool(screen.get('supported'))
                state['backend'] = screen.get('backend', 'none')
                if restore_selection and saved_ids:
                    state['participates'] = screen['id'] in saved_ids
                else:
                    state.setdefault('participates', True)
                if screen['id'] in manual_ids:
                    state['manual_set'] = True
                if screen.get('current') is not None and not state.get('manual_set'):
                    state['brightness'] = int(screen['current'])
            
            self._rebuild_monitor_checkboxes()
            self._refresh_screen_combo()
            self._register_devices()
            
            # 记录当前显示器列表用于热插拔检测
            self.last_monitor_ids = [screen['id'] for screen in self.available_monitors]
            
            found = len(self.available_monitors)
            supported = sum(1 for screen in self.available_monitors if screen.get('supported'))
            self.log_info(f"已刷新显示器列表：{found} 块屏，{supported} 块支持软件调光")
        except Exception as e:
            self.log_error(f"刷新显示器列表失败：{e}")
    
    def on_refresh_monitors(self):
        """手动刷新显示器列表"""
        if not self.can_click_button("refresh_monitors"):
            show_window_notification("操作过于频繁，请稍后再试", position=self.notification_position, level='warning')
            return
        
        self.log_info("正在刷新显示器列表...")
        self.refresh_monitor_list(force_refresh=True)
        show_window_notification("显示器列表已刷新", position=self.notification_position, level='success')
    
    def refresh_devices(self, force_refresh=False):
        """同时刷新摄像头与显示器"""
        self.refresh_camera_list(restore_selection=True, force_refresh=force_refresh)
        self.refresh_monitor_list(restore_selection=True, force_refresh=force_refresh)
    
    def on_camera_change(self, event):
        """摄像头选择变化回调"""
        camera = self._active_camera()
        if not camera:
            # 下拉框被清空（设备断开）：保持未选择状态，不自动切到其它摄像头
            config_manager.set('selected_camera_id', None)
            self.log_warning("未选择摄像头，自动调节已暂停")
            return
        config_manager.set('selected_camera_id', camera['camera_id'])
        config_manager.set('selected_camera_name', camera['name'])
        self.log_info(f"已选择摄像头: {camera['name']}（{camera['camera_id']}）")
        if devices.is_virtual_camera(camera):
            self.log_warning(
                "这是虚拟摄像头（可能是手机投屏），画面来自另一台设备，环境光测量可能不准确"
            )
            show_window_notification(
                "当前选中的是虚拟摄像头，建议改用物理摄像头",
                position=self.notification_position,
                level='warning'
            )
        threading.Thread(target=self._switch_camera_model, args=(camera,), daemon=True).start()
    
    def _camera_index(self, camera):
        """摄像头在 OpenCV 中的序号；无效时返回 None"""
        try:
            return int((camera or {}).get('index'))
        except (TypeError, ValueError):
            return None
    
    def _switch_camera_model(self, camera):
        """后台加载切换后摄像头的模型"""
        try:
            self.get_camera_model(camera['camera_id'])
            self.log_info(f"已切换到摄像头模型：{camera['name']}")
            self.root.after(0, lambda: self._update_screen_status())
        except Exception as e:
            self.log_error(f"切换摄像头模型失败: {e}")
    
    def on_monitor_change(self, screen_id=None):
        """显示器勾选变化：勾选 = 参与自动调节"""
        if screen_id is not None:
            var = self.monitor_vars.get(screen_id)
            if var is not None:
                self._screen_state(screen_id)['participates'] = bool(var.get())
        
        selected_ids = []
        for sid, var in self.monitor_vars.items():
            self._screen_state(sid)['participates'] = bool(var.get())
            if var.get():
                selected_ids.append(sid)
        config_manager.set('selected_screen_ids', selected_ids)
        self.log_info(f"参与自动调节的屏幕：{len(selected_ids)} 块")
    
    def _handle_selected_monitors_unavailable(self, removed_screen_ids):
        """参与自动调节的屏幕断开时自动重新选择"""
        remaining = [
            screen for screen in self.available_monitors
            if screen['id'] not in removed_screen_ids
        ]
        selected = [
            screen for screen in remaining
            if self._screen_state(screen['id']).get('participates')
        ]
        if selected:
            return
        
        self.log_warning("参与自动调节的屏幕都已断开，自动选择可用屏幕")
        for screen in remaining:
            self._screen_state(screen['id'])['participates'] = True
            var = self.monitor_vars.get(screen['id'])
            if var is not None:
                var.set(True)
        config_manager.set('selected_screen_ids', [screen['id'] for screen in remaining])
        if remaining:
            names = ', '.join(screen['name'] for screen in remaining)
            show_window_notification(f"显示器已断开，已自动选择：{names}", position=self.notification_position, level='warning')
        else:
            show_window_notification("所有显示器已断开，无法调整亮度", position=self.notification_position, level='error')
    
    def start_hotplug_detection(self):
        """启动热插拔检测（改为按需检测）"""
        self.last_camera_ids = [camera['camera_id'] for camera in self.available_cameras]
        self.last_monitor_ids = [screen['id'] for screen in self.available_monitors]
        self.log_info("热插拔检测已禁用（改为按需检测）")
    
    def check_camera_hotplug_if_needed(self):
        """需要时检测摄像头热插拔（断开时不自动切到其它摄像头）"""
        current_id = config_manager.get('selected_camera_id')
        current_ids = [camera['camera_id'] for camera in self.available_cameras]
        
        if current_id and current_id not in current_ids:
            self.log_info("检测到摄像头可能已断开，正在刷新列表...")
            self.refresh_camera_list(restore_selection=True, force_refresh=True)
        elif not self.available_cameras:
            self.refresh_camera_list(restore_selection=False, force_refresh=True)
    
    def check_monitor_hotplug_if_needed(self):
        """需要时检测显示器热插拔"""
        selected_ids = {
            sid for sid, var in self.monitor_vars.items() if var.get()
        }
        current_ids = {screen['id'] for screen in self.available_monitors}
        missing = selected_ids - current_ids
        
        if missing:
            self.log_info("检测到显示器可能已断开，正在刷新列表...")
            self.refresh_monitor_list(restore_selection=True, force_refresh=True)
            current_ids = {screen['id'] for screen in self.available_monitors}
            missing = selected_ids - current_ids
            if missing:
                names = ', '.join(sorted(missing))
                show_window_notification(f"显示器已断开：{names}", position=self.notification_position, level='warning')
                self._handle_selected_monitors_unavailable(missing)
        elif not self.available_monitors:
            self.refresh_monitor_list(restore_selection=False, force_refresh=True)
    
    def get_selected_camera_index(self):
        """当前摄像头的 OpenCV 序号（没有可用摄像头时为 None）"""
        camera = self._active_camera()
        return camera['index'] if camera else None
    
    def get_selected_monitor_indices(self):
        """参与自动调节的屏幕枚举序号（兼容旧接口）"""
        indices = [
            screen['index'] for screen in self.available_monitors
            if self._screen_state(screen['id']).get('participates')
        ]
        if not indices and self.available_monitors:
            indices.append(self.available_monitors[0]['index'])
        return indices
    
    def log_info(self, message):
        """记录信息"""
        self.log(message, "INFO")
    
    def log_warning(self, message):
        """记录警告"""
        self.log(message, "WARNING")
    
    def log_error(self, message):
        """记录错误"""
        self.log(message, "ERROR")
    
    def log(self, message, level="INFO"):
        """记录日志"""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] [{level}] {message}\n"
        
        # 使用after确保GUI更新在主线程中执行
        def update_gui():
            # 更新信息文本框
            self.info_text.config(state=tk.NORMAL)
            self.info_text.insert(tk.END, log_message)
            self.info_text.see(tk.END)
            self.info_text.config(state=tk.DISABLED)
        
        # 将GUI更新任务放到主线程的事件队列中
        self.root.after(0, update_gui)
        
        # 打印到控制台
        print(log_message.strip())
    
    def on_exit(self):
        """退出应用"""
        # 恢复被软件调光（伽马）改过的屏幕
        try:
            restore_gamma_ramps()
        except Exception as e:
            print(f"恢复伽马表失败: {e}")
        
        # 停止自动调整定时任务
        self.stop_auto_adjust_timer()
        
        # 停止系统托盘
        if self.tray:
            self.tray.stop()
        
        # 安全退出应用
        # 先停止Tkinter主循环
        self.root.quit()
        # 然后使用线程安全的方式退出
        # 使用destroy()确保所有Tkinter资源被释放
        self.root.destroy()
        
        # 释放全局资源
        global _global_mutex, _instance_lock_file
        
        # 释放文件锁
        if _instance_lock_file:
            try:
                # 在Windows上，文件关闭时会自动释放锁
                _instance_lock_file.close()
                _instance_lock_file = None
            except Exception as e:
                print(f"释放文件锁失败: {e}")
        
        # 释放互斥量
        if _global_mutex:
            try:
                win32event.CloseHandle(_global_mutex)
                _global_mutex = None
            except Exception as e:
                print(f"释放互斥量失败: {e}")
    
    def run(self):
        """运行应用"""
        # 启动主循环
        self.root.mainloop()

def find_window(title):
    """根据窗口标题查找窗口
    
    Args:
        title: 窗口标题
    
    Returns:
        int: 窗口句柄
    """
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            window_title = win32gui.GetWindowText(hwnd)
            if title in window_title:
                extra.append(hwnd)
    
    hwnds = []
    win32gui.EnumWindows(callback, hwnds)
    return hwnds[0] if hwnds else 0

def is_single_instance():
    """检查是否为单实例运行
    
    Returns:
        bool: 如果是单实例，返回True；否则返回False
    """
    global _global_mutex
    global _instance_lock_file
    
    # 1. 使用更唯一的互斥量名称，包含应用程序的唯一标识符
    # 使用GUID确保互斥量名称的唯一性，避免与其他应用冲突
    mutex_name = "Global\\{8B5B0A6A-4F9F-4C1E-8A3B-1C7D5E8F9A0B}-BrightnessManagerMutex"
    
    # 首先检查互斥量，如果成功则直接返回True
    try:
        # 创建互斥量，Global前缀确保在所有会话中都有效
        # 参数说明：
        # lpMutexAttributes: None表示默认安全属性
        # bInitialOwner: False表示不立即获取互斥量所有权
        # lpName: 互斥量名称，Global前缀确保在所有会话中都有效
        _global_mutex = win32event.CreateMutex(None, False, mutex_name)
        
        # 检查是否创建成功
        last_error = win32api.GetLastError()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            # 互斥量已存在，说明已有实例在运行
            if _global_mutex:
                win32event.CloseHandle(_global_mutex)
                _global_mutex = None
            
            # 尝试查找并激活已有实例的窗口
            try:
                hwnd = find_window("亮度管理器")
                if hwnd:
                    # 激活窗口
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
            except Exception as e:
                print(f"激活已有实例窗口失败: {e}")
            
            return False
        elif last_error == 0:
            # 互斥量创建成功，保存互斥量句柄，确保程序运行期间不会被关闭
            return True
        else:
            # 其他错误，继续尝试其他检测方法
            print(f"创建互斥量失败，错误代码: {last_error}")
    except Exception as e:
        print(f"互斥量检测失败: {e}")
    
    # 2. 进程检测作为备份机制（使用win32api和win32process，无需额外依赖）
    try:
        # 获取当前进程的可执行文件路径和名称
        current_exe = sys.executable if getattr(sys, 'frozen', False) else sys.argv[0]
        current_exe_name = os.path.basename(current_exe).lower()
        
        # 统计同名进程数量
        count = 0
        
        # 使用win32api.EnumProcesses获取所有进程ID
        pids = win32process.EnumProcesses()
        
        for pid in pids:
            handle = None
            try:
                if pid == 0:
                    continue  # 跳过无效进程ID
                
                # 打开进程
                handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
                
                if handle:
                    try:
                        # 获取进程的可执行文件路径
                        exe_path = win32process.GetModuleFileNameEx(handle, 0)
                        exe_name = os.path.basename(exe_path).lower()
                        
                        # 检查是否与当前进程同名
                        if exe_name == current_exe_name:
                            count += 1
                            if count > 1:
                                # 已有其他实例运行
                                return False
                    except win32process.error as e:
                        # 无法获取进程信息，跳过
                        pass
            except win32api.error as e:
                # 无法打开进程（可能权限不足），跳过
                pass
            finally:
                # 确保进程句柄总是被关闭
                if handle:
                    try:
                        win32api.CloseHandle(handle)
                    except win32api.error:
                        pass
        
        # 如果只有一个进程（当前进程），继续检查文件锁
        if count == 1:
            pass
        else:
            return False
    except Exception as e:
        print(f"进程检测失败: {e}")
    
    # 3. 作为最后手段，检查文件锁（Windows兼容版本）
    try:
        lock_file_path = os.path.join(SystemUtils.get_app_data_path(), "instance.lock")
        
        # 确保应用数据目录存在
        os.makedirs(os.path.dirname(lock_file_path), exist_ok=True)
        
        # 在Windows上，使用msvcrt模块的locking函数
        import msvcrt  # Windows文件控制模块
        
        # 尝试以独占方式打开文件
        lock_file = open(lock_file_path, "w")
        # 获取文件句柄
        handle = msvcrt.get_osfhandle(lock_file.fileno())
        
        try:
            # 尝试锁定文件的前10个字节，非阻塞模式
            # LK_NBLCK = 0x10 表示非阻塞锁定
            msvcrt.locking(handle, 0x10, 10)
            # 将文件句柄保存到全局变量，确保程序运行期间不会被关闭
            _instance_lock_file = lock_file
            return True
        except OSError:
            # 文件已被锁定，关闭文件并返回False
            lock_file.close()
            return False
    except Exception as e:
        print(f"文件锁检测失败: {e}")
        # 如果所有检测都失败，我们假设已有实例在运行，返回False
        return False
    
    # 如果执行到这里，说明所有检测都失败了，我们应该返回False
    # 因为我们无法确定是否已有实例在运行，保守起见，不允许启动新实例
    return False

if __name__ == "__main__":
    # 设置全局异常处理
    import traceback
    
    def global_exception_handler(exctype, value, tb):
        """全局异常处理器，记录崩溃信息"""
        error_msg = ''.join(traceback.format_exception(exctype, value, tb))
        try:
            from config import get_user_data_path
            log_path = os.path.join(get_user_data_path(), 'crash.log')
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*50}\n")
                f.write(f"崩溃时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"错误类型: {exctype.__name__}\n")
                f.write(f"错误信息: {value}\n")
                f.write(f"堆栈跟踪:\n{error_msg}\n")
        except:
            pass
        print(f"程序崩溃: {error_msg}")
    
    # 安装全局异常处理器
    sys.excepthook = global_exception_handler
    
    # 检查是否为单实例运行
    if not is_single_instance():
        print("应用程序已经在运行中！")
        # 使用 PyQt5 消息框显示重复实例警告
        from PyQt5.QtWidgets import QMessageBox
        from notification import get_app
        
        # 确保 QApplication 存在
        app = get_app()
        
        # 创建消息框
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setWindowTitle("应用程序已在运行")
        msg_box.setText("亮度管理器已经在运行中！")
        msg_box.setStandardButtons(QMessageBox.Ok | QMessageBox.Abort)
        msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowStaysOnTopHint)
        
        # 自定义按钮文本
        ok_button = msg_box.button(QMessageBox.Ok)
        ok_button.setText("取消")
        abort_button = msg_box.button(QMessageBox.Abort)
        abort_button.setText("结束已有实例并启动")
        
        # 显示消息框并等待用户确认
        result = msg_box.exec_()
        
        # 如果用户选择"结束已有实例并启动"
        if result == QMessageBox.Abort:
            # 结束已有实例
            try:
                hwnd = find_window("亮度管理器")
                if hwnd:
                    import win32process
                    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                    handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
                    win32api.TerminateProcess(handle, 0)
                    win32api.CloseHandle(handle)
                    print(f"已结束已有实例 (PID: {pid})")
                    
                    # 短暂等待，确保进程已完全退出
                    time.sleep(0.5)
                    
                    # 释放互斥量，允许新实例启动
                    if _global_mutex:
                        win32event.CloseHandle(_global_mutex)
                        _global_mutex = None
                    
                    # 继续启动新实例
                    print("正在启动新实例...")
                else:
                    print("未找到已有实例窗口")
            except Exception as e:
                print(f"结束已有实例失败：{e}")
        else:
            # 用户选择取消，退出程序
            sys.exit(0)
    
    # 显示启动通知
    show_startup_notification("正在启动亮度管理器", level='info')
    # 创建并运行应用
    app = BrightnessManagerApp()
    app.run()