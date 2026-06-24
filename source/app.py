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

from liangdu import BrightnessNeuralNetwork, capture_frames_from_camera, calculate_average_brightness, adjust_brightness, generate_synthetic_data, get_available_cameras, get_available_monitors
from config import config_manager
from utils import SystemUtils, ToastNotifier
from notification import Notification, show_notification, show_startup_notification, show_window_notification, show_cursor_notification, show_all_screens_notification

# 全局资源变量，确保整个程序运行期间保持打开
_global_mutex = None
_instance_lock_file = None

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
        self.nn = None  # 神经网络模型
        self.current_brightness = 50  # 当前亮度值
        self.brightness_data = []  # 亮度数据缓存
        self.auto_brightness_enabled = config_manager.get('auto_brightness', True)  # 自动亮度调整开关，默认开启
        self.check_interval = config_manager.get('check_interval', 300)  # 循环检查间隔（秒）
        # 获取实际的开机自启动状态
        self.startup_enabled = SystemUtils.get_startup()
        # 更新配置文件
        config_manager.set('startup', self.startup_enabled)
        
        # 摄像头和显示器选择相关变量
        self.available_cameras = []  # 可用摄像头列表
        self.available_monitors = []  # 可用显示器列表
        self.selected_camera_index = 0  # 选中的摄像头索引
        self.selected_monitor_indices = [0]  # 选中的显示器索引列表
        
        # 记录上次可用的显示器名称列表，用于热插拔检测
        self.last_monitor_names = []
        self.last_camera_names = []
        
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

        # 创建应用按钮
        apply_button = ttk.Button(button_frame, text="应用", command=self.apply_brightness)
        apply_button.pack(side=tk.LEFT, padx=5)

        # 创建训练按钮
        train_button = ttk.Button(button_frame, text="训练", command=self.train_network)
        train_button.pack(side=tk.LEFT, padx=5)
        
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
    def on_notification_position_change(self, event):
        """处理通知位置选择变化"""
        selected_text = self.notification_position_var.get()
        # 转换为配置值
        self.notification_position = 'bottom' if selected_text == "右下" else 'top'
        # 保存到配置
        config_manager.set('notification_position', self.notification_position)
        config_manager.save_config()
        self.log_info(f"通知位置已更改为：{selected_text}")
    
    def init_neural_network_async(self):
        """在后台线程中异步初始化神经网络"""
        self.model_loading = True
        self.log_info("正在后台加载神经网络模型...")
        
        # 在后台线程中加载模型
        threading.Thread(target=self._load_model_in_background, daemon=True).start()
    
    def _load_model_in_background(self):
        """在后台线程中加载模型"""
        try:
            executable_path = get_executable_path()
            model_path = config_manager.get('model_path', 'brightness_model.npz')
            
            # 优先从用户数据目录查找模型
            from config import get_user_data_path
            user_data_path = get_user_data_path()
            user_model_path = os.path.join(user_data_path, model_path)
            exe_model_path = os.path.join(executable_path, model_path)
            
            # 确定模型路径优先级：用户数据目录 > exe目录
            if os.path.exists(user_model_path):
                model_full_path = user_model_path
            elif os.path.exists(exe_model_path):
                model_full_path = exe_model_path
            else:
                # 都不存在，使用用户数据目录
                model_full_path = user_model_path
            
            # 尝试加载已保存的模型
            if os.path.exists(model_full_path):
                self.nn = BrightnessNeuralNetwork()
                self.nn.load_model(model_full_path)
                # 检查模型输入大小是否与当前需要的14维特征匹配
                if self.nn.input_size != 14:
                    self.log_info(f"加载的模型输入大小为{self.nn.input_size}，与当前需要的14维特征不匹配，将重新创建模型")
                    # 创建新模型
                    self.nn = BrightnessNeuralNetwork()
                    # 预训练模型
                    self.pretrain_network()
                    # 保存模型到用户数据目录
                    self.nn.save_model(user_model_path)
                    self.log_info("已创建并预训练神经网络模型")
                else:
                    self.log_info("已加载神经网络模型")
            else:
                # 如果模型不存在，创建新模型
                self.nn = BrightnessNeuralNetwork()
                # 预训练模型
                self.pretrain_network()
                # 保存模型到用户数据目录
                os.makedirs(os.path.dirname(user_model_path), exist_ok=True)
                self.nn.save_model(user_model_path)
                self.log_info("已创建并预训练神经网络模型")
            
            # 模型加载完成
            self.model_loading = False
            self.model_loaded = True
            
            # 在主线程中更新UI和启动定时任务
            self.root.after(0, self._on_model_loaded)
            
        except Exception as e:
            self.log_error(f"初始化神经网络失败: {e}")
            # 创建默认模型
            self.nn = BrightnessNeuralNetwork()
            self.model_loading = False
            self.model_loaded = True
            self.root.after(0, self._on_model_loaded)
    
    def _on_model_loaded(self):
        """模型加载完成后的回调（在主线程中执行）"""
        if self.nn:
            self.log_info("神经网络模型加载完成")
            show_startup_notification("神经网络模型加载完成", level='success')
            
            # 更新模型状态标签
            self.model_status_label.config(
                text="模型状态: 已加载",
                foreground="green"
            )
        else:
            self.log_error("神经网络模型加载失败")
            show_startup_notification("神经网络模型加载失败，部分功能可能不可用", level='error')
            
            # 更新模型状态标签为错误状态
            self.model_status_label.config(
                text="模型状态: 加载失败",
                foreground="red"
            )
        
        # 如果自动亮度调整已启用，启动定时任务
        if self.auto_brightness_enabled and self.nn:
            self.log_info("启动自动亮度调整定时任务")
            self.start_auto_adjust_timer()
    
    def init_neural_network(self):
        """初始化神经网络（同步版本，保留用于兼容）"""
        executable_path = get_executable_path()
        model_path = config_manager.get('model_path', 'brightness_model.npz')
        
        # 优先从用户数据目录查找模型
        from config import get_user_data_path
        user_data_path = get_user_data_path()
        user_model_path = os.path.join(user_data_path, model_path)
        exe_model_path = os.path.join(executable_path, model_path)
        
        # 确定模型路径优先级：用户数据目录 > exe目录
        if os.path.exists(user_model_path):
            model_full_path = user_model_path
        elif os.path.exists(exe_model_path):
            model_full_path = exe_model_path
        else:
            # 都不存在，使用用户数据目录
            model_full_path = user_model_path
        
        try:
            # 尝试加载已保存的模型
            if os.path.exists(model_full_path):
                self.nn = BrightnessNeuralNetwork()
                self.nn.load_model(model_full_path)
                # 检查模型输入大小是否与当前需要的14维特征匹配
                if self.nn.input_size != 14:
                    self.log_info(f"加载的模型输入大小为{self.nn.input_size}，与当前需要的14维特征不匹配，将重新创建模型")
                    # 创建新模型
                    self.nn = BrightnessNeuralNetwork()
                    # 预训练模型
                    self.pretrain_network()
                    # 保存模型到用户数据目录
                    self.nn.save_model(user_model_path)
                    self.log_info("已创建并预训练神经网络模型")
                else:
                    self.log_info("已加载神经网络模型")
            else:
                # 如果模型不存在，创建新模型
                self.nn = BrightnessNeuralNetwork()
                # 预训练模型
                self.pretrain_network()
                # 保存模型到用户数据目录
                os.makedirs(os.path.dirname(user_model_path), exist_ok=True)
                self.nn.save_model(user_model_path)
                self.log_info("已创建并预训练神经网络模型")
        except Exception as e:
            self.log_error(f"初始化神经网络失败: {e}")
            # 创建默认模型
            self.nn = BrightnessNeuralNetwork()
    
    def pretrain_network(self):
        """预训练神经网络"""
        try:
            # 使用generate_synthetic_data生成训练数据
            X, y = generate_synthetic_data(num_samples=1000)
            
            # 训练模型，传递log_callback参数
            self.nn.train(X, y, learning_rate=0.001, epochs=1000, batch_size=32, log_callback=self.log_info)
            self.log_info("神经网络预训练完成")
        except Exception as e:
            self.log_error(f"预训练神经网络失败: {e}")
    
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
    
    def on_brightness_change(self, value):
        """亮度滑动条变化回调"""
        brightness = int(float(value))
        self.brightness_label.config(text=f"亮度: {brightness}%")
    
    def apply_brightness(self):
        """应用亮度设置"""
        # 检查按钮点击频率
        if not self.can_click_button("apply_brightness"):
            show_window_notification("操作过于频繁，请稍后再试", position=self.notification_position, level='warning')
            return
        
        # 检查模型是否正在加载
        if self.model_loading:
            self.log_info("模型正在加载中，请稍后再试")
            show_window_notification("模型正在加载中，请稍后再试", position=self.notification_position, level='info')
            return
        
        brightness = self.brightness_var.get()
        try:
            # 检查屏幕是否息屏，如果是则跳过亮度调整
            if not is_monitor_on():
                self.log_info("屏幕处于息屏状态，跳过手动亮度调整")
                show_window_notification("屏幕处于息屏状态，亮度调整已跳过", position=self.notification_position, level='warning')
                return  # 跳出函数，不执行亮度调整
            
            # 检查显示器可用性
            if not self.available_monitors:
                self.log_warning("没有可用的显示器，无法调整亮度")
                show_window_notification("没有可用的显示器", position=self.notification_position, level='warning')
                return
            
            # 获取选中的显示器索引列表
            monitor_indices = self.get_selected_monitor_indices()
            
            if not monitor_indices:
                self.log_warning("没有选中任何显示器，无法调整亮度")
                show_window_notification("请先选择要调整的显示器", position=self.notification_position, level='warning')
                return
            
            success = adjust_brightness(brightness, monitor_indices)
            if success:
                self.log_info(f"亮度已调整为：{brightness}%")
                self.current_brightness = brightness
                # 在所有需要调整的屏幕上显示通知
                for idx in monitor_indices:
                    show_notification(f"亮度已调整为 {brightness}%", position=self.notification_position, screen_mode='custom', screen_index=idx, level='success')
            else:
                error_msg = "调整亮度失败，显示器可能不支持亮度调节"
                self.log_error(error_msg)
                # 在所有需要调整的屏幕上显示错误通知
                for idx in monitor_indices:
                    show_notification(error_msg, position=self.notification_position, screen_mode='custom', screen_index=idx, level='error')
        except Exception as e:
            error_msg = f"调整亮度时出错：{e}"
            self.log_error(error_msg)
            # 显示错误通知
            show_window_notification(error_msg, position=self.notification_position, level='error')
    
    def train_network(self):
        """训练神经网络"""
        # 检查按钮点击频率
        if not self.can_click_button("train_network"):
            show_window_notification("操作过于频繁，请稍后再试", position=self.notification_position, level='warning')
            return
        
        # 检查模型是否正在加载
        if self.model_loading:
            self.log_info("模型正在加载中，请稍后再试")
            show_window_notification("模型正在加载中，请稍后再试", position=self.notification_position, level='info')
            return
        
        if not self.nn:
            self.log_error("神经网络未初始化")
            show_window_notification("模型未加载，无法训练", position=self.notification_position, level='error')
            return
        
        if not self.brightness_data:
            self.log_error("没有足够的亮度数据进行训练")
            show_window_notification("没有亮度数据，请先获取亮度", position=self.notification_position, level='warning')
            return
        
        # 检查显示器可用性（训练需要应用亮度）
        if not self.available_monitors:
            self.log_warning("没有可用的显示器，无法训练")
            show_window_notification("没有可用的显示器，无法训练", position=self.notification_position, level='warning')
            return
        
        # 应用当前亮度设置
        self.apply_brightness()
        
        # 开始训练
        self.log_info("开始训练神经网络...")
        
        # 在后台线程中训练
        threading.Thread(target=self.do_train_network, daemon=True).start()
    
    def do_train_network(self):
        """在后台线程中训练神经网络"""
        try:
            # 准备训练数据
            X = []
            y = []
            
            # 使用最近的亮度数据作为输入
            for data in self.brightness_data:
                X.append(data)
                y.append(self.current_brightness)  # 使用当前设置的亮度作为目标输出
            
            # 训练模型，传递log_callback参数
            self.nn.train(X, y, learning_rate=0.001, epochs=1000, batch_size=32, log_callback=self.log_info)
            
            # 保存模型到用户数据目录
            from config import get_user_data_path
            executable_path = get_executable_path()
            model_path = config_manager.get('model_path', 'brightness_model.npz')
            user_data_path = get_user_data_path()
            user_model_path = os.path.join(user_data_path, model_path)
            
            os.makedirs(os.path.dirname(user_model_path), exist_ok=True)
            self.nn.save_model(user_model_path)
            
            self.log_info("神经网络训练完成并保存")
        except Exception as e:
            self.log_error(f"训练神经网络失败: {e}")
    
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
        """获取亮度数据"""
        self.log_info("正在获取亮度数据...")
        self.root.after(0, lambda: show_window_notification("正在获取亮度...", position=self.notification_position, level='info'))
        try:
            # 检查设备可用性
            if not self._check_devices_available():
                self.root.after(0, lambda: show_window_notification("摄像头不可用，无法获取亮度", position=self.notification_position, level='error'))
                return
            
            # 获取选中的摄像头索引
            camera_index = self.get_selected_camera_index()
            
            if camera_index is None:
                self.log_error("无效的摄像头选择")
                self.root.after(0, lambda: show_window_notification("摄像头不可用，无法获取亮度", position=self.notification_position, level='error'))
                return
            
            # 捕获图像帧，减少帧数以提升性能
            frames = capture_frames_from_camera(camera_index=camera_index, num_frames=10)
            
            if not frames:
                self.log_error("无法从摄像头捕获图像，可能摄像头被占用或已断开")
                self.root.after(0, lambda: show_window_notification("无法获取摄像头图像", position=self.notification_position, level='error'))
                return
            
            # 计算当前亮度和其他特征，返回(current_brightness, features)元组
            current_brightness, features = calculate_average_brightness(frames)
            
            # 缓存亮度数据（线程安全）
            with self.data_lock:
                self.brightness_data.append(features)
                # 限制缓存大小，避免内存占用过大
                if len(self.brightness_data) > 100:
                    self.brightness_data = self.brightness_data[-100:]
            
            # 使用神经网络预测亮度（线程安全）
            with self.nn_lock:
                if self.nn:
                    predicted_brightness = self.nn.forward(features)
                    predicted_brightness = int(predicted_brightness)
                else:
                    predicted_brightness = None
            
            if predicted_brightness is not None:
                # 更新滑动条位置
                self.brightness_var.set(predicted_brightness)
                self.brightness_label.config(text=f"亮度: {predicted_brightness}%")
                
                self.log_info(f"当前环境亮度: {current_brightness}")
                self.log_info(f"神经网络推荐亮度: {predicted_brightness}%")
            else:
                self.log_error("神经网络未初始化")
                self.root.after(0, lambda: show_window_notification("模型未加载，无法预测亮度", position=self.notification_position, level='error'))
        except Exception as e:
            self.log_error(f"获取亮度数据失败：{e}")
            self.root.after(0, lambda: show_window_notification(f"获取亮度失败：{e}", position=self.notification_position, level='error'))
    
    def toggle_auto_brightness(self):
        """切换自动亮度调整"""
        self.auto_brightness_enabled = not self.auto_brightness_enabled
        config_manager.set('auto_brightness', self.auto_brightness_enabled)
        
        if self.auto_brightness_enabled:
            self.log_info("已启用自动亮度调整")
            # 启动自动调整定时任务
            self.start_auto_adjust_timer()
        else:
            self.log_info("已禁用自动亮度调整")
            # 停止自动调整定时任务
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
            
            # 创建并启动线程
            thread = threading.Thread(target=run_adjust, daemon=True)
            thread.start()
        
        # 使用配置的间隔执行
        self.auto_adjust_timer = threading.Timer(self.check_interval, auto_adjust)
        self.auto_adjust_timer.daemon = True
        self.auto_adjust_timer.start()
    
    def stop_auto_adjust_timer(self):
        """停止自动调整定时任务"""
        if self.auto_adjust_timer:
            self.auto_adjust_timer.cancel()
            self.auto_adjust_timer = None
    
    def auto_adjust_brightness(self):
        """自动调整亮度"""
        try:
            # 检查屏幕是否息屏，如果是则跳过亮度调整
            if not is_monitor_on():
                self.log_info("屏幕处于息屏状态，跳过亮度调整")
                return  # 跳出函数，不执行后续的亮度调整
            
            # 检查设备可用性
            if not self._check_devices_available():
                return
            
            # 显示调整开始通知 - 在主线程执行
            self.root.after(0, lambda: show_window_notification("正在自动调整亮度...", position=self.notification_position, level='info'))
            # 设置超时机制，避免长时间阻塞
            def run_with_timeout():
                try:
                    # 获取选中的摄像头索引
                    camera_index = self.get_selected_camera_index()
                    
                    # 验证摄像头索引有效性
                    if camera_index is None:
                        self._handle_auto_adjust_failure("摄像头不可用，已跳过本次调整")
                        return
                    
                    # 获取亮度数据，减少帧数以提升性能
                    frames = capture_frames_from_camera(camera_index=camera_index, num_frames=10)
                    
                    if not frames:
                        self._handle_auto_adjust_failure("无法从摄像头获取图像，可能摄像头被占用或已断开")
                        return
                    
                    # 计算当前亮度和其他特征，返回(current_brightness, features)元组
                    current_brightness, features = calculate_average_brightness(frames)
                    
                    # 缓存亮度数据（线程安全）
                    with self.data_lock:
                        self.brightness_data.append(features)
                        # 限制缓存大小
                        if len(self.brightness_data) > 100:
                            self.brightness_data = self.brightness_data[-100:]
                    
                    # 使用神经网络预测亮度（线程安全）
                    with self.nn_lock:
                        if not self.nn:
                            self._handle_auto_adjust_failure("神经网络模型未加载，无法预测亮度")
                            return
                        predicted_brightness = self.nn.forward(features)
                        predicted_brightness = int(predicted_brightness)
                    
                    # 获取选中的显示器索引列表（线程安全）
                    with self.monitor_lock:
                        monitor_indices = self.get_selected_monitor_indices()
                    
                    if not monitor_indices:
                        self._handle_auto_adjust_failure("没有可用的显示器，已跳过亮度调整")
                        return
                    
                    success = adjust_brightness(predicted_brightness, monitor_indices)
                    if success:
                        # 在所有需要调整的屏幕上显示通知
                        indices_copy = list(monitor_indices)
                        brightness_val = predicted_brightness
                        notification_pos = self.notification_position
                        self.root.after(0, lambda: [
                            show_notification(
                                f"亮度已自动调整为 {brightness_val}%", 
                                position=notification_pos,
                                screen_mode='custom', 
                                screen_index=idx, 
                                level='success'
                            )
                            for idx in indices_copy
                        ])
                        self.log_info(f"自动调整亮度为：{predicted_brightness}%")
                    else:
                        self._handle_auto_adjust_failure("自动调整亮度失败，显示器可能不支持亮度调节")
                except Exception as e:
                    error_msg = f"自动调整亮度线程执行出错: {e}"
                    self._handle_auto_adjust_failure(error_msg)
            
            # 创建并启动线程
            thread = threading.Thread(target=run_with_timeout, daemon=True)
            thread.start()
            
            # 等待线程完成，设置超时
            thread.join(timeout=30)  # 30秒超时
            
            if thread.is_alive():
                error_msg = "自动调整亮度线程超时"
                self.log_error(error_msg)
                # 显示错误通知 - 在主线程执行
                self.root.after(0, lambda: show_window_notification(error_msg, position=self.notification_position, level='error'))
                
        except Exception as e:
            # 忽略错误，确保程序稳定性
            error_msg = f"自动调整亮度时出错：{e}"
            self.log_error(error_msg)
            # 显示错误通知 - 在主线程执行
            self.root.after(0, lambda: show_window_notification(error_msg, position=self.notification_position, level='error'))
    
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
    
    def refresh_camera_list(self, restore_selection=True, force_refresh=False):
        """刷新摄像头列表
        
        Args:
            restore_selection: 是否恢复上次选择的摄像头
            force_refresh: 是否强制刷新摄像头列表
        """
        try:
            # 获取可用的摄像头列表
            self.available_cameras = get_available_cameras(force_refresh=force_refresh)
            
            # 更新下拉框选项
            camera_names = [cam['name'] for cam in self.available_cameras]
            self.camera_combo['values'] = camera_names
            
            # 设置选中项
            if camera_names:
                if restore_selection:
                    # 尝试恢复上次选择的摄像头
                    saved_camera_name = config_manager.get('selected_camera_name')
                    if saved_camera_name and saved_camera_name in camera_names:
                        self.camera_var.set(saved_camera_name)
                        self.log_info(f"已恢复上次选择的摄像头: {saved_camera_name}")
                    else:
                        # 保存的设备不存在或无记录，使用第一个
                        self.camera_var.set(camera_names[0])
                        if saved_camera_name:
                            self.log_warning(f"保存的摄像头 '{saved_camera_name}' 不可用，已使用默认摄像头")
                else:
                    self.camera_var.set(camera_names[0])
            
            self.log_info(f"已刷新摄像头列表，找到 {len(self.available_cameras)} 个摄像头")
        except Exception as e:
            self.log_error(f"刷新摄像头列表失败：{e}")
    
    def on_refresh_cameras(self):
        """手动刷新摄像头列表"""
        if not self.can_click_button("refresh_cameras"):
            show_window_notification("操作过于频繁，请稍后再试", position=self.notification_position, level='warning')
            return
        
        self.log_info("正在刷新摄像头列表...")
        self.refresh_camera_list(force_refresh=True)
        show_window_notification("摄像头列表已刷新", position=self.notification_position, level='success')
    
    def refresh_monitor_list(self, restore_selection=True, force_refresh=False):
        """刷新显示器列表
        
        Args:
            restore_selection: 是否恢复上次选择的显示器
            force_refresh: 是否强制刷新显示器列表
        """
        try:
            # 获取可用的显示器列表
            self.available_monitors = get_available_monitors(force_refresh=force_refresh)
            
            # 清除现有的复选框
            for widget in self.monitor_checkboxes_frame.winfo_children():
                widget.destroy()
            self.monitor_vars.clear()
            
            # 获取上次保存的选择
            saved_monitor_names = []
            if restore_selection:
                saved_monitor_names = config_manager.get('selected_monitor_names') or []
            
            # 创建新的复选框
            for monitor in self.available_monitors:
                # 默认选中所有显示器
                is_selected = True
                if restore_selection and saved_monitor_names:
                    # 如果保存的选择中有这个显示器，恢复选中状态
                    is_selected = monitor['name'] in saved_monitor_names
                
                var = tk.BooleanVar(value=is_selected)
                self.monitor_vars[monitor['index']] = var
                
                # 绑定选择变化事件
                var.trace_add('write', lambda *args: self.on_monitor_change())
                
                cb = ttk.Checkbutton(
                    self.monitor_checkboxes_frame,
                    text=monitor['name'],
                    variable=var
                )
                cb.pack(anchor=tk.W)
            
            # 检查是否有保存的显示器不可用
            current_names = [m['name'] for m in self.available_monitors]
            if restore_selection and saved_monitor_names:
                missing = [name for name in saved_monitor_names if name not in current_names]
                if missing:
                    self.log_warning(f"保存的显示器不可用: {', '.join(missing)}")
                    # 显示通知提醒用户
                    self.root.after(1000, lambda: show_window_notification(
                        "部分显示器已断开连接，已自动调整选择",
                        position=self.notification_position,
                        level='warning'
                    ))
            
            # 记录当前显示器列表用于热插拔检测
            self.last_monitor_names = current_names
            
            self.log_info(f"已刷新显示器列表，找到 {len(self.available_monitors)} 个显示器")
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
    
    def on_camera_change(self, event):
        """摄像头选择变化回调"""
        selected_name = self.camera_var.get()
        config_manager.set('selected_camera_name', selected_name)
        self.log_info(f"已选择摄像头: {selected_name}")
    
    def on_monitor_change(self):
        """显示器选择变化回调"""
        selected_names = []
        for monitor in self.available_monitors:
            if self.monitor_vars.get(monitor['index'], tk.BooleanVar(value=False)).get():
                selected_names.append(monitor['name'])
        config_manager.set('selected_monitor_names', selected_names)
        self.log_info(f"已选择显示器: {', '.join(selected_names) if selected_names else '无'}")
    
    def check_device_hotplug(self):
        """检查设备（摄像头和显示器）是否发生热插拔变化"""
        # 防抖检查：避免频繁刷新
        current_time = time.time()
        if current_time - self.last_hotplug_refresh_time < self.hotplug_debounce_interval:
            return False
        
        device_changed = False
        
        # 已废弃：改为按需检测
        # 只在需要时（调整亮度或手动刷新）才检测
    
    def _handle_selected_camera_unavailable(self, removed_cameras):
        """处理选中的摄像头不可用的情况"""
        current_selected = self.camera_var.get()
        if current_selected in removed_cameras:
            self.log_warning(f"当前选中的摄像头 '{current_selected}' 已断开，自动切换")
            # 切换到第一个可用的摄像头
            if self.available_cameras:
                new_camera = self.available_cameras[0]['name']
                self.camera_var.set(new_camera)
                config_manager.set('selected_camera_name', new_camera)
                show_window_notification(f"摄像头已断开，已自动切换到：{new_camera}", position=self.notification_position, level='warning')
            else:
                # 没有任何可用摄像头
                self.camera_var.set('')
                show_window_notification("所有摄像头已断开，无法获取环境亮度", position=self.notification_position, level='error')
    
    def _handle_selected_monitors_unavailable(self, removed_monitors):
        """处理选中的显示器不可用的情况"""
        # 检查当前选中的显示器是否有断开的
        selected_names = []
        for monitor in self.available_monitors:
            if self.monitor_vars.get(monitor['index'], tk.BooleanVar(value=False)).get():
                selected_names.append(monitor['name'])
        
        # 如果所有选中的显示器都断开了，或者没有选中任何显示器
        if not selected_names or all(name in removed_monitors for name in selected_names):
            self.log_warning("当前选中的显示器已断开，自动切换到可用显示器")
            # 选中所有可用的显示器
            for monitor in self.available_monitors:
                if monitor['index'] in self.monitor_vars:
                    self.monitor_vars[monitor['index']].set(True)
            
            # 更新配置
            new_selected = [m['name'] for m in self.available_monitors]
            config_manager.set('selected_monitor_names', new_selected)
            
            if new_selected:
                show_window_notification(f"显示器已断开，已自动选择：{', '.join(new_selected)}", position=self.notification_position, level='warning')
            else:
                show_window_notification("所有显示器已断开，无法调整亮度", position=self.notification_position, level='error')
    
    def start_hotplug_detection(self):
        """启动热插拔检测定时器"""
        # 显示器热插拔检测已移除，改为按需检测
        # 只在需要调整亮度或手动刷新时才检测显示器变化
        self.last_camera_names = [c['name'] for c in self.available_cameras]
        self.last_monitor_names = [m['name'] for m in self.available_monitors]
        
        self.log_info("热插拔检测已禁用（改为按需检测）")
    
    def check_camera_hotplug_if_needed(self):
        """检查摄像头是否需要热插拔检测（只在需要时检测）
        
        当选中的摄像头不可用时，才进行热插拔检测
        """
        selected_camera = self.camera_var.get()
        
        # 如果选中的摄像头不在当前列表中，说明可能需要刷新
        if selected_camera and self.available_cameras:
            if selected_camera not in [c['name'] for c in self.available_cameras]:
                # 选中的摄像头不可用，刷新列表
                self.log_info("检测到摄像头可能已断开，正在刷新列表...")
                self.refresh_camera_list(restore_selection=False, force_refresh=True)
                
                # 如果刷新后还是没有找到选中的摄像头
                if selected_camera not in [c['name'] for c in self.available_cameras]:
                    self.log_warning(f"摄像头 '{selected_camera}' 已断开")
                    show_window_notification(f"摄像头已断开：{selected_camera}", position=self.notification_position, level='warning')
                    # 自动切换到第一个可用摄像头
                    if self.available_cameras:
                        new_camera = self.available_cameras[0]['name']
                        self.camera_var.set(new_camera)
                        config_manager.set('selected_camera_name', new_camera)
                        show_window_notification(f"已自动切换到：{new_camera}", position=self.notification_position, level='info')
                    else:
                        self.camera_var.set('')
                        show_window_notification("所有摄像头已断开，无法获取环境亮度", position=self.notification_position, level='error')
        elif not self.available_cameras:
            # 没有任何摄像头，尝试刷新
            self.refresh_camera_list(restore_selection=False, force_refresh=True)
    
    def check_monitor_hotplug_if_needed(self):
        """检查显示器是否需要热插拔检测（只在需要时检测）
        
        当选中的显示器不可用时，才进行热插拔检测
        """
        # 获取当前选中的显示器名称
        selected_names = []
        for monitor in self.available_monitors:
            if self.monitor_vars.get(monitor['index'], tk.BooleanVar(value=False)).get():
                selected_names.append(monitor['name'])
        
        # 检查是否有选中的显示器不在当前列表中
        if selected_names and self.available_monitors:
            current_monitor_names = [m['name'] for m in self.available_monitors]
            unavailable_monitors = [name for name in selected_names if name not in current_monitor_names]
            
            if unavailable_monitors:
                # 有显示器不可用，刷新列表
                self.log_info("检测到显示器可能已断开，正在刷新列表...")
                self.refresh_monitor_list(restore_selection=True, force_refresh=True)
                
                # 通知用户
                show_window_notification(f"显示器已断开：{', '.join(unavailable_monitors)}", position=self.notification_position, level='warning')
                
                # 自动切换到可用的显示器
                self._handle_selected_monitors_unavailable(unavailable_monitors)
        elif not self.available_monitors:
            # 没有任何显示器，尝试刷新
            self.refresh_monitor_list(restore_selection=False)
    
    def get_selected_camera_index(self):
        """获取选中的摄像头索引
        
        Returns:
            int or None: 摄像头索引，如果没有可用摄像头或选中无效则返回 None
        """
        selected_name = self.camera_var.get()
        if not selected_name:
            return None
        
        for cam in self.available_cameras:
            if cam['name'] == selected_name:
                return cam['index']
        
        return None  # 选中的摄像头不在可用列表中
    
    def get_selected_monitor_indices(self):
        """获取选中的显示器索引列表"""
        selected_indices = []
        for monitor in self.available_monitors:
            if self.monitor_vars.get(monitor['index'], tk.BooleanVar(value=False)).get():
                selected_indices.append(monitor['index'])
        
        # 如果没有选中任何显示器，默认使用第一个
        if not selected_indices and self.available_monitors:
            selected_indices.append(0)
        
        return selected_indices
    
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