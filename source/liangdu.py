import numpy as np
import cv2
import wmi
import pythoncom
import time

# 摄像头列表缓存
_camera_cache = {
    'cameras': [],
    'timestamp': 0,
    'cache_duration': 60  # 缓存 60 秒，减少频繁扫描
}

# 显示器列表缓存
_monitor_cache = {
    'monitors': [],
    'timestamp': 0,
    'cache_duration': 60  # 缓存 60 秒，减少频繁扫描
}

def get_available_cameras(force_refresh=False):
    """获取所有可用的摄像头列表
    
    Args:
        force_refresh: 是否强制刷新，忽略缓存
        
    Returns:
        list: 摄像头信息列表，每个元素为字典，包含 'index' 和 'name'
    """
    global _camera_cache
    
    # 检查缓存是否有效（除非强制刷新）
    current_time = time.time()
    if not force_refresh and _camera_cache['cameras']:
        if current_time - _camera_cache['timestamp'] < _camera_cache['cache_duration']:
            return _camera_cache['cameras']
    
    cameras = []
    
    # 方法 1：使用 DirectShow 过滤器枚举摄像头
    try:
        # 尝试打开索引 0-9 的摄像头
        for i in range(10):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                # 尝试获取摄像头名称
                camera_name = f"摄像头 {i}"
                try:
                    # 读取一帧以确认摄像头可用
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        cameras.append({
                            'index': i,
                            'name': camera_name
                        })
                except:
                    pass
                cap.release()
    except Exception as e:
        print(f"枚举摄像头时出错：{e}")
    
    # 如果 DirectShow 方法失败，使用简单的测试方法
    if not cameras:
        try:
            for i in range(5):
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        cameras.append({
                            'index': i,
                            'name': f"摄像头 {i}"
                        })
                    cap.release()
        except Exception as e:
            print(f"备用方法枚举摄像头失败：{e}")
    
    # 如果还是没有找到摄像头，至少添加默认摄像头 0
    if not cameras:
        cameras.append({
            'index': 0,
            'name': '默认摄像头'
        })
    
    # 更新缓存
    _camera_cache['cameras'] = cameras
    _camera_cache['timestamp'] = current_time
    
    return cameras

def get_available_monitors(force_refresh=False):
    """获取所有可用的显示器列表
    
    Args:
        force_refresh: 是否强制刷新，忽略缓存
        
    Returns:
        list: 显示器信息列表，每个元素为字典，包含 'index' 和 'name'
    """
    global _monitor_cache
    
    # 检查缓存是否有效（除非强制刷新）
    current_time = time.time()
    if not force_refresh and _monitor_cache['monitors']:
        if current_time - _monitor_cache['timestamp'] < _monitor_cache['cache_duration']:
            return _monitor_cache['monitors']
    
    monitors = []
    com_initialized = False
    
    try:
        # 初始化 COM（WMI 需要）
        try:
            pythoncom.CoInitialize()
            com_initialized = True
        except pythoncom.com_error:
            com_initialized = False
        
        # 使用 WMI 获取显示器信息
        wmi_obj = wmi.WMI(namespace='root\\wmi')
        monitor_configs = wmi_obj.WmiMonitorID()
        
        for i, monitor in enumerate(monitor_configs):
            try:
                # 获取显示器名称
                name = "未知显示器"
                if hasattr(monitor, 'UserFriendlyName') and monitor.UserFriendlyName:
                    # UserFriendlyName 是一个字节数组，需要转换
                    try:
                        name_bytes = monitor.UserFriendlyName
                        # 过滤掉空字节和无效字符
                        name_str = ''.join([chr(b) for b in name_bytes if b != 0 and b < 128])
                        if name_str.strip():
                            name = name_str.strip()
                    except (UnicodeDecodeError, TypeError):
                        pass
                
                monitors.append({
                    'index': i,
                    'name': f"{name} (显示器 {i+1})"
                })
            except Exception as e:
                print(f"获取显示器 {i} 信息失败：{e}")
                # 添加一个通用的显示器条目
                monitors.append({
                    'index': i,
                    'name': f'显示器 {i+1}'
                })
            
    except wmi.x_wmi as e:
        print(f"使用 WMI 获取显示器列表失败：{e}")
    except pythoncom.com_error as e:
        print(f"COM 初始化失败：{e}")
    except Exception as e:
        print(f"使用 WMI 获取显示器列表失败：{e}")
        # 如果 WMI 方法失败，使用 Windows API 枚举显示器
        try:
            import ctypes
            from ctypes import wintypes
            
            # 简单的显示器枚举
            monitor_count = 0
            def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                nonlocal monitor_count
                monitor_count += 1
                return True
            
            user32 = ctypes.windll.user32
            MONITORENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_bool,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.wintypes.RECT),
                ctypes.c_void_p
            )
            
            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
            
            # 添加检测到的显示器
            for i in range(monitor_count):
                monitors.append({
                    'index': i,
                    'name': f'显示器 {i+1}'
                })
        except ImportError as e2:
            print(f"导入 ctypes 失败：{e2}")
        except Exception as e2:
            print(f"备用方法获取显示器列表失败：{e2}")
            # 如果所有方法都失败，至少添加一个默认显示器
            monitors.append({
                'index': 0,
                'name': '默认显示器'
            })
    finally:
        # 清理 COM（只在成功初始化后清理）
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
            except pythoncom.com_error:
                pass
    
    # 如果没有找到任何显示器，添加一个默认显示器
    if not monitors:
        monitors.append({
            'index': 0,
            'name': '默认显示器'
        })
    
    # 更新缓存
    _monitor_cache['monitors'] = monitors
    _monitor_cache['timestamp'] = current_time
    
    return monitors

def process_frame(frame):
    """处理每一帧图像的亮度和对比度计算
    
    Args:
        frame: 输入的RGB图像帧（BGR格式，OpenCV默认格式）
        
    Returns:
        tuple: 包含两个元素的元组
            - brightness: 帧的亮度值（灰度图像的平均值，范围0-255）
            - contrast: 帧的对比度值（灰度图像的标准差）
    """
    # 将RGB图像转换为灰度图像
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 计算灰度图像的平均值作为亮度
    brightness = np.mean(gray_frame)
    # 计算灰度图像的标准差作为对比度
    contrast = np.std(gray_frame)
    return brightness, contrast

def calculate_average_brightness(frames, weights=None, window_size=5):
    """计算综合亮度，支持多线程环境
    
    Args:
        frames: 图像帧列表
        weights: 可选的权重列表，与frames长度相同
        window_size: 移动平均窗口大小，默认为5
        
    Returns:
        tuple: 包含两个元素的元组
            - int: 综合亮度评分（范围1-100）
            - list: 特征向量，包含多个特征
    """
    # 如果未提供权重，默认所有帧权重相等
    if weights is None:
        weights = np.ones(len(frames))  # 默认所有帧的权重相等
    
    # 存储每一帧的亮度值
    brightness_values = []
    # 存储每一帧的对比度值
    contrast_values = []
    # 存储相邻帧之间的亮度变化率
    brightness_changes = []
    
    # 存储每一帧的其他特征
    frame_features = []
    
    # 初始化前一帧亮度值（使用第一帧的灰度平均值）
    previous_brightness = np.mean(cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY))
    
    # 计算每一帧的亮度、对比度和亮度变化率
    for i, frame in enumerate(frames):
        # 处理当前帧，获取亮度和对比度
        brightness, contrast = process_frame(frame)  # 处理帧并返回亮度和对比度
        # 存储当前帧的亮度值
        brightness_values.append(brightness)
        # 存储当前帧的对比度值
        contrast_values.append(contrast)
        # 计算亮度变化率（从第二帧开始）
        if i > 0:
            brightness_changes.append(abs(brightness - previous_brightness)) 
        # 更新前一帧亮度值为当前帧亮度
        previous_brightness = brightness
        
        # 计算当前帧的综合特征
        # 1. 色彩特征
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        R, G, B = rgb_frame[:,:,0], rgb_frame[:,:,1], rgb_frame[:,:,2]
        
        # 平均RGB值
        avg_r = np.mean(R)
        avg_g = np.mean(G)
        avg_b = np.mean(B)
        
        # 色彩饱和度
        hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        avg_saturation = np.mean(hsv_frame[:,:,1])
        
        # 2. 感知亮度和伽马校正
        y_channel = 0.299 * R + 0.587 * G + 0.114 * B
        gamma = 2.2
        y_corrected = (y_channel / 255.0) ** (1/gamma) * 255.0
        
        # 3. 亮度分布特征
        brightness_std_frame = np.std(y_corrected)
        brightness_kurtosis_frame = np.mean(((y_corrected - np.mean(y_corrected)) / brightness_std_frame)**4) if brightness_std_frame > 0 else 0
        brightness_skewness_frame = np.mean(((y_corrected - np.mean(y_corrected)) / brightness_std_frame)**3) if brightness_std_frame > 0 else 0
        
        # 4. 清晰度评估（使用拉普拉斯算子）
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray_frame, cv2.CV_64F)
        clarity = np.var(laplacian)
        
        # 5. 空间加权平均亮度
        height, width = y_corrected.shape
        center_x, center_y = width // 2, height // 2
        x = np.linspace(0, width-1, width)
        y = np.linspace(0, height-1, height)
        xx, yy = np.meshgrid(x, y)
        distance = np.sqrt((xx - center_x)**2 + (yy - center_y)**2)
        max_distance = np.sqrt(center_x**2 + center_y**2)
        normalized_distance = distance / max_distance
        weight_matrix = np.exp(-(normalized_distance**2) / (2 * 0.5**2))
        weighted_avg_brightness = np.sum(y_corrected * weight_matrix) / np.sum(weight_matrix)
        
        # 存储当前帧的特征
        frame_features.append([
            brightness,
            contrast,
            avg_r,
            avg_g,
            avg_b,
            avg_saturation,
            np.mean(y_corrected),
            brightness_std_frame,
            brightness_kurtosis_frame,
            brightness_skewness_frame,
            clarity,
            weighted_avg_brightness
        ])
    
    # 1. 计算加权平均亮度
    weighted_brightness = np.average(brightness_values, weights=weights)
    # 2. 计算亮度值的标准差（反映亮度波动情况）
    brightness_std = np.std(brightness_values)
    # 3. 计算平均亮度变化率（反映帧间亮度变化）
    avg_brightness_change_rate = np.mean(brightness_changes) if brightness_changes else 0
    # 4. 计算移动平均亮度（平滑亮度波动）
    moving_average_brightness = np.convolve(brightness_values, np.ones(window_size)/window_size, mode='valid')
    moving_average_brightness = moving_average_brightness[-1] if len(moving_average_brightness) > 0 else 0
    # 5. 计算亮度和对比度的平均值
    average_brightness = np.mean(brightness_values)
    average_contrast = np.mean(contrast_values)
    
    # 计算帧特征的平均值
    frame_features_array = np.array(frame_features)
    avg_frame_features = np.mean(frame_features_array, axis=0)
    
    # 提取平均特征
    avg_r = avg_frame_features[2]
    avg_g = avg_frame_features[3]
    avg_b = avg_frame_features[4]
    avg_saturation = avg_frame_features[5]
    avg_perceived_brightness = avg_frame_features[6]
    avg_brightness_std = avg_frame_features[7]
    avg_brightness_kurtosis = avg_frame_features[8]
    avg_brightness_skewness = avg_frame_features[9]
    avg_clarity = avg_frame_features[10]
    avg_weighted_brightness = avg_frame_features[11]
    
    # 计算屏幕暗度指标：RGB平均值越低，屏幕越暗，暗度值越高
    avg_rgb = (avg_r + avg_g + avg_b) / 3
    screen_darkness = (255 - avg_rgb) / 255  # 0表示极亮，1表示极暗
    
    # 屏幕颜色对亮度的影响权重
    screen_color_weight = 0.15  # 新增：屏幕颜色影响权重为15%
    
    # 综合评分计算：为每个指标分配一个权重
    # 优化后的权重分配，结合更多特征
    weighted_score = (0.35 * weighted_brightness +      # 加权平均亮度占 35%
                      0.15 * average_brightness +       # 平均亮度占 15%
                      0.10 * (1.0 - brightness_std / 255.0) +  # 亮度标准差（越小越好）占 10%
                      0.05 * (1.0 - min(avg_brightness_change_rate / 255.0, 1.0)) +  # 亮度变化率（越小越好）占 5%
                      0.10 * moving_average_brightness +   # 移动平均亮度占 10%
                      0.10 * average_contrast +       # 平均对比度占 10%
                      0.05 * avg_saturation +         # 色彩饱和度占 5%
                      0.05 * avg_perceived_brightness +  # 感知亮度占 5%
                      0.03 * (3.0 - abs(avg_brightness_kurtosis - 3.0)) / 3.0 +  # 亮度分布峰度（越接近正态越好）占 3%
                      0.02 * (1.0 - abs(avg_brightness_skewness) / 10.0) +  # 亮度分布偏度（越对称越好）占 2%
                      screen_color_weight * screen_darkness)  # 新增：屏幕暗度影响占 15% - 屏幕越暗，推荐亮度越高
    
    # 将综合评分映射到 1-100 的范围
    normalized_score = np.clip(weighted_score, 0, 255)  # 确保不会超过 255
    final_brightness = int((normalized_score / 255) * 99 + 1)  # 映射到 1-100 之间
    
    # 构建特征向量（包含所有摄像头和屏幕参数，不包含已经计算好的亮度结果）
    features = [
        average_brightness,  # 平均亮度
        average_contrast,  # 平均对比度
        avg_brightness_change_rate,  # 平均亮度变化率
        moving_average_brightness,  # 移动平均亮度
        avg_r,  # 平均R值
        avg_g,  # 平均G值
        avg_b,  # 平均B值
        avg_saturation,  # 平均色彩饱和度
        avg_perceived_brightness,  # 平均感知亮度
        avg_brightness_std,  # 平均亮度标准差
        avg_brightness_kurtosis,  # 平均亮度峰度
        avg_brightness_skewness,  # 平均亮度偏度
        avg_clarity,  # 平均清晰度
        avg_weighted_brightness  # 平均加权亮度
    ]
    
    # 打印计算结果
    print(f"计算亮度：{final_brightness}")
    return final_brightness, features

# 捕获帧（取消每帧之间的等待时间）
def capture_frames_from_camera(camera_index=0, num_frames=20):
    """从摄像头捕获指定数量的图像帧
    
    Args:
        camera_index: 摄像头索引，默认为0（默认摄像头）
        num_frames: 要捕获的帧数，默认为20
        
    Returns:
        list: 捕获的图像帧列表
        None: 如果无法打开摄像头或未捕获到任何帧
    """
    # 打开摄像头（使用 DirectShow 后端以减少警告）
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    
    # 检查摄像头是否成功打开
    if not cap.isOpened():
        print("无法打开摄像头")
        return None
    
    # 存储捕获的帧
    frames = []
    
    # 捕获指定数量的帧
    for _ in range(num_frames):
        # 读取一帧
        ret, frame = cap.read()
        
        # 检查是否成功读取帧
        if not ret:
            print("无法读取帧")
            break
        
        # 确保帧不为空
        if frame is not None:  # 确保帧不为空
            frames.append(frame)
    
    # 释放摄像头资源
    cap.release()
    
    # 检查是否成功捕获到帧
    if not frames:
        print("未成功捕获任何帧")
    
    return frames

def adjust_brightness(target_brightness, monitor_indices=None):
    """调整 Windows 系统屏幕亮度
    
    Args:
        target_brightness: 目标亮度值（范围0-100）
        
        monitor_indices: 要调整亮度的显示器索引列表，默认为None（调整所有显示器）
        
    Returns:
        bool: 操作是否成功
    """
    import ctypes
    import ctypes.wintypes
    
    # 确保目标亮度在0-100范围内
    target_brightness = max(0, min(100, target_brightness))
    brightness = int(target_brightness)
    
    print(f"尝试设置屏幕亮度为: {brightness}%")
    
    try:
        # 方法1: 使用WMI方法 - 适用于大多数Windows系统
        try:
            import wmi
            print("使用WMI方法设置亮度...")
            
            # 尝试不同的WMI命名空间
            namespaces = ['wmi', 'root\\wmi']
            success = False
            
            for namespace in namespaces:
                try:
                    w = wmi.WMI(namespace=namespace)
                    monitors = w.WmiMonitorBrightnessMethods()
                    
                    if monitors:
                        print(f"在命名空间 {namespace} 中找到 {len(monitors)} 个显示器")
                        for i, monitor in enumerate(monitors):
                            # 如果指定了显示器索引列表，只调整指定的显示器
                            if monitor_indices is not None and i not in monitor_indices:
                                print(f"跳过显示器 {i}（不在选择列表中）")
                                continue
                            
                            try:
                                print(f"正在设置显示器 {i} 的亮度...")
                                # 尝试不同的超时值
                                for timeout in [0, 1000, 5000]:
                                    try:
                                        monitor.WmiSetBrightness(brightness, timeout)
                                        print(f"显示器 {i} 亮度设置成功")
                                        success = True
                                        break
                                    except wmi.x_wmi as e:
                                        print(f"使用超时 {timeout} 设置亮度失败: {str(e)}")
                                    except pythoncom.com_error as e:
                                        print(f"COM错误，使用超时 {timeout} 设置亮度失败: {str(e)}")
                            except wmi.x_wmi as e:
                                print(f"设置显示器 {i} 亮度时WMI出错: {str(e)}")
                            except pythoncom.com_error as e:
                                print(f"设置显示器 {i} 亮度时COM出错: {str(e)}")
                        if success:
                            break
                    else:
                        print(f"在命名空间 {namespace} 中未找到显示器")
                except wmi.x_wmi as e:
                    print(f"访问命名空间 {namespace} 时WMI出错: {str(e)}")
                except pythoncom.com_error as e:
                    print(f"访问命名空间 {namespace} 时COM出错: {str(e)}")
            
            if success:
                print(f"屏幕亮度已成功设置为: {brightness}%")
                return True
        except ImportError:
            print("wmi模块未安装")
        except wmi.x_wmi as e:
            print(f"WMI方法出错: {str(e)}")
        except pythoncom.com_error as e:
            print(f"WMI方法COM错误: {str(e)}")
        
        # 方法2: 使用Windows API - 更底层的方法
        print("尝试使用Windows API设置亮度...")
        try:
            # 定义必要的常量和结构体
            DISPLAY_DEVICE = ctypes.Structure
            MONITORINFOEX = ctypes.Structure
            
            # 尝试使用SetMonitorBrightness函数
            # 注意：这需要管理员权限，并且在不同Windows版本上可能有所不同
            user32 = ctypes.WinDLL('user32.dll')
            gdi32 = ctypes.WinDLL('gdi32.dll')
            
            # 获取显示器数量
            monitor_count = ctypes.c_int()
            user32.EnumDisplayMonitors(None, None, lambda hMonitor, hdcMonitor, lprcMonitor, dwData: dwData.contents.value + 1, ctypes.byref(monitor_count))
            print(f"找到 {monitor_count.value} 个显示器")
            
            # 注意：这里只是演示，实际的SetMonitorBrightness调用需要更复杂的实现
            # 由于权限和兼容性问题，我们会在下面提供一个更简单的方法
            
        except ctypes.WinError as e:
            print(f"Windows API方法WinError: {str(e)}")
        except AttributeError as e:
            print(f"Windows API方法属性错误: {str(e)}")
        
        # 方法3: 使用Registry方法 - 适用于某些系统
        print("尝试使用注册表方法设置亮度...")
        try:
            import winreg
            
            # 打开注册表项
            key_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0000"
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE)
                # 注意：实际的注册表键可能因系统而异
                print("注册表访问成功")
                winreg.CloseKey(key)
            except FileNotFoundError:
                print(f"注册表路径不存在: {key_path}")
            except PermissionError:
                print(f"没有权限访问注册表: {key_path}")
            except OSError as e:
                print(f"注册表访问出错: {str(e)}")
                
        except ImportError:
            print("winreg模块不可用")
        
        # 方法4: 使用PowerShell命令 - 最可靠的方法
        print("尝试使用PowerShell命令设置亮度...")
        try:
            import subprocess
            
            # 使用PowerShell命令设置亮度
            # 注意：这可能需要管理员权限
            command = f"(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness({brightness}, 0)"
            result = subprocess.run(["powershell", "-Command", command], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"PowerShell命令执行成功")
                print(f"屏幕亮度已设置为: {brightness}%")
                return True
            else:
                print(f"PowerShell命令执行失败: {result.stderr}")
                
        except FileNotFoundError:
            print("PowerShell命令未找到")
        except subprocess.CalledProcessError as e:
            print(f"PowerShell命令执行错误: {str(e)}")
        except OSError as e:
            print(f"PowerShell方法系统错误: {str(e)}")
        
        # 如果所有方法都失败
        print("警告: 所有设置亮度的方法都失败了")
        print("请尝试以下解决方案:")
        print("1. 以管理员身份运行此脚本")
        print("2. 确保您的显示器支持软件亮度调节")
        print("3. 检查显示器驱动程序是否已更新")
        
        return False
        
    except ValueError as e:
        print(f"设置亮度时参数错误: {str(e)}")
        return False
    except Exception as e:
        print(f"设置亮度时出错: {str(e)}")
        return False

class BrightnessNeuralNetwork:
    """亮度神经网络类，用于处理亮度值并输出新的评分"""
    
    def __init__(self, input_size=14, hidden_sizes=[32, 16, 8], output_size=1, activation='relu', use_adam=True, use_cache=True):
        """初始化神经网络参数
        
        Args:
            input_size: 输入层大小（默认14，对应扩展后的特征向量）
            hidden_sizes: 隐藏层大小列表（默认[32, 16, 8]，三个隐藏层）
            output_size: 输出层大小（默认1，对应新的评分）
            activation: 激活函数选择（'relu'或'tanh'，默认'relu'）
            use_adam: 是否使用Adam优化器（默认True）
            use_cache: 是否使用缓存机制（默认True）
        """
        # 初始化网络结构参数
        self.input_size = input_size
        self.hidden_sizes = hidden_sizes
        self.output_size = output_size
        self.num_hidden_layers = len(hidden_sizes)
        self.activation = activation
        self.use_adam = use_adam
        self.use_cache = use_cache
        
        # 初始化缓存 - 使用有序字典实现LRU缓存
        if self.use_cache:
            from collections import OrderedDict
            self.cache = OrderedDict()  # 缓存输入特征到输出的映射
            self.cache_size = 2000  # 增大缓存大小，提高命中率
        
        # 初始化权重和偏置
        self.weights = []
        self.biases = []
        
        # 输入层到第一个隐藏层
        self.weights.append(np.random.randn(input_size, hidden_sizes[0]) * 0.01)
        self.biases.append(np.zeros((1, hidden_sizes[0])))
        
        # 隐藏层之间的连接
        for i in range(self.num_hidden_layers - 1):
            self.weights.append(np.random.randn(hidden_sizes[i], hidden_sizes[i+1]) * 0.01)
            self.biases.append(np.zeros((1, hidden_sizes[i+1])))
        
        # 最后一个隐藏层到输出层
        self.weights.append(np.random.randn(hidden_sizes[-1], output_size) * 0.01)
        self.biases.append(np.zeros((1, output_size)))
        
        # 初始化Adam优化器参数
        if self.use_adam:
            self.beta1 = 0.9
            self.beta2 = 0.999
            self.epsilon = 1e-8
            self.t = 0
            
            # 初始化动量和平方梯度
            self.m_weights = [np.zeros_like(w) for w in self.weights]
            self.v_weights = [np.zeros_like(w) for w in self.weights]
            self.m_biases = [np.zeros_like(b) for b in self.biases]
            self.v_biases = [np.zeros_like(b) for b in self.biases]
    
    def _get_cache_key(self, features):
        """生成缓存键
        
        Args:
            features: 输入特征列表
            
        Returns:
            缓存键（元组形式）
        """
        # 将特征四舍五入到小数点后2位，以减少缓存大小
        rounded_features = tuple(round(f, 2) for f in features)
        return rounded_features
    
    def clear_cache(self):
        """清空缓存"""
        if self.use_cache:
            self.cache.clear()
    
    def forward(self, features, training=False):
        """前向传播计算
        
        Args:
            features: 输入特征列表，包含亮度、对比度、色彩特征等多个特征
            training: 是否为训练模式（默认False）
            
        Returns:
            - 非训练模式：新的评分值（范围0-100）
            - 训练模式：元组包含(原始输出, 激活值列表, z值列表)
        """
        # 检查缓存（仅在非训练模式下）
        if not training and self.use_cache:
            cache_key = self._get_cache_key(features)
            if cache_key in self.cache:
                # 更新缓存项的位置，使其成为最新访问的项
                self.cache.move_to_end(cache_key)
                return self.cache[cache_key]
        
        # 归一化输入特征 - 使用向量化操作优化
        features_array = np.array(features)
        normalized_features = np.zeros_like(features_array, dtype=np.float32)
        
        # 特征归一化
        # 平均亮度（0-255）
        normalized_features[0] = features_array[0] / 255
        # 对比度值、感知亮度、空间加权亮度（0-255）
        normalized_features[[1, 8, 9, 13]] = features_array[[1, 8, 9, 13]] / 255
        # 亮度变化率（0-255）
        normalized_features[2] = min(features_array[2] / 255, 1.0)
        # 移动平均亮度（0-255）
        normalized_features[3] = features_array[3] / 255
        # RGB值（0-255）
        normalized_features[[4, 5, 6]] = features_array[[4, 5, 6]] / 255
        # 色彩饱和度（0-255）
        normalized_features[7] = features_array[7] / 255
        # 亮度峰度（0-6，越接近3越好）
        normalized_features[10] = np.clip((3.0 - abs(features_array[10] - 3.0)) / 3.0, 0.0, 1.0)
        # 亮度偏度（-5到5，越接近0越好）
        normalized_features[11] = np.clip((1.0 - abs(features_array[11]) / 10.0), 0.0, 1.0)
        # 清晰度（0-无上限，需要归一化到0-1）
        normalized_features[12] = min(features_array[12] / 2000, 1.0)
        
        normalized_input = normalized_features.reshape(1, -1)  # 转换为2D数组
        
        # 前向传播计算 - 统一流程（不再分训练和非训练分支）
        activations = [normalized_input]
        z_values = []
        current_input = normalized_input
        
        # 前向传播通过所有隐藏层
        for i in range(self.num_hidden_layers):
            # 使用numpy的高效矩阵乘法
            z = np.dot(current_input, self.weights[i]) + self.biases[i]
            z_values.append(z)
            # 根据选择的激活函数计算
            if self.activation == 'relu':
                activation = np.maximum(0, z)  # 使用numpy内置的relu实现
            else:  # tanh
                activation = np.tanh(z)  # 使用numpy内置的tanh实现
            activations.append(activation)
            current_input = activation
        
        # 输出层计算
        output_z = np.dot(current_input, self.weights[-1]) + self.biases[-1]
        z_values.append(output_z)
        output = 1 / (1 + np.exp(-output_z))  # 直接计算sigmoid，避免函数调用开销
        activations.append(output)
        
        if training:
            # 训练模式下，返回原始输出和中间值用于反向传播
            return output, activations, z_values
        else:
            # 非训练模式下，返回处理后的结果
            # 将输出映射到0-100范围
            new_score = output[0, 0] * 100
            result = min(max(round(new_score), 0), 100)  # 确保在0-100范围内，且为整数
            
            # 存储到缓存
            if self.use_cache:
                cache_key = self._get_cache_key(features)
                # 检查缓存大小
                if len(self.cache) >= self.cache_size:
                    # 移除最旧的缓存项（第一个项）
                    self.cache.popitem(last=False)
                # 添加新的缓存项到末尾
                self.cache[cache_key] = result
            
            return result
    
    def train(self, X, y, learning_rate=0.001, epochs=2000, batch_size=64, lr_decay=0.995, log_callback=None):
        """训练神经网络
        
        Args:
            X: 输入数据（特征列表，每个元素是一个特征向量）
            y: 目标输出值列表
            learning_rate: 学习率
            epochs: 训练轮数
            batch_size: 批量大小
            lr_decay: 学习率衰减因子（默认0.995）
            log_callback: 日志回调函数，用于将训练信息传递给GUI界面
        """
        # 训练循环
        for epoch in range(epochs):
            # 使用固定学习率，不进行衰减
            current_lr = learning_rate
            
            # 随机打乱数据
            indices = np.arange(len(X))
            np.random.shuffle(indices)
            X_shuffled = np.array(X)[indices]
            y_shuffled = np.array(y)[indices]
            
            # 批量训练
            for i in range(0, len(X_shuffled), batch_size):
                # 获取当前批次
                batch_end = min(i + batch_size, len(X_shuffled))
                batch_X = X_shuffled[i:batch_end]
                batch_y = y_shuffled[i:batch_end] / 100  # 将0-100映射到0-1
                batch_y = batch_y.reshape(-1, 1)
                
                # 逐个样本训练
                for j in range(len(batch_X)):
                    # 前向传播 - 获取当前样本的输出和中间值
                    output, activations, z_values = self.forward(batch_X[j], training=True)
                    
                    # 计算输出层梯度 (sigmoid导数 * MSE导数)
                    # sigmoid导数: sigmoid(x) * (1 - sigmoid(x)) = output * (1 - output)
                    delta = (output - batch_y[j:j+1]) * output * (1 - output)
                    
                    # 更新参数
                    if self.use_adam:
                        # Adam优化器更新
                        self.t += 1
                        
                        # 更新输出层参数
                        # 权重更新
                        g_w = activations[-2].T.dot(delta)
                        self.m_weights[-1] = self.beta1 * self.m_weights[-1] + (1 - self.beta1) * g_w
                        self.v_weights[-1] = self.beta2 * self.v_weights[-1] + (1 - self.beta2) * (g_w ** 2)
                        m_hat_w = self.m_weights[-1] / (1 - self.beta1 ** self.t)
                        v_hat_w = self.v_weights[-1] / (1 - self.beta2 ** self.t)
                        self.weights[-1] -= current_lr * m_hat_w / (np.sqrt(v_hat_w) + self.epsilon)
                        
                        # 偏置更新
                        g_b = np.sum(delta, axis=0, keepdims=True)
                        self.m_biases[-1] = self.beta1 * self.m_biases[-1] + (1 - self.beta1) * g_b
                        self.v_biases[-1] = self.beta2 * self.v_biases[-1] + (1 - self.beta2) * (g_b ** 2)
                        m_hat_b = self.m_biases[-1] / (1 - self.beta1 ** self.t)
                        v_hat_b = self.v_biases[-1] / (1 - self.beta2 ** self.t)
                        self.biases[-1] -= current_lr * m_hat_b / (np.sqrt(v_hat_b) + self.epsilon)
                        
                        # 反向传播通过隐藏层
                        for k in range(self.num_hidden_layers, 0, -1):
                            # 计算当前层的梯度
                            if self.activation == 'relu':
                                delta = delta.dot(self.weights[k].T) * relu_derivative(z_values[k-1])
                            else:  # tanh
                                delta = delta.dot(self.weights[k].T) * tanh_derivative(activations[k])
                            
                            # 权重更新
                            g_w = activations[k-1].T.dot(delta)
                            self.m_weights[k-1] = self.beta1 * self.m_weights[k-1] + (1 - self.beta1) * g_w
                            self.v_weights[k-1] = self.beta2 * self.v_weights[k-1] + (1 - self.beta2) * (g_w ** 2)
                            m_hat_w = self.m_weights[k-1] / (1 - self.beta1 ** self.t)
                            v_hat_w = self.v_weights[k-1] / (1 - self.beta2 ** self.t)
                            self.weights[k-1] -= current_lr * m_hat_w / (np.sqrt(v_hat_w) + self.epsilon)
                            
                            # 偏置更新
                            g_b = np.sum(delta, axis=0, keepdims=True)
                            self.m_biases[k-1] = self.beta1 * self.m_biases[k-1] + (1 - self.beta1) * g_b
                            self.v_biases[k-1] = self.beta2 * self.v_biases[k-1] + (1 - self.beta2) * (g_b ** 2)
                            m_hat_b = self.m_biases[k-1] / (1 - self.beta1 ** self.t)
                            v_hat_b = self.v_biases[k-1] / (1 - self.beta2 ** self.t)
                            self.biases[k-1] -= current_lr * m_hat_b / (np.sqrt(v_hat_b) + self.epsilon)
                    else:
                        # 标准SGD更新
                        # 更新最后一层权重和偏置
                        self.weights[-1] -= current_lr * activations[-2].T.dot(delta)
                        self.biases[-1] -= current_lr * np.sum(delta, axis=0, keepdims=True)
                        
                        # 反向传播通过隐藏层
                        for k in range(self.num_hidden_layers, 0, -1):
                            # 计算当前层的梯度
                            if self.activation == 'relu':
                                delta = delta.dot(self.weights[k].T) * relu_derivative(z_values[k-1])
                            else:  # tanh
                                delta = delta.dot(self.weights[k].T) * tanh_derivative(activations[k])
                            
                            self.weights[k-1] -= current_lr * activations[k-1].T.dot(delta)
                            self.biases[k-1] -= current_lr * np.sum(delta, axis=0, keepdims=True)
            
            # 每100轮打印一次损失
            if epoch % 100 == 0:
                # 清空缓存后计算当前损失，确保使用最新的权重
                self.clear_cache()
                predictions = np.array([self.forward(x, training=True)[0][0,0] * 100 for x in X])
                avg_loss = np.mean(0.5 * (predictions - y) ** 2)
                mae = np.mean(np.abs(predictions - y))
                
                # 构建日志消息
                log_msg = f"Epoch {epoch}, Loss: {avg_loss:.4f}, MAE: {mae:.2f}"
                
                # 打印到控制台并记录到日志回调
                #print(log_msg)
                if log_callback:
                    log_callback(log_msg)
                
                # 当损失值小于0.5时，提前收敛
                if avg_loss < 0.5:
                    converge_msg = f"损失值已小于0.5，提前收敛，训练结束"
                    #print(converge_msg)
                    
                    if log_callback:
                        log_callback(converge_msg)
                    return
    
    def evaluate(self, X, y):
        """评估模型性能
        
        Args:
            X: 输入数据（特征列表，每个元素是一个特征向量）
            y: 目标输出值列表
            
        Returns:
            dict: 包含损失、MAE等评估指标的字典
        """
        predictions = np.array([self.forward(x) for x in X])
        mse = np.mean(0.5 * (predictions - y) ** 2)
        mae = np.mean(np.abs(predictions - y))
        rmse = np.sqrt(np.mean((predictions - y) ** 2))
        
        return {
            'mse': mse,
            'mae': mae,
            'rmse': rmse
        }
    
    def save_model(self, filename):
        """保存模型参数
        
        Args:
            filename: 保存文件名
        """
        # 构建保存字典
        save_dict = {
            'input_size': self.input_size,
            'hidden_sizes': self.hidden_sizes,
            'output_size': self.output_size,
            'activation': self.activation,
            'use_adam': self.use_adam,
            'use_cache': self.use_cache
        }
        
        # 保存权重和偏置
        for i, (weight, bias) in enumerate(zip(self.weights, self.biases)):
            save_dict[f'weights{i}'] = weight
            save_dict[f'bias{i}'] = bias
        
        # 如果使用Adam优化器，保存其参数
        if self.use_adam:
            save_dict['beta1'] = self.beta1
            save_dict['beta2'] = self.beta2
            save_dict['epsilon'] = self.epsilon
            save_dict['t'] = self.t
            for i, (m_w, v_w, m_b, v_b) in enumerate(zip(self.m_weights, self.v_weights, self.m_biases, self.v_biases)):
                save_dict[f'm_weights{i}'] = m_w
                save_dict[f'v_weights{i}'] = v_w
                save_dict[f'm_biases{i}'] = m_b
                save_dict[f'v_biases{i}'] = v_b
        
        np.savez(filename, **save_dict)
    
    def load_model(self, filename):
        """加载模型参数
        
        Args:
            filename: 加载文件名
        """
        data = np.load(filename)
        
        # 加载网络结构参数
        self.input_size = data['input_size']
        self.hidden_sizes = data['hidden_sizes']
        self.output_size = data['output_size']
        self.activation = data.get('activation', 'relu')  # 默认使用relu
        self.use_adam = data.get('use_adam', True)  # 默认使用Adam
        self.use_cache = data.get('use_cache', True)  # 默认使用缓存
        self.num_hidden_layers = len(self.hidden_sizes)
        
        # 初始化缓存
        if self.use_cache:
            self.cache = {}  # 缓存输入特征到输出的映射
            self.cache_size = 1000  # 缓存大小限制
        
        # 加载权重和偏置
        self.weights = []
        self.biases = []
        
        # 计算需要加载的权重和偏置数量
        num_layers = self.num_hidden_layers + 1  # 隐藏层 + 输出层
        
        for i in range(num_layers):
            self.weights.append(data[f'weights{i}'])
            self.biases.append(data[f'bias{i}'])
        
        # 初始化激活值列表
        self.activations = []
        
        # 如果使用Adam优化器，加载其参数
        if self.use_adam:
            self.beta1 = data.get('beta1', 0.9)
            self.beta2 = data.get('beta2', 0.999)
            self.epsilon = data.get('epsilon', 1e-8)
            self.t = data.get('t', 0)
            
            # 加载动量和平方梯度
            self.m_weights = []
            self.v_weights = []
            self.m_biases = []
            self.v_biases = []
            
            for i in range(num_layers):
                self.m_weights.append(data.get(f'm_weights{i}', np.zeros_like(self.weights[i])))
                self.v_weights.append(data.get(f'v_weights{i}', np.zeros_like(self.weights[i])))
                self.m_biases.append(data.get(f'm_biases{i}', np.zeros_like(self.biases[i])))
                self.v_biases.append(data.get(f'v_biases{i}', np.zeros_like(self.biases[i])))

