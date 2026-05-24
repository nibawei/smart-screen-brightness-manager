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

# 获取可执行文件路径
def get_executable_path():
    if getattr(sys, 'frozen', False):
        # 打包后的可执行文件路径
        return os.path.dirname(sys.executable)
    else:
        # 开发环境中的脚本路径
        return os.path.dirname(os.path.abspath(__file__))

def is_monitor_on():
    """
    Check if the monitor is currently on by querying the power state
    Returns True if monitor is on, False if it's off/sleeping
    """
    try:
        # Method 1: Try using WMI to check monitor status
        try:
            pythoncom.CoInitialize()  # Initialize COM for this thread
            wmi_obj = wmi.WMI(namespace='root\\wmi')
            # Query the monitor power state
            monitor_states = wmi_obj.WmiMonitorBasicDisplayParams()
            for state in monitor_states:
                # Power state: 1=ON, 2=STANDBY, 3=SUSPEND, 4=OFF
                if hasattr(state, 'PowerState'):
                    return state.PowerState == 1
            # If no specific state found, assume monitor is on
            return True
        except Exception:
            # Method 2: Alternative approach using Windows API
            # We can try to send a message to check if the monitor responds
            # However, since we can't reliably detect if monitor is off via API,
            # we'll return True as a fallback to prevent disabling brightness adjustments
            return True
    except Exception:
        # If all methods fail, assume monitor is on to maintain functionality
        return True

