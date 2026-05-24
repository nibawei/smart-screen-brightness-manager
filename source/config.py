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

class ConfigManager:
    """配置管理类，用于读写应用配置"""
    
    def __init__(self, config_file='config.json'):
        """初始化配置管理器
        
        Args:
            config_file: 配置文件路径
        """
        # 使用可执行文件所在目录的路径
        executable_path = get_executable_path()
        self.config_file = os.path.join(executable_path, config_file)
        self.default_config = {
            'auto_brightness': False,  # 是否自动调整亮度
            'startup': False,  # 是否开机自启动
            'model_path': 'brightness_model.npz',  # 神经网络模型路径（相对于可执行文件）
            'log_level': 'info'  # 日志级别
        }
        self.config = self.load_config()
    
    def load_config(self):
        """加载配置文件
        
        Returns:
            dict: 配置字典
        """
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 合并默认配置和加载的配置
                    for key, value in self.default_config.items():
                        if key not in config:
                            config[key] = value
                    return config
            except Exception as e:
                print(f"加载配置文件失败: {e}")
                return self.default_config.copy()
        else:
            # 如果配置文件不存在，返回默认配置
            return self.default_config.copy()
    
    def save_config(self):
        """保存配置到文件
        
        Returns:
            bool: 保存是否成功
        """
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
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
