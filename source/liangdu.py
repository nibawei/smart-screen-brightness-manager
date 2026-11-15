import atexit
import ctypes
import ctypes.wintypes
import time

import cv2
import numpy as np
import pythoncom
import wmi

import devices

# 目标函数版本：模型文件里记录这个值，版本不一致时应用会重新训练，
# 避免沿用「屏幕越暗 → 推荐越亮」的旧模型。
TARGET_VERSION = 2

# 场景模型常量：摄像头读数 = 环境光贡献 + 屏幕发光贡献
SCENE_FLOOR = 0.04   # 全黑环境下摄像头读数的下限（0-1，线性亮度）
SCENE_RANGE = 0.82   # 环境光照对读数的增益范围（0-1，线性亮度）
SCREEN_GLOW_MIN = 0.02   # 屏幕发光对摄像头读数的耦合系数下限
SCREEN_GLOW_MAX = 0.10   # 上限：屏幕主要照亮环境，不会让整个画面翻倍


def ambient_to_target(ambient_level):
    """环境光照水平（0-1）→ 舒适屏幕亮度（0-100）

    这是唯一的「目标亮度」来源：只与环境光照有关，与屏幕上显示什么内容无关。
    暗环境下给出偏低的亮度，明亮环境下给出偏高的亮度，中间段近似线性。

    Args:
        ambient_level: 环境光照水平，0 表示全黑，1 表示很亮

    Returns:
        float: 0-100 的推荐亮度
    """
    ambient_level = np.clip(np.asarray(ambient_level, dtype=np.float64), 0.0, 1.0)
    return 12.0 + 83.0 / (1.0 + np.exp(-(ambient_level - 0.42) / 0.16))


def get_available_cameras(force_refresh=False):
    """获取所有可用的摄像头列表

    身份与名称来自 `devices.enumerate_cameras`（DirectShow DevicePath / FriendlyName），
    可打开序号来自 OpenCV；两者按顺序合并，因此插拔后仍能认出同一台设备。

    Args:
        force_refresh: 是否强制刷新，忽略缓存

    Returns:
        list: 每项包含 index / name / camera_id / device_path / resolution / id_source
    """
    try:
        return devices.enumerate_cameras(force_refresh=force_refresh)
    except Exception as e:
        print(f"枚举摄像头失败：{e}")
        return [{
            'index': 0,
            'name': '默认摄像头',
            'camera_id': 'cam-fp-unknown',
            'device_path': '',
            'resolution': '',
            'id_source': 'placeholder',
        }]


def get_available_monitors(force_refresh=False):
    """获取所有可用的显示器列表

    主键为 WMI `InstanceName`（`devices.enumerate_screens`），另带 `\\.\DISPLAYn`、
    是否支持软件调光与当前亮度。

    Args:
        force_refresh: 是否强制刷新，忽略缓存

    Returns:
        list: 每项包含 index / id / name / instance_name / display_name / supported / backend / current
    """
    try:
        return devices.enumerate_screens(force_refresh=force_refresh)
    except Exception as e:
        print(f"枚举显示器失败：{e}")
        return [{
            'index': 0,
            'id': 'scr-unknown',
            'name': '默认显示器',
            'instance_name': '',
            'display_name': '',
            'supported': False,
            'backend': 'none',
            'current': None,
        }]


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
    
    # 环境光照估计：把读数还原成线性亮度后，单调映射到环境光照水平。
    # 屏幕暗度、屏幕 RGB 这类直接反映「屏幕内容亮度」的量不再参与目标计算，
    # 否则切到深色主题就会把推荐亮度推高，形成「屏幕越暗 → 推荐越亮」的正反馈。
    luma = np.power(np.clip(avg_weighted_brightness / 255.0, 0.0, 1.0), 2.2)
    ambient_level = float(np.clip((luma - SCENE_FLOOR) / SCENE_RANGE, 0.0, 1.0))
    final_brightness = int(round(ambient_to_target(ambient_level)))
    
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

# ---------------------------------------------------------------------------
# 每屏亮度控制：WMI 优先，DDC/CI 回退
# ---------------------------------------------------------------------------

VCP_BRIGHTNESS = 0x10

_DXVA2 = {"lib": None, "tried": False, "error": ""}


class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [
        ("hPhysicalMonitor", ctypes.wintypes.HANDLE),
        ("szPhysicalMonitorDescription", ctypes.wintypes.WCHAR * 128),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szDevice", ctypes.wintypes.WCHAR * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    ctypes.wintypes.HMONITOR,
    ctypes.wintypes.HDC,
    ctypes.POINTER(ctypes.wintypes.RECT),
    ctypes.c_void_p,
)


