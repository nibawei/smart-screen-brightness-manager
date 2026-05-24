import os
import sys
import ctypes
import winreg

class SystemUtils:
    """系统工具类，提供系统相关的工具函数"""
    
    @staticmethod
    def show_notification(title, message, duration=5):
        """显示系统通知
        
        Args:
            title: 通知标题
            message: 通知内容
            duration: 通知显示时间（秒）
        """
        try:
            # 使用内置的ToastNotifier显示通知
            toaster = ToastNotifier()
            toaster.show_toast(title, message, duration=duration)
        except Exception as e:
            print(f"显示通知失败: {e}")
    
    @staticmethod
    def set_startup(enable):
        """设置开机自启动
        
        Args:
            enable: 是否启用开机自启动
            
        Returns:
            bool: 设置是否成功
        """
        try:
            # 获取当前脚本的路径
            script_path = os.path.abspath(sys.argv[0])
            script_name = os.path.basename(script_path)
            
            # 打开注册表项
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
            
            if enable:
                # 设置开机自启动
                winreg.SetValueEx(key, script_name, 0, winreg.REG_SZ, script_path)
            else:
                # 移除开机自启动
                try:
                    winreg.DeleteValue(key, script_name)
                except FileNotFoundError:
                    # 如果值不存在，忽略错误
                    pass
            
            winreg.CloseKey(key)
            return True
        except Exception as e:
            print(f"设置开机自启动失败: {e}")
            return False
    
    @staticmethod
    def get_startup():
        """获取开机自启动状态
        
        Returns:
            bool: 当前是否启用了开机自启动
        """
        try:
            # 获取当前脚本的路径
            script_path = os.path.abspath(sys.argv[0])
            script_name = os.path.basename(script_path)
            
            # 打开注册表项
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE)
            
            # 尝试获取值
            try:
                value, _ = winreg.QueryValueEx(key, script_name)
                # 检查值是否正确
                result = value == script_path
            except FileNotFoundError:
                # 如果值不存在，返回False
                result = False
            
            winreg.CloseKey(key)
            return result
        except Exception as e:
            print(f"获取开机自启动状态失败: {e}")
            return False
    
    @staticmethod
    def is_admin():
        """检查当前是否以管理员权限运行
        
        Returns:
            bool: 是否以管理员权限运行
        """
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    
    @staticmethod
    def run_as_admin():
        """以管理员权限重新运行程序
        
        Returns:
            bool: 是否成功以管理员权限运行
        """
        try:
            # 获取当前脚本的路径
            script_path = os.path.abspath(sys.argv[0])
            
            # 以管理员权限重新运行
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, script_path, None, 1
            )
            return True
        except Exception as e:
            print(f"以管理员权限运行失败: {e}")
            return False
    
    @staticmethod
    def get_app_data_path():
        """获取应用数据路径
        
        Returns:
            str: 应用数据路径
        """
        app_data_path = os.path.join(os.path.expanduser("~"), "AppData", "Local", "BrightnessManager")
        if not os.path.exists(app_data_path):
            os.makedirs(app_data_path)
        return app_data_path

# 确保win10toast已安装
class ToastNotifier:
    """简单的通知类，用于在win10toast未安装时提供基本功能"""
    def show_toast(self, title, message, duration=5):
        """显示通知
        
        Args:
            title: 通知标题
            message: 通知内容
            duration: 通知显示时间（秒）
        """
        print(f"通知: {title} - {message}")

# 尝试导入win10toast
# 注意：在实际使用中，应该在安装依赖时添加win10toast
# 这里为了简化，我们先使用内置的ToastNotifier类