# 添加激活函数支持
if not hasattr(np, 'sigmoid'):
    def sigmoid(x):
        return 1 / (1 + np.exp(-x))
    np.sigmoid = sigmoid

# 添加ReLU激活函数
def relu(x):
    return np.maximum(0, x)

# 添加ReLU导数
def relu_derivative(x):
    return np.where(x > 0, 1, 0)

# 添加tanh导数
def tanh_derivative(x):
    return 1 - np.tanh(x) ** 2

# 综合亮度计算算法
def calculate_comprehensive_brightness(frame):
    """综合分析屏幕内容的亮度水平，考虑人眼视觉感知特性和图像空间分布
    
    Args:
        frame: 输入的RGB图像帧（BGR格式，OpenCV默认格式）
        
    Returns:
        int: 综合亮度评分（范围0-100）
            - 0表示极暗
            - 100表示极亮
            - 50表示中等亮度
    """
    # 1. 将BGR图像转换为RGB图像
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    
    # 2. 转换为感知亮度（Y通道）
    # 使用标准的RGB到Y的转换公式，考虑人眼对不同颜色的敏感度
    # Y = 0.299*R + 0.587*G + 0.114*B
    R, G, B = rgb_frame[:,:,0], rgb_frame[:,:,1], rgb_frame[:,:,2]
    y_channel = 0.299 * R + 0.587 * G + 0.114 * B
    
    # 3. 应用伽马校正，模拟人眼的非线性感知特性
    # 伽马值通常取2.2，因为CRT显示器的响应曲线大约为2.2
    gamma = 2.2
    y_corrected = (y_channel / 255.0) ** (1/gamma) * 255.0
    
    # 4. 应用空间加权，模拟视觉中央凹效应
    height, width = y_corrected.shape
    center_x, center_y = width // 2, height // 2
    
    # 创建距离矩阵
    x = np.linspace(0, width-1, width)
    y = np.linspace(0, height-1, height)
    xx, yy = np.meshgrid(x, y)
    
    # 计算每个像素到中心的距离（归一化到0-1）
    distance = np.sqrt((xx - center_x)**2 + (yy - center_y)**2)
    max_distance = np.sqrt(center_x**2 + center_y**2)
    normalized_distance = distance / max_distance
    
    # 创建高斯权重矩阵，中心权重高，边缘权重低
    # 标准差设为0.5，使权重在中心区域较高
    weight_matrix = np.exp(-(normalized_distance**2) / (2 * 0.5**2))
    
    # 5. 计算加权平均亮度
    weighted_avg_brightness = np.sum(y_corrected * weight_matrix) / np.sum(weight_matrix)
    
    # 6. 分析亮度分布特征
    # 计算亮度的标准差（反映亮度的均匀性）
    brightness_std = np.std(y_corrected)
    
    # 计算亮度直方图特征
    hist, bins = np.histogram(y_corrected, bins=256, range=(0, 255))
    
    # 计算亮度分布的峰度（反映亮度分布的尖锐程度）
    brightness_kurtosis = np.mean(((y_corrected - np.mean(y_corrected)) / brightness_std)**4) if brightness_std > 0 else 0
    
    # 计算亮度分布的偏度（反映亮度分布的不对称性）
    brightness_skewness = np.mean(((y_corrected - np.mean(y_corrected)) / brightness_std)**3) if brightness_std > 0 else 0
    
    # 7. 综合评分计算
    # 科学合理的权重分配方案，基于人眼视觉感知特性
    weight_avg = 0.60    # 加权平均亮度权重（最重要，直接反映整体亮度）
    weight_std = 0.20    # 亮度标准差权重（反映亮度均匀性，对视觉舒适度影响大）
    weight_kurtosis = 0.10  # 峰度权重（反映亮度分布的尖锐程度）
    weight_skewness = 0.10  # 偏度权重（反映亮度分布的对称性）
    
    # 计算加权综合评分
    # 归一化各个指标到0-1范围
    normalized_avg = weighted_avg_brightness / 255.0  # 平均亮度归一化到0-1
    normalized_std = brightness_std / 255.0  # 标准差归一化到0-1
    
    # 峰度和偏度的归一化（确保它们在综合评分中的贡献合理）
    # 峰度：正态分布峰度为3，将其调整到0-1范围，越接近正态分布得分越高
    normalized_kurtosis = np.clip((3.0 - abs(brightness_kurtosis - 3.0)) / 3.0, 0.0, 1.0)
    
    # 偏度：将其调整到0-1范围，偏度越小（分布越对称）得分越高
    normalized_skewness = np.clip((1.0 - abs(brightness_skewness) / 10.0), 0.0, 1.0)
    
    # 计算综合评分
    composite_score = (weight_avg * normalized_avg +
                       weight_std * (1.0 - normalized_std) +  # 标准差越小，图像越均匀，评分越高
                       weight_kurtosis * normalized_kurtosis +  # 峰度接近正态分布，评分越高
                       weight_skewness * normalized_skewness)  # 偏度越小，分布越对称，评分越高
    
    # 将综合评分映射到0-100范围
    final_score = int(np.clip(composite_score * 100, 0, 100))
    
    return final_score

# 旧的亮度检查函数保留
def check_brightness_dominance(frame, threshold=127):
    """检查屏幕内容是以暗色占大多数还是以亮色占大多数（简单统计方法）
    
    Args:
        frame: 输入的RGB图像帧（BGR格式，OpenCV默认格式）
        threshold: 亮度阈值，低于此值为暗色，高于此值为亮色，默认为127（灰度图像的中值）
        
    Returns:
        int: 亮色像素占总像素的百分比（范围0-100）
            - 0表示全是暗色像素
            - 100表示全是亮色像素
            - 50表示亮色和暗色像素各占一半
    """
    # 将RGB图像转换为灰度图像
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    # 统计总像素数
    total_pixels = gray_frame.size
    
    # 统计亮色像素数（高于阈值的像素）
    bright_pixels = np.sum(gray_frame > threshold)
    
    # 计算亮色像素占比
    bright_ratio = (bright_pixels / total_pixels) * 100
    
    # 转换为整数并确保在0-100范围内
    bright_ratio_int = int(np.clip(bright_ratio, 0, 100))
    
    return bright_ratio_int

def generate_synthetic_data(num_samples=1000):
    """生成合成训练数据
    
    Args:
        num_samples: 生成的样本数量
        
    Returns:
        tuple: 包含特征矩阵和目标值列表的元组
    """
    X = []
    y = []
    
    for _ in range(num_samples):
        # 生成随机特征（模拟摄像头和屏幕参数）
        brightness = np.random.uniform(0, 255)
        contrast = np.random.uniform(0, 255)
        brightness_change = np.random.uniform(0, 255)
        moving_avg = np.random.uniform(0, 255)
        avg_r = np.random.uniform(0, 255)
        avg_g = np.random.uniform(0, 255)
        avg_b = np.random.uniform(0, 255)
        saturation = np.random.uniform(0, 255)
        perceived_brightness = np.random.uniform(0, 255)
        brightness_std = np.random.uniform(0, 255)
        brightness_kurtosis = np.random.uniform(0, 6)
        brightness_skewness = np.random.uniform(-5, 5)
        clarity = np.random.uniform(0, 2000)
        weighted_brightness = np.random.uniform(0, 255)
        
        # 构建特征向量（包含所有摄像头和屏幕参数）
        features = [
            brightness,
            contrast,
            brightness_change,
            moving_avg,
            avg_r,
            avg_g,
            avg_b,
            saturation,
            perceived_brightness,
            brightness_std,
            brightness_kurtosis,
            brightness_skewness,
            clarity,
            weighted_brightness
        ]
        
        # 计算屏幕暗度指标：RGB平均值越低，屏幕越暗，暗度值越高
        avg_rgb = (avg_r + avg_g + avg_b) / 3
        screen_darkness = (255 - avg_rgb) / 255  # 0表示极亮，1表示极暗
        
        # 屏幕颜色对亮度的影响权重
        screen_color_weight = 0.15  # 新增：屏幕颜色影响权重为15%
        
        # 使用与calculate_average_brightness相同的加权计算，并增加屏幕颜色的影响
        weighted_score = (
            0.35 * weighted_brightness +      # 加权平均亮度占 35%
            0.15 * brightness +       # 平均亮度占 15%
            0.10 * (1.0 - brightness_std / 255.0) +  # 亮度标准差（越小越好）占 10%
            0.05 * (1.0 - min(brightness_change / 255.0, 1.0)) +  # 亮度变化率（越小越好）占 5%
            0.10 * moving_avg +   # 移动平均亮度占 10%
            0.10 * contrast +       # 平均对比度占 10%
            0.05 * saturation +         # 色彩饱和度占 5%
            0.05 * perceived_brightness +  # 感知亮度占 5%
            0.03 * (3.0 - abs(brightness_kurtosis - 3.0)) / 3.0 +  # 亮度分布峰度（越接近正态越好）占 3%
            0.02 * (1.0 - abs(brightness_skewness) / 10.0) +  # 亮度分布偏度（越对称越好）占 2%
            screen_color_weight * screen_darkness  # 新增：屏幕暗度影响占 15% - 屏幕越暗，推荐亮度越高
        )
        
        # 将综合评分映射到 0-100 的范围
        normalized_score = np.clip(weighted_score, 0, 255)  # 确保不会超过 255
        target = int((normalized_score / 255) * 100)  # 映射到 0-100 之间
        target = np.clip(target, 0, 100)
        
        X.append(features)
        y.append(target)
    
    return X, y

# 主函数，用于直接运行脚本时执行
if __name__ == "__main__":
    # 捕获图像帧
    frames = capture_frames_from_camera()

    if frames:
        # 计算当前亮度和其他特征
        current_brightness, features = calculate_average_brightness(frames)
        print(f"当前亮度：{current_brightness}")
        print(f"特征向量：{features}")
        
        # 创建并使用神经网络
        # 使用默认的输入层大小14，对应扩展后的特征向量
        nn = BrightnessNeuralNetwork(hidden_sizes=[32, 16, 8], activation='relu', use_adam=True)
        # 暂时注释掉训练模型
        # print("\n=== 生成合成训练数据 ===")
        # X_train, y_train = generate_synthetic_data(num_samples=1000)
        # print(f"生成了 {len(X_train)} 个训练样本")
        
        # 训练神经网络
        # print("\n=== 训练神经网络 ===")
        # nn.train(X_train, y_train, learning_rate=0.01, epochs=1000, batch_size=64)
        
        # 评估模型性能
        # print("\n=== 评估模型性能 ===")
        # metrics = nn.evaluate(X_train, y_train)
        # print(f"MSE: {metrics['mse']:.4f}")
        # print(f"MAE: {metrics['mae']:.2f}")
        # print(f"RMSE: {metrics['rmse']:.2f}")
        # 保存模型
        # print("\n=== 保存模型 ===")
        # model_filename = "brightness_model.npz"
        # nn.save_model(model_filename)
        # print(f"模型已保存到 {model_filename}")
        
        # 加载模型
        # print("\n=== 加载模型 ===")
        # loaded_nn = BrightnessNeuralNetwork()
        # loaded_nn.load_model(model_filename)
        # print("模型已加载")
        # 生成合成训练数据

        
        # 使用原始神经网络预测新的亮度评分
        predicted_score = nn.forward(features)
        print(f"神经网络预测的亮度评分：{predicted_score}")
        
        # 使用加载的神经网络预测新的亮度评分（暂时注释，因为模型加载代码已注释）
        # loaded_predicted_score = loaded_nn.forward(features)
        # print(f"加载的神经网络预测的亮度评分：{loaded_predicted_score}")
        
        # 使用综合亮度计算算法评估屏幕亮度
        # 使用第一帧进行检查，因为连续帧的内容通常相似
        comprehensive_brightness = calculate_comprehensive_brightness(frames[0])
        print(f"综合亮度评分：{comprehensive_brightness}")
        
        # 根据综合评分判断屏幕亮度水平
        if comprehensive_brightness < 20:
            print("结论：屏幕内容极暗")
        elif comprehensive_brightness < 40:
            print("结论：屏幕内容较暗")
        elif comprehensive_brightness < 60:
            print("结论：屏幕内容亮度适中")
        elif comprehensive_brightness < 80:
            print("结论：屏幕内容较亮")
        else:
            print("结论：屏幕内容极亮")
        
        # 使用神经网络预测的亮度作为最终结果
        # 确保结果在0-100范围内
        final_brightness = int(np.clip(predicted_score, 0, 100))
        
        print(f"\n=== 最终亮度评估 ===")
        print(f"摄像头原始亮度：{current_brightness}")
        print(f"综合亮度算法评分：{comprehensive_brightness}")
        print(f"神经网络预测亮度：{predicted_score}")
        print(f"最终亮度：{final_brightness}")
        
        # 根据最终亮度判断屏幕亮度水平
        if final_brightness < 20:
            print("最终结论：屏幕整体极暗")
        elif final_brightness < 40:
            print("最终结论：屏幕整体较暗")
        elif final_brightness < 60:
            print("最终结论：屏幕整体亮度适中")
        elif final_brightness < 80:
            print("最终结论：屏幕整体较亮")
        else:
            print("最终结论：屏幕整体极亮")
        
        # 调整亮度到目标值
        target_brightness = final_brightness  # 使用神经网络预测的亮度作为目标亮度
        
        print(f"\n=== 亮度调整尝试 ===")
        success = adjust_brightness(target_brightness)
        if success:
            print("屏幕亮度调整成功")
        else:
            print("屏幕亮度调整失败")
        
    else:
        print("无法捕获图像帧，无法调整亮度")