def _load_dxva2():
    """加载 dxva2.dll 并声明签名；DDC/CI 句柄是 64 位，必须设 argtypes 否则会被截断。"""
    if _DXVA2["tried"]:
        return _DXVA2["lib"]
    _DXVA2["tried"] = True
    try:
        lib = ctypes.WinDLL("dxva2.dll")
        lib.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [
            ctypes.wintypes.HMONITOR, ctypes.POINTER(ctypes.wintypes.DWORD)
        ]
        lib.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = ctypes.wintypes.BOOL
        lib.GetPhysicalMonitorsFromHMONITOR.argtypes = [
            ctypes.wintypes.HMONITOR, ctypes.wintypes.DWORD, ctypes.POINTER(_PHYSICAL_MONITOR)
        ]
        lib.GetPhysicalMonitorsFromHMONITOR.restype = ctypes.wintypes.BOOL
        lib.SetVCPFeature.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.BYTE, ctypes.wintypes.DWORD]
        lib.SetVCPFeature.restype = ctypes.wintypes.BOOL
        lib.GetVCPFeatureAndVCPFeatureReply.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.wintypes.BYTE, ctypes.c_void_p,
            ctypes.POINTER(ctypes.wintypes.DWORD), ctypes.POINTER(ctypes.wintypes.DWORD),
        ]
        lib.GetVCPFeatureAndVCPFeatureReply.restype = ctypes.wintypes.BOOL
        lib.DestroyPhysicalMonitors.argtypes = [ctypes.wintypes.DWORD, ctypes.POINTER(_PHYSICAL_MONITOR)]
        lib.DestroyPhysicalMonitors.restype = ctypes.wintypes.BOOL
    except Exception as exc:
        _DXVA2["error"] = f"dxva2.dll 不可用: {exc}"
        return None
    _DXVA2["lib"] = lib
    return lib


def ddc_status():
    """DDC/CI 是否可用（供诊断脚本/UI 展示）。"""
    lib = _load_dxva2()
    return {"available": lib is not None, "error": _DXVA2["error"]}


def _display_monitors():
    """EnumDisplayMonitors → [(hmonitor, '\\\\.\\DISPLAYn')]。"""
    monitors = []
    user32 = ctypes.windll.user32
    user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
    user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [
        ctypes.wintypes.HDC, ctypes.POINTER(ctypes.wintypes.RECT), _MONITORENUMPROC, ctypes.wintypes.LPARAM
    ]
    user32.EnumDisplayMonitors.restype = ctypes.wintypes.BOOL

    def _callback(hmonitor, hdc, rect, data):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            monitors.append((hmonitor, info.szDevice))
        return 1

    user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_callback), 0)
    return monitors


def _ddc_handles(display_name):
    """拿到 `\\.\DISPLAYn` 对应的物理显示器句柄数组；失败返回 None。"""
    lib = _load_dxva2()
    if lib is None or not display_name:
        return None
    hmonitor = None
    for handle, device in _display_monitors():
        if str(device).upper() == str(display_name).upper():
            hmonitor = handle
            break
    if hmonitor is None:
        return None
    count = ctypes.wintypes.DWORD()
    if not lib.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)) or count.value == 0:
        return None
    handles = (_PHYSICAL_MONITOR * count.value)()
    if not lib.GetPhysicalMonitorsFromHMONITOR(hmonitor, count.value, handles):
        return None
    return handles


def _ddc_destroy(handles):
    lib = _DXVA2["lib"]
    if lib is None or not handles:
        return
    try:
        lib.DestroyPhysicalMonitors(len(handles), handles)
    except Exception:
        pass


def _ddc_probe(handle):
    """读 VCP 0x10 的 (current, max)；不支持返回 (None, None)。"""
    lib = _DXVA2["lib"]
    current = ctypes.wintypes.DWORD()
    maximum = ctypes.wintypes.DWORD()
    try:
        ok = lib.GetVCPFeatureAndVCPFeatureReply(
            handle, VCP_BRIGHTNESS, None, ctypes.byref(current), ctypes.byref(maximum)
        )
    except Exception:
        return None, None
    if not ok:
        return None, None
    return int(current.value), (int(maximum.value) or 100)


def _ddc_probe_display(display_name):
    """探测某块屏的 DDC/CI 亮度能力。

    Returns:
        dict: available / current / max
    """
    handles = _ddc_handles(display_name)
    if not handles:
        return {"available": False, "current": None, "max": None}
    try:
        for monitor in handles:
            current, maximum = _ddc_probe(monitor.hPhysicalMonitor)
            if current is not None:
                return {"available": True, "current": current, "max": maximum}
        return {"available": False, "current": None, "max": None}
    finally:
        _ddc_destroy(handles)


def _ddc_read(display_name):
    return _ddc_probe_display(display_name).get("current")


def _ddc_read_max(display_name):
    return _ddc_probe_display(display_name).get("max")