from liangdu import BrightnessNeuralNetwork, capture_frames_from_camera, calculate_average_brightness, adjust_brightness, generate_synthetic_data
from config import config_manager
from utils import SystemUtils, ToastNotifier
from notification import Notification

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
            # 尝试加载图标
            if hasattr(sys, '_MEIPASS'):
                # 打包后，资源会被提取到_MEIPASS目录
                icon_path = os.path.join(sys._MEIPASS, "favicon.ico")
            else:
                # 开发环境中，直接使用当前目录
                icon_path = os.path.join(get_executable_path(), "favicon.ico")
            
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
        
        # 初始化系统托盘
        self.tray = None
        
        # 初始化定时任务
        self.auto_adjust_timer = None
        
        # 创建GUI
        self.create_gui()
        
        # 初始化神经网络
        self.init_neural_network()
        
        # 启动系统托盘
        self.setup_tray()
        
        # 隐藏主窗口，只显示系统托盘
        self.root.withdraw()
        
        # 显示启动通知
        Notification("亮度管理器已启动", position='top')
        
        # 如果自动亮度调整已启用，启动定时任务
        if self.auto_brightness_enabled:
            self.start_auto_adjust_timer()
    
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
    
    def init_neural_network(self):
        """初始化神经网络"""
        executable_path = get_executable_path()
        model_path = config_manager.get('model_path', 'brightness_model.npz')
        model_path = os.path.join(executable_path, model_path)
        
        try:
            # 尝试加载已保存的模型
            if os.path.exists(model_path):
                self.nn = BrightnessNeuralNetwork()
                self.nn.load_model(model_path)
                # 检查模型输入大小是否与当前需要的14维特征匹配
                if self.nn.input_size != 14:
                    self.log_info(f"加载的模型输入大小为{self.nn.input_size}，与当前需要的14维特征不匹配，将重新创建模型")
                    # 创建新模型
                    self.nn = BrightnessNeuralNetwork()
                    # 预训练模型
                    self.pretrain_network()
                    # 保存模型
                    self.nn.save_model(model_path)
                    self.log_info("已创建并预训练神经网络模型")
                else:
                    self.log_info("已加载神经网络模型")
            else:
                # 如果模型不存在，创建新模型
                self.nn = BrightnessNeuralNetwork()
                # 预训练模型
                self.pretrain_network()
                # 保存模型
                self.nn.save_model(model_path)
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
                # 尝试从打包后的资源中加载图标
                if hasattr(sys, '_MEIPASS'):
                    # 打包后，资源会被提取到_MEIPASS目录
                    icon_path = os.path.join(sys._MEIPASS, "favicon.ico")
                else:
                    # 开发环境中，直接使用当前目录
                    icon_path = os.path.join(get_executable_path(), "favicon.ico")
                
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
            
            # 启动系统托盘
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception as e:
            self.log_error(f"设置系统托盘失败: {e}")
    
    def show_gui(self):
        """显示GUI界面"""
        self.root.deiconify()
        self.root.lift()
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
        brightness = self.brightness_var.get()
        try:
            # 检查屏幕是否息屏，如果是则跳过亮度调整
            if not is_monitor_on():
                self.log_info("屏幕处于息屏状态，跳过手动亮度调整")
                Notification("屏幕处于息屏状态，亮度调整已跳过", position='top')
                return  # 跳出函数，不执行亮度调整
            
            # 显示调整开始通知
            # Notification(f"正在调整亮度至 {brightness}%", position='top')

            success = adjust_brightness(brightness)
            if success:
                self.log_info(f"亮度已调整为: {brightness}%")
                self.current_brightness = brightness
                # 显示调整完成通知
                Notification(f"亮度已调整为 {brightness}%", position='top')
            else:
                error_msg = "调整亮度失败"
                self.log_error(error_msg)
                # 显示错误通知
                Notification(error_msg, position='top')
        except Exception as e:
            error_msg = f"调整亮度时出错: {e}"
            self.log_error(error_msg)
            # 显示错误通知
            Notification(error_msg, position='top')
    
    def train_network(self):
        """训练神经网络"""
        if not self.nn:
            self.log_error("神经网络未初始化")
            return
        
        if not self.brightness_data:
            self.log_error("没有足够的亮度数据进行训练")
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
            
            # 保存模型
            executable_path = get_executable_path()
            model_path = config_manager.get('model_path', 'brightness_model.npz')
            model_path = os.path.join(executable_path, model_path)
            self.nn.save_model(model_path)
            
            self.log_info("神经网络训练完成并保存")
        except Exception as e:
            self.log_error(f"训练神经网络失败: {e}")
    
    def on_get_brightness_click(self):
        """获取亮度按钮点击回调"""
        threading.Thread(target=self.get_brightness_data, daemon=True).start()

    def get_brightness_data(self):
        """获取亮度数据"""
        self.log_info("正在获取亮度数据...")
        try:
            # 捕获图像帧，减少帧数以提升性能
            frames = capture_frames_from_camera(num_frames=10)
            
            if frames:
                # 计算当前亮度和其他特征，返回(current_brightness, features)元组
                current_brightness, features = calculate_average_brightness(frames)
                
                # 缓存亮度数据
                self.brightness_data.append(features)
                # 限制缓存大小，避免内存占用过大
                if len(self.brightness_data) > 100:
                    self.brightness_data = self.brightness_data[-100:]
                
                # 使用神经网络预测亮度
                if self.nn:
                    predicted_brightness = self.nn.forward(features)
                    predicted_brightness = int(predicted_brightness)
                    
                    # 更新滑动条位置
                    self.brightness_var.set(predicted_brightness)
                    self.brightness_label.config(text=f"亮度: {predicted_brightness}%")
                    
                    self.log_info(f"当前环境亮度: {current_brightness}")
                    self.log_info(f"神经网络推荐亮度: {predicted_brightness}%")
                else:
                    self.log_error("神经网络未初始化")
            else:
                self.log_error("无法捕获图像帧")
        except Exception as e:
            self.log_error(f"获取亮度数据失败: {e}")
    
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
            
            # 显示调整开始通知 - 在主线程执行
            self.root.after(0, lambda: Notification(f"正在自动调整亮度...", position='top'))
            # 设置超时机制，避免长时间阻塞
            def run_with_timeout():
                try:
                    # 获取亮度数据，减少帧数以提升性能
                    frames = capture_frames_from_camera(num_frames=10)
                    
                    if frames and self.nn:
                        # 计算当前亮度和其他特征，返回(current_brightness, features)元组
                        current_brightness, features = calculate_average_brightness(frames)
                        
                        # 缓存亮度数据
                        self.brightness_data.append(features)
                        # 限制缓存大小
                        if len(self.brightness_data) > 100:
                            self.brightness_data = self.brightness_data[-100:]
                        
                        # 使用神经网络预测亮度
                        predicted_brightness = self.nn.forward(features)
                        predicted_brightness = int(predicted_brightness)
                        
                        # 调整亮度
                        success = adjust_brightness(predicted_brightness)
                        if success:
                            # 使用Tkinter的after方法在主线程显示通知
                            self.root.after(0, lambda: Notification(f"亮度已自动调整为 {predicted_brightness}%", position='top'))
                            self.log_info(f"自动调整亮度为: {predicted_brightness}%")
                        else:
                            error_msg = "自动调整亮度失败"
                            self.log_error(error_msg)
                            # 使用Tkinter的after方法在主线程显示通知
                            self.root.after(0, lambda: Notification(error_msg, position='top'))
                except Exception as e:
                    error_msg = f"自动调整亮度线程执行出错: {e}"
                    self.log_error(error_msg)
                    # 使用Tkinter的after方法在主线程显示通知
                    self.root.after(0, lambda: Notification(error_msg, position='top'))
            
            # 创建并启动线程
            thread = threading.Thread(target=run_with_timeout, daemon=True)
            thread.start()
            
            # 等待线程完成，设置超时
            thread.join(timeout=30)  # 30秒超时
            
            if thread.is_alive():
                error_msg = "自动调整亮度线程超时"
                self.log_error(error_msg)
                # 显示错误通知 - 在主线程执行
                self.root.after(0, lambda: Notification(error_msg, position='top'))
                
        except Exception as e:
            # 忽略错误，确保程序稳定性
            error_msg = f"自动调整亮度时出错: {e}"
            self.log_error(error_msg)
            # 显示错误通知 - 在主线程执行
            self.root.after(0, lambda: Notification(error_msg, position='top'))
    
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
            # 使用Tkinter的after方法在主线程显示通知
            self.root.after(0, lambda: Notification("设置开机自启动失败", position='top'))
    
    def on_interval_change(self, event):
        """循环检查间隔变化回调"""
        selected_text = self.interval_var.get()
        for seconds, text in self.interval_options:
            if text == selected_text:
                self.check_interval = seconds
                config_manager.set('check_interval', self.check_interval)
                self.log_info(f"循环检查间隔已设置为: {text}")
                # 如果自动亮度调整已启用，重启定时任务
                if self.auto_brightness_enabled:
                    self.start_auto_adjust_timer()
                break
    
    def log_info(self, message):
        """记录信息"""
        self.log(message, "INFO")
    
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
            if pid == 0:
                continue  # 跳过无效进程ID
            
            try:
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
                                win32api.CloseHandle(handle)
                                return False
                    finally:
                        win32api.CloseHandle(handle)
            except Exception:
                # 忽略无法访问的进程
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
    # 检查是否为单实例运行
    if not is_single_instance():
        print("应用程序已经在运行中！")
        # 直接显示通知，因为此时还没有创建Tkinter实例
        Notification(f"应用程序已经在运行中！", position='top')
        sys.exit(0)
    
    # 显示启动通知
    Notification("正在启动亮度管理器", position='top')
    # 创建并运行应用
    app = BrightnessManagerApp()
    app.run()