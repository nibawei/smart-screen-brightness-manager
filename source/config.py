import json
import os
import sys

# 获取可执行文件路径
def get_executable_path():
    if getattr(sys, 'frozen', False):
        # 打包后的可执行文件路径
        return os.path.dirname(sys.executable)
    else:
        # 开发环境中的脚本路径
        return os.path.dirname(os.path.abspath(__file__))

# 获取用户数据目录
def get_user_data_path():
    """获取用户数据目录，确保有写入权限"""
    try:
        # 使用AppData目录（Windows）
        app_data = os.path.join(os.path.expanduser("~"), "AppData", "Local", "BrightnessManager")
        if not os.path.exists(app_data):
            os.makedirs(app_data, exist_ok=True)
        return app_data
    except:
        # 降级到可执行文件目录
        return get_executable_path()

class ConfigManager:
    """配置管理类，用于读写应用配置"""
    
    def __init__(self, config_file='config.json'):
        """初始化配置管理器
        
        Args:
            config_file: 配置文件路径
        """
        # 优先使用用户数据目录（有写入权限）
        # 如果用户数据目录中有配置文件，使用它
        # 否则使用可执行文件目录中的配置文件（用于读取默认配置）
        self.config_filename = config_file
        self.user_config_path = os.path.join(get_user_data_path(), config_file)
        self.exe_config_path = os.path.join(get_executable_path(), config_file)
        
        # 优先使用用户目录的配置文件
        if os.path.exists(self.user_config_path):
            self.config_file = self.user_config_path
        else:
            # 如果用户目录没有配置，使用exe目录的配置（如果存在）
            if os.path.exists(self.exe_config_path):
                self.config_file = self.exe_config_path
            else:
                # 都不存在，使用用户目录
                self.config_file = self.user_config_path
        
        self.default_config = {
            'auto_brightness': False,  # 是否自动调整亮度
            'startup': False,  # 是否开机自启动
            'model_path': 'brightness_model.npz',  # 神经网络模型路径（相对于可执行文件）
            'log_level': 'info',  # 日志级别
            'check_interval': 300,  # 循环检查间隔（秒），默认 5 分钟，范围 60 秒到 86400 秒
            'selected_camera_name': None,  # 上次选择的摄像头名称
            'selected_monitor_names': None,  # 上次选择的显示器名称列表
            'notification_position': 'bottom'  # 通知显示位置：'top'（顶部）或 'bottom'（右下）
        }
        self.config = self.load_config()
    
    def load_config(self):
        """加载配置文件
        
        Returns:
            dict: 配置字典
        """
        # 尝试从用户配置路径加载
        if os.path.exists(self.user_config_path):
            try:
                with open(self.user_config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 合并默认配置和加载的配置
                    for key, value in self.default_config.items():
                        if key not in config:
                            config[key] = value
                    return config
            except Exception as e:
                print(f"加载用户配置文件失败: {e}")
        
        # 尝试从exe配置路径加载（作为备份）
        if os.path.exists(self.exe_config_path):
            try:
                with open(self.exe_config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 合并默认配置和加载的配置
                    for key, value in self.default_config.items():
                        if key not in config:
                            config[key] = value
                    return config
            except Exception as e:
                print(f"加载exe配置文件失败: {e}")
        
        # 都失败，返回默认配置
        return self.default_config.copy()
    
    def save_config(self):
        """保存配置到文件
        
        Returns:
            bool: 保存是否成功
        """
        try:
            # 确保用户数据目录存在
            os.makedirs(os.path.dirname(self.user_config_path), exist_ok=True)
            
            # 保存到用户数据目录
            with open(self.user_config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            
            # 更新当前配置文件路径为用户目录
            self.config_file = self.user_config_path
            return True
        except Exception as e:
            print(f"保存配置文件失败: {e}")
            return False
    
    def get(self, key, default=None):
        """获取配置项
        
        Args:
            key: 配置项键名
            default: 默认值
            
        Returns:
            配置项值
        """
        return self.config.get(key, default)
    
    def set(self, key, value):
        """设置配置项
        
        Args:
            key: 配置项键名
            value: 配置项值
        """
        self.config[key] = value
        self.save_config()

# 创建全局配置管理器实例
config_manager = ConfigManager()