def _ddc_set(display_name, value):
    """写 DDC/CI 亮度（按 VCP 最大值缩放），带重试与回读。

    一些外接屏（HDMI/DP）第一次写会失败或需要一点时间生效，所以每块物理屏最多试两次；
    回读与目标差 >2% 视为没写进去，继续尝试。

    Returns:
        tuple: (ok, 回读百分比, 错误信息)
    """
    handles = _ddc_handles(display_name)
    if not handles:
        return False, None, "未找到 DDC/CI 物理显示器"
    lib = _DXVA2["lib"]
    target_percent = max(0, min(100, int(round(float(value)))))
    last_error = "SetVCPFeature 失败"
    try:
        for monitor in handles:
            handle = monitor.hPhysicalMonitor
            current, maximum = _ddc_probe(handle)
            if current is None:
                continue
            raw_target = int(round(target_percent / 100.0 * maximum))
            for _ in range(2):
                try:
                    ok = bool(lib.SetVCPFeature(handle, VCP_BRIGHTNESS, raw_target))
                except Exception as exc:
                    ok = False
                    last_error = str(exc)
                time.sleep(0.05)
                readback, _ = _ddc_probe(handle)
                if ok and readback is not None:
                    percent = int(round(readback / maximum * 100))
                    if abs(percent - target_percent) <= 2:
                        return True, percent, ""
                    last_error = f"回读 {percent}%，与目标 {target_percent}% 不一致"
                elif not ok:
                    last_error = "SetVCPFeature 失败"
        return False, None, last_error
    finally:
        _ddc_destroy(handles)


# ---------------------------------------------------------------------------
# 软件调光兜底（伽马表）：DDC/WMI 都不可用时仍能压暗屏幕
# ---------------------------------------------------------------------------

_GAMMA_APPLIED = {}


def _build_gamma_ramp(brightness):
    """把 0-100 的亮度映射成 3×256 的伽马表（100 = 恒等，0 = 最暗但不是全黑）。"""
    level = max(0.0, min(100.0, float(brightness))) / 100.0
    gamma = 1.0 + (1.0 - level) * 2.2
    ramp = (ctypes.c_ushort * 768)()
    for channel in range(3):
        base = channel * 256
        for index in range(256):
            ramp[base + index] = int(round(65535.0 * (index / 255.0) ** gamma))
    return ramp


def _gamma_set(display_name, brightness):
    """软件调光：只能压暗，100 表示恢复原样。

    Returns:
        tuple: (ok, 当前值, 错误信息)
    """
    if not display_name:
        return False, None, "缺少显示设备名，无法软件调光"
    try:
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateDCW.restype = ctypes.wintypes.HDC
        gdi32.CreateDCW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR, ctypes.c_void_p,
        ]
        gdi32.SetDeviceGammaRamp.argtypes = [ctypes.wintypes.HDC, ctypes.c_void_p]
        gdi32.SetDeviceGammaRamp.restype = ctypes.wintypes.BOOL
        hdc = gdi32.CreateDCW("DISPLAY", str(display_name), None, None)
    except Exception as exc:
        return False, None, f"创建显示 DC 失败: {exc}"
    if not hdc:
        return False, None, "创建显示 DC 失败"
    try:
        ramp = _build_gamma_ramp(brightness)
        ok = gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
        if not ok:
            return False, None, "SetDeviceGammaRamp 被驱动拒绝（HDR 模式下常见）"
        level = int(round(float(brightness)))
        _GAMMA_APPLIED[display_name] = level
        return True, level, ""
    except Exception as exc:
        return False, None, str(exc)
    finally:
        try:
            ctypes.windll.gdi32.DeleteDC(hdc)
        except Exception:
            pass


def _gamma_reset(display_name):
    """把某块屏的伽马表恢复成恒等。"""
    if not display_name:
        return
    try:
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateDCW.restype = ctypes.wintypes.HDC
        gdi32.CreateDCW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR, ctypes.c_void_p,
        ]
        gdi32.SetDeviceGammaRamp.argtypes = [ctypes.wintypes.HDC, ctypes.c_void_p]
        gdi32.SetDeviceGammaRamp.restype = ctypes.wintypes.BOOL
        hdc = gdi32.CreateDCW("DISPLAY", str(display_name), None, None)
        if hdc:
            ramp = _build_gamma_ramp(100)
            gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
            ctypes.windll.gdi32.DeleteDC(hdc)
    except Exception:
        pass
    _GAMMA_APPLIED.pop(display_name, None)


def restore_gamma_ramps():
    """进程退出时恢复所有被软件调光改过的屏幕。"""
    for display_name in list(_GAMMA_APPLIED):
        _gamma_reset(display_name)
    _GAMMA_APPLIED.clear()


atexit.register(restore_gamma_ramps)



def _wmi_set(instance_name, value, position=0):
    """按 InstanceName 写入 WMI 亮度；找不到时按序号回退。

    Returns:
        tuple: (ok, 回读值, 提示信息)
    """
    import wmi as wmi_module

    com_initialized = False
    try:
        pythoncom.CoInitialize()
        com_initialized = True
    except Exception:
        pass
    try:
        w = wmi_module.WMI(namespace="root\\wmi")
        monitors = list(w.WmiMonitorBrightnessMethods())
        if not monitors:
            return False, None, "WMI 未找到可调光的显示器"
        target = None
        for monitor in monitors:
            if str(getattr(monitor, "InstanceName", "")).upper() == str(instance_name).upper():
                target = monitor
                break
        message = ""
        if target is None:
            index = max(0, min(int(position), len(monitors) - 1))
            target = monitors[index]
            message = "WMI InstanceName 未匹配，已按序号回退"
        target.WmiSetBrightness(int(value), 0)
        current = None
        try:
            for item in w.WmiMonitorBrightness():
                if str(item.InstanceName).upper() == str(target.InstanceName).upper():
                    current = int(item.CurrentBrightness)
                    break
        except Exception:
            current = None
        return True, current, message
    except Exception as exc:
        return False, None, str(exc)
    finally:
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


_INTERNAL_CONNECTIONS = {"内置屏", "LVDS", "DisplayPort(内置)", "UDI(内置)"}


def _backend_order(screen):
    """按连接类型决定调光后端优先级。

    外接屏（HDMI / DisplayPort / DVI…）优先 DDC/CI：Windows 对附屏的 WMI 亮度支持
    经常是「列得出来但写不进去」；内置屏优先 WMI。两者都失败时用伽马表软件调光兜底。
    """
    connection = str((screen.get("params") or {}).get("connection") or "")
    if connection in _INTERNAL_CONNECTIONS:
        return ["wmi", "ddc", "gamma"]
    return ["ddc", "wmi", "gamma"]


def list_brightness_capabilities(force_refresh=False):
    """列出每块屏的调光后端（按优先级），并探测 DDC/CI 是否可用。

    返回的 `backends` 是本次可尝试的顺序，最后一项通常是 `gamma`（软件调光兜底）；
    `backend` 只是首选，实际写入失败时会自动降级。

    Returns:
        list[dict]: devices.enumerate_screens 的结果，另含 backends / backend / current /
                    supported / software_only / params
    """
    screens = get_available_monitors(force_refresh=force_refresh)
    result = []
    for screen in screens:
        wmi_available = bool(screen.get("supported"))
        ddc = _ddc_probe_display(screen.get("display_name"))
        ddc_available = bool(ddc.get("available"))

        backends = []
        for backend in _backend_order(screen):
            if backend == "wmi" and wmi_available:
                backends.append("wmi")
            elif backend == "ddc" and ddc_available:
                backends.append("ddc")
            elif backend == "gamma" and screen.get("display_name"):
                backends.append("gamma")

        physical = [item for item in backends if item != "gamma"]
        backend = backends[0] if backends else "none"

        current = screen.get("current") if wmi_available else None
        if ddc_available and ddc.get("current") is not None:
            ddc_percent = int(round(ddc["current"] / max(ddc.get("max") or 100, 1) * 100))
            if current is None or backend == "ddc":
                current = ddc_percent

        params = dict(screen.get("params") or {})
        params.update({
            "backend": backend,
            "backends": backends,
            "ddc": ddc_available,
            "software_only": bool(backends) and not physical,
        })
        if ddc_available:
            params["brightness_max"] = ddc.get("max")
        elif wmi_available:
            params["brightness_max"] = 100

        result.append({
            **screen,
            "backend": backend,
            "backends": backends,
            "supported": bool(backends),
            "software_only": params["software_only"],
            "current": current,
            "params": params,
        })
    return result


def set_brightness_targets(targets, screens=None, verify=True):
    """按屏幕 ID 写入亮度，按 backends 顺序自动降级。

    顺序通常是：外接屏 DDC → WMI → 伽马；内置屏 WMI → DDC → 伽马。
    某个后端写失败或回读不一致时自动尝试下一个，全部失败才返回错误。

    Args:
        targets: {screen_id: 0-100}
        screens: 可用屏幕列表（默认现枚举一次）
        verify: 物理后端写后回读校验，偏差 > 2 视为失败

    Returns:
        dict: {screen_id: {"ok", "backend", "current", "error"}}
    """
    if screens is None:
        screens = list_brightness_capabilities(force_refresh=True)
    by_id = {screen["id"]: screen for screen in screens}
    results = {}
    for screen_id, value in targets.items():
        screen = by_id.get(screen_id)
        if screen is None:
            results[screen_id] = {"ok": False, "backend": "none", "current": None, "error": "屏幕不在当前列表"}
            continue

        target = max(0, min(100, int(round(float(value)))))
        backends = list(screen.get("backends") or [])
        if not backends:
            preferred = screen.get("backend")
            if preferred and preferred != "none":
                backends = [preferred]
        if "gamma" not in backends and screen.get("display_name"):
            backends.append("gamma")

        errors = []
        settled = False
        for backend in backends:
            display_name = screen.get("display_name", "")
            if backend == "wmi":
                ok, current, error = _wmi_set(
                    screen.get("instance_name", ""), target, screen.get("index", 0)
                )
            elif backend == "ddc":
                ok, current, error = _ddc_set(display_name, target)
            elif backend == "gamma":
                ok, current, error = _gamma_set(display_name, target)
            else:
                continue

            if ok and verify and backend in ("wmi", "ddc") and current is not None:
                if abs(current - target) > 2:
                    ok = False
                    error = f"写入后回读 {current}，与目标 {target} 不一致"

            if ok:
                if backend != "gamma" and display_name in _GAMMA_APPLIED:
                    _gamma_reset(display_name)
                results[screen_id] = {
                    "ok": True, "backend": backend, "current": current, "error": ""
                }
                settled = True
                break
            errors.append(f"{backend}: {error or '失败'}")

        if not settled:
            results[screen_id] = {
                "ok": False,
                "backend": "none",
                "current": None,
                "error": "；".join(errors) or "没有可用的调光后端",
            }
    return results



def adjust_brightness(target_brightness, monitor_indices=None):
    """兼容旧接口：把同一个亮度写到选中的屏幕序号上。

    Args:
        target_brightness: 0-100
        monitor_indices: 屏幕枚举序号列表，None 表示全部

    Returns:
        bool: 是否至少有一块屏写入成功
    """
    screens = get_available_monitors()
    selected = None if monitor_indices is None else set(int(i) for i in monitor_indices)
    targets = {}
    for screen in screens:
        if selected is None or screen.get("index") in selected:
            targets[screen["id"]] = target_brightness
    if not targets:
        return False
    results = set_brightness_targets(targets, screens=screens)
    return any(item["ok"] for item in results.values())


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
        
        # He 初始化（ReLU）/ Xavier 初始化（tanh）
        # 旧实现用 0.01 的固定缩放，偶发「所有隐藏层 ReLU 全部失活 → 输出恒定」，
        # 初始化尺度随扇入自适应后，激活值和梯度都能保持在合理量级。
        def _init_weight(fan_in, fan_out):
            scale = 2.0 if self.activation == 'relu' else 1.0
            return np.random.randn(fan_in, fan_out) * np.sqrt(scale / max(fan_in, 1))
        
        # 输入层到第一个隐藏层
        self.weights.append(_init_weight(input_size, hidden_sizes[0]))
        self.biases.append(np.zeros((1, hidden_sizes[0])))
        
        # 隐藏层之间的连接
        for i in range(self.num_hidden_layers - 1):
            self.weights.append(_init_weight(hidden_sizes[i], hidden_sizes[i+1]))
            self.biases.append(np.zeros((1, hidden_sizes[i+1])))
        
        # 最后一个隐藏层到输出层
        self.weights.append(_init_weight(hidden_sizes[-1], output_size))
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
        
        # 新建模型的训练目标即为当前版本
        self.target_version = TARGET_VERSION
    
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
    
    def _normalize_features(self, features_array):
        """把原始特征映射到 0-1 区间

        单样本和批量训练共用同一套映射，避免两条路径的归一化方式不一致。

        Args:
            features_array: 形状为 (14,) 或 (N, 14) 的特征数组

        Returns:
            np.ndarray: 与输入同形状的 float32 归一化特征
        """
        features_array = np.asarray(features_array, dtype=np.float64)
        normalized_features = np.zeros_like(features_array, dtype=np.float32)
        
        # 平均亮度（0-255）
        normalized_features[..., 0] = features_array[..., 0] / 255
        # 对比度值、感知亮度、空间加权亮度（0-255）
        normalized_features[..., [1, 8, 9, 13]] = features_array[..., [1, 8, 9, 13]] / 255
        # 亮度变化率（0-255）
        normalized_features[..., 2] = np.minimum(features_array[..., 2] / 255, 1.0)
        # 移动平均亮度（0-255）
        normalized_features[..., 3] = features_array[..., 3] / 255
        # RGB值（0-255）
        normalized_features[..., [4, 5, 6]] = features_array[..., [4, 5, 6]] / 255
        # 色彩饱和度（0-255）
        normalized_features[..., 7] = features_array[..., 7] / 255
        # 亮度峰度（0-6，越接近3越好）
        normalized_features[..., 10] = np.clip((3.0 - np.abs(features_array[..., 10] - 3.0)) / 3.0, 0.0, 1.0)
        # 亮度偏度（-5到5，越接近0越好）
        normalized_features[..., 11] = np.clip((1.0 - np.abs(features_array[..., 11]) / 10.0), 0.0, 1.0)
        # 清晰度（0-无上限，需要归一化到0-1）
        normalized_features[..., 12] = np.minimum(features_array[..., 12] / 2000, 1.0)
        
        return normalized_features
    
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
        
        # 归一化输入特征（与批量训练共用同一套映射）
        normalized_input = self._normalize_features(np.asarray(features, dtype=np.float64)).reshape(1, -1)
        
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
    
    def _forward_batch(self, normalized_input):
        """对一批归一化输入执行前向传播

        Args:
            normalized_input: 形状为 (N, input_size) 的归一化特征

        Returns:
            tuple: (输出 (N, 1), 各层激活值列表, 各层 z 值列表)
        """
        activations = [normalized_input]
        z_values = []
        current_input = normalized_input

        for i in range(self.num_hidden_layers):
            z = np.dot(current_input, self.weights[i]) + self.biases[i]
            z_values.append(z)
            if self.activation == 'relu':
                activation = np.maximum(0, z)
            else:  # tanh
                activation = np.tanh(z)
            activations.append(activation)
            current_input = activation

        output_z = np.dot(current_input, self.weights[-1]) + self.biases[-1]
        z_values.append(output_z)
        output = 1 / (1 + np.exp(-output_z))
        activations.append(output)
        return output, activations, z_values

    def _apply_gradients(self, grads_w, grads_b, learning_rate):
        """把整批梯度一次性应用到所有参数（Adam 或 SGD）

        Args:
            grads_w: 每一层权重的梯度列表
            grads_b: 每一层偏置的梯度列表
            learning_rate: 当前学习率
        """
        if self.use_adam:
            self.t += 1
            bias_correction1 = 1 - self.beta1 ** self.t
            bias_correction2 = 1 - self.beta2 ** self.t

        for i in range(len(self.weights)):
            g_w = grads_w[i]
            g_b = grads_b[i]
            if self.use_adam:
                self.m_weights[i] = self.beta1 * self.m_weights[i] + (1 - self.beta1) * g_w
                self.v_weights[i] = self.beta2 * self.v_weights[i] + (1 - self.beta2) * (g_w ** 2)
                m_hat_w = self.m_weights[i] / bias_correction1
                v_hat_w = self.v_weights[i] / bias_correction2
                self.weights[i] -= learning_rate * m_hat_w / (np.sqrt(v_hat_w) + self.epsilon)

                self.m_biases[i] = self.beta1 * self.m_biases[i] + (1 - self.beta1) * g_b
                self.v_biases[i] = self.beta2 * self.v_biases[i] + (1 - self.beta2) * (g_b ** 2)
                m_hat_b = self.m_biases[i] / bias_correction1
                v_hat_b = self.v_biases[i] / bias_correction2
                self.biases[i] -= learning_rate * m_hat_b / (np.sqrt(v_hat_b) + self.epsilon)
            else:
                self.weights[i] -= learning_rate * g_w
                self.biases[i] -= learning_rate * g_b

    def train(self, X, y, learning_rate=0.001, epochs=2000, batch_size=64, lr_decay=0.995,
              log_callback=None, val_split=0.15, patience=120, verbose_interval=100):
        """训练神经网络（批量向量化实现）

        目标函数、激活函数与 Adam 公式与逐样本实现完全一致，只是把一个批次内
        所有样本的梯度合并成一次矩阵运算，训练速度约提升两个数量级。

        Args:
            X: 输入数据（特征列表，每个元素是一个特征向量）
            y: 目标输出值列表（0-100）
            learning_rate: 学习率
            epochs: 训练轮数
            batch_size: 批量大小
            lr_decay: 保留参数，兼容旧调用（当前使用固定学习率）
            log_callback: 日志回调函数，用于将训练信息传递给GUI界面
            val_split: 验证集比例（样本数 >= 40 时启用），用于早停
            patience: 验证集连续多少轮没有改善就停止
            verbose_interval: 每隔多少轮输出一次日志
        """
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64).reshape(-1)
        if X.shape[0] == 0:
            return

        normalized_X = self._normalize_features(X)
        normalized_y = np.clip(y, 0.0, 100.0) / 100.0

        # 划分训练集 / 验证集
        n_samples = normalized_X.shape[0]
        n_val = int(round(n_samples * val_split)) if n_samples >= 40 else 0
        indices = np.random.permutation(n_samples)
        val_indices = indices[:n_val]
        train_indices = indices[n_val:]

        X_train = normalized_X[train_indices]
        y_train = normalized_y[train_indices]
        X_val = normalized_X[val_indices] if n_val else None
        y_val = normalized_y[val_indices] if n_val else None

        batch_size = max(1, int(batch_size))
        best_val_loss = np.inf
        best_weights = None
        best_biases = None
        epochs_without_improvement = 0

        for epoch in range(epochs):
            # 使用固定学习率，不进行衰减（与旧实现保持一致）
            current_lr = learning_rate

            permutation = np.random.permutation(X_train.shape[0])
            for start in range(0, X_train.shape[0], batch_size):
                batch_indices = permutation[start:start + batch_size]
                if batch_indices.size == 0:
                    continue
                batch_X = X_train[batch_indices]
                batch_y = y_train[batch_indices].reshape(-1, 1)

                output, activations, z_values = self._forward_batch(batch_X)

                # 输出层梯度（sigmoid 导数 * MSE 导数），按批大小取平均
                delta = (output - batch_y) * output * (1 - output) / batch_X.shape[0]

                grads_w = [None] * len(self.weights)
                grads_b = [None] * len(self.biases)

                grads_w[-1] = activations[-2].T.dot(delta)
                grads_b[-1] = np.sum(delta, axis=0, keepdims=True)

                # 反向传播通过隐藏层
                for k in range(self.num_hidden_layers, 0, -1):
                    if self.activation == 'relu':
                        delta = delta.dot(self.weights[k].T) * relu_derivative(z_values[k - 1])
                    else:  # tanh
                        delta = delta.dot(self.weights[k].T) * tanh_derivative(activations[k])
                    grads_w[k - 1] = activations[k - 1].T.dot(delta)
                    grads_b[k - 1] = np.sum(delta, axis=0, keepdims=True)

                self._apply_gradients(grads_w, grads_b, current_lr)

            # 定期输出训练/验证损失
            if epoch % verbose_interval == 0 or epoch == epochs - 1:
                train_predictions = self._forward_batch(X_train)[0][:, 0] * 100
                avg_loss = np.mean(0.5 * (train_predictions - y_train * 100) ** 2)
                mae = np.mean(np.abs(train_predictions - y_train * 100))

                log_msg = f"Epoch {epoch}, Loss: {avg_loss:.4f}, MAE: {mae:.2f}"
                val_loss = None
                if X_val is not None:
                    val_predictions = self._forward_batch(X_val)[0][:, 0] * 100
                    val_loss = np.mean(0.5 * (val_predictions - y_val * 100) ** 2)
                    log_msg += f", ValLoss: {val_loss:.4f}"
                if log_callback:
                    log_callback(log_msg)

                # 当损失值小于0.5时，提前收敛（保留原有行为）
                if avg_loss < 0.5:
                    if log_callback:
                        log_callback("损失值已小于0.5，提前收敛，训练结束")
                    self.clear_cache()
                    return

                # 验证集早停
                if val_loss is not None:
                    if val_loss < best_val_loss - 1e-4:
                        best_val_loss = val_loss
                        best_weights = [w.copy() for w in self.weights]
                        best_biases = [b.copy() for b in self.biases]
                        epochs_without_improvement = 0
                    else:
                        epochs_without_improvement += verbose_interval
                        if epochs_without_improvement >= patience:
                            if log_callback:
                                log_callback(f"验证集 {patience} 轮没有改善，提前停止训练")
                            break

        # 回滚到验证集表现最好的参数
        if best_weights is not None:
            self.weights = best_weights
            self.biases = best_biases

        self.clear_cache()
    
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
            'use_cache': self.use_cache,
            'target_version': TARGET_VERSION,
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
        # 旧模型没有这个字段，按 0 处理，应用据此触发重新训练
        self.target_version = int(data.get('target_version', 0))
        self.num_hidden_layers = len(self.hidden_sizes)
        
        # 初始化缓存
        if self.use_cache:
            from collections import OrderedDict
            self.cache = OrderedDict()  # 缓存输入特征到输出的映射（必须支持 move_to_end）
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

def synthesize_features(ambient, screen_level, screen_content, seed=None,
                        glow_gain=None, exposure=None, texture=None, warm=None):
    """由潜在变量生成 14 维特征

    物理模型把摄像头读数拆成两部分：
        scene_luma  = 环境光照贡献（真正要学习的因素）
        screen_glow = 屏幕亮度 × 屏幕内容亮度 × 耦合系数（干扰因素）

    这个函数只负责「给定环境光和屏幕状态，摄像头会读到什么」，不含目标值，
    因此训练、离线评估、回归测试可以共用同一套前向模型。

    Args:
        ambient: 环境光照水平 0-1（数组）
        screen_level: 屏幕亮度 0-1（数组，干扰变量）
        screen_content: 屏幕内容平均亮度 0-1（数组，干扰变量）
        seed: 随机种子
        glow_gain / exposure / texture / warm: 可选，固定干扰系数（默认随机采样），
            仅用于离线评估时做单变量对照

    Returns:
        np.ndarray: 形状为 (N, 14) 的特征矩阵
    """
    rng = np.random.default_rng(seed)
    ambient = np.asarray(ambient, dtype=np.float64).reshape(-1)
    screen_level = np.asarray(screen_level, dtype=np.float64).reshape(-1)
    screen_content = np.asarray(screen_content, dtype=np.float64).reshape(-1)
    n = ambient.shape[0]

    if glow_gain is None:
        glow_gain = rng.uniform(SCREEN_GLOW_MIN, SCREEN_GLOW_MAX, n)  # 屏幕发光对摄像头读数的耦合系数
    else:
        glow_gain = np.broadcast_to(np.asarray(glow_gain, dtype=np.float64), (n,))
    if exposure is None:
        exposure = np.exp(rng.normal(0.0, 0.10, n))             # 摄像头曝光差异
    else:
        exposure = np.broadcast_to(np.asarray(exposure, dtype=np.float64), (n,))
    if texture is None:
        texture = rng.uniform(0.55, 1.45, n)                    # 画面纹理反差
    else:
        texture = np.broadcast_to(np.asarray(texture, dtype=np.float64), (n,))
    if warm is None:
        warm = rng.normal(0.0, 0.04, n)                         # 环境色温偏移
    else:
        warm = np.broadcast_to(np.asarray(warm, dtype=np.float64), (n,))

    # 摄像头读数 = 环境光 + 屏幕发光
    scene = SCENE_FLOOR + SCENE_RANGE * ambient
    glow = glow_gain * screen_level * screen_content
    mu = np.clip(scene * exposure + glow, 0.02, 0.98)

    gamma = 1.0 / 2.2
    gamma_mu = 255.0 * np.power(mu, gamma)
    sigma = (0.05 + 0.14 * texture) * 2.0 * np.sqrt(np.clip(mu * (1.0 - mu), 0.0, 0.25))
    sigma = np.clip(sigma, 0.01, 0.45)

    average_brightness = 255.0 * mu
    contrast = 255.0 * sigma
    # 伽马校正后的标准差，与真实特征提取的口径保持一致
    brightness_std = 255.0 * gamma * np.power(np.clip(mu, 0.02, 1.0), gamma - 1.0) * sigma

    avg_r = 255.0 * np.clip(mu * (1.0 + warm), 0.0, 1.0)
    avg_g = 255.0 * np.clip(mu * (1.0 - 0.15 * warm), 0.0, 1.0)
    screen_blue = 0.06 * screen_level * (0.3 + 0.7 * screen_content)
    avg_b = 255.0 * np.clip(mu * (1.0 - 0.85 * warm + screen_blue), 0.0, 1.0)

    saturation = 255.0 * np.clip(
        0.07 + 0.30 * rng.beta(1.5, 3.0, n) + 0.20 * np.abs(warm), 0.02, 0.85
    )
    perceived_brightness = np.clip(gamma_mu + rng.normal(0.0, 1.5, n), 0.0, 255.0)
    weighted_brightness = np.clip(
        gamma_mu * (1.0 + 0.05 * rng.normal(0.0, 1.0, n)), 0.0, 255.0
    )
    moving_avg = np.clip(average_brightness + rng.normal(0.0, 1.2, n), 0.0, 255.0)
    brightness_change = np.abs(rng.normal(1.5, 1.2, n))

    brightness_kurtosis = np.clip(
        3.0 + rng.normal(0.0, 1.1, n) + 2.0 * screen_content * (1.0 - screen_content),
        1.3, 12.0
    )
    brightness_skewness = np.clip(rng.normal(0.0, 0.35, n), -2.0, 2.0)
    clarity = 400.0 * np.power(contrast / 40.0, 1.7) * np.exp(rng.normal(0.0, 0.4, n))
    clarity = np.clip(clarity, 3.0, 5000.0)

    return np.column_stack([
        average_brightness, contrast, brightness_change, moving_avg,
        avg_r, avg_g, avg_b, saturation,
        perceived_brightness, brightness_std,
        brightness_kurtosis, brightness_skewness, clarity, weighted_brightness
    ])


def generate_synthetic_data(num_samples=1000, seed=None):
    """生成合成训练数据

    目标亮度只由环境光照水平决定；屏幕亮度 / 屏幕内容亮度在采样时与目标完全
    独立，因此训练出来的网络不会因为屏幕内容变亮而抬高推荐亮度。

    Args:
        num_samples: 生成的样本数量
        seed: 随机种子（便于复现）

    Returns:
        tuple: (特征矩阵列表, 目标值列表)
    """
    rng = np.random.default_rng(seed)

    ambient = rng.beta(1.6, 1.6, num_samples)          # 环境光照水平 0-1（目标唯一来源）
    screen_level = rng.uniform(0.0, 1.0, num_samples)  # 屏幕亮度 0-100%（干扰）
    screen_content = rng.beta(2.0, 2.0, num_samples)   # 屏幕内容平均亮度 0-1（干扰）

    features_matrix = synthesize_features(ambient, screen_level, screen_content, seed=seed)

    # 目标亮度：只由环境光照决定（外加一点逐用户抖动）
    target = ambient_to_target(ambient) + rng.normal(0.0, 4.0, num_samples)
    target = np.clip(target, 5.0, 100.0)

    return features_matrix.tolist(), target.tolist()

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



