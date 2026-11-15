"""设备枚举、身份与登记。

- 摄像头：DirectShow（comtypes）枚举，`DevicePath` 作为稳定 ID，FriendlyName 作为展示名。
  **扫描阶段不打开设备**，避免虚拟摄像头（手机投屏 / OBS）被意外启动；
  设备能否打开留到真正采集时判断。
- 屏幕：WMI `root\\wmi` 枚举，`WmiMonitorID.InstanceName` 作为稳定 ID；
  同时用 `EnumDisplayDevices` 解析 `\\.\DISPLAYn`，供 DDC/CI 与通知定位，
  并从注册表读 EDID 建立参数档案（厂商/型号/序列号/尺寸/连接类型）。
- 登记：`devices.json`，记录见过/训练过的设备与样本数。

本模块不导入 liangdu，避免循环依赖；comtypes / WMI 不可用时返回空列表并标注原因，
由调用方决定回退策略。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import threading
import time

SCHEMA_VERSION = 1

_CAMERA_CACHE = {"data": [], "timestamp": 0.0}
_SCREEN_CACHE = {"data": [], "timestamp": 0.0}
_CACHE_LOCK = threading.Lock()
_CACHE_SECONDS = 60.0

_EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001

# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def stable_id(prefix, text):
    """把任意字符串转成稳定短 ID。"""
    digest = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"{prefix}{digest}"


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def camera_fingerprint(name, width, height):
    """拿不到 DevicePath 时的摄像头指纹（尽力而为，插拔后可能变化）。"""
    return stable_id("cam-fp-", f"{name}|{int(width)}x{int(height)}")


# ---------------------------------------------------------------------------
# DirectShow 摄像头枚举（comtypes）
# ---------------------------------------------------------------------------

_DSHOW = {"api": None, "tried": False, "error": ""}


def _load_dshow_api():
    """延迟定义 DirectShow COM 接口；comtypes 不可用时返回 None。"""
    if _DSHOW["tried"]:
        return _DSHOW["api"]
    _DSHOW["tried"] = True
    try:
        import comtypes
        from comtypes import CLSCTX_INPROC_SERVER, COMMETHOD, GUID, HRESULT, IUnknown
        from comtypes.automation import VARIANT
        from comtypes.persist import IPersist, IPropertyBag
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        _DSHOW["error"] = f"comtypes 不可用: {exc}"
        return None

    class IPersistStream(IPersist):
        _iid_ = GUID("{00000109-0000-0000-C000-000000000046}")
        _methods_ = [
            COMMETHOD([], HRESULT, "IsDirty"),
            COMMETHOD([], HRESULT, "Load"),
            COMMETHOD([], HRESULT, "Save"),
            COMMETHOD([], HRESULT, "GetSizeMax"),
        ]

    class IMoniker(IPersistStream):
        _iid_ = GUID("{0000000F-0000-0000-C000-000000000046}")
        _methods_ = [
            COMMETHOD([], HRESULT, "BindToObject"),
            COMMETHOD([], HRESULT, "BindToStorage",
                      (["in"], ctypes.c_void_p, "pbc"),
                      (["in"], ctypes.c_void_p, "pmkToLeft"),
                      (["in"], ctypes.POINTER(GUID), "riid"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IPropertyBag)), "ppv")),
            COMMETHOD([], HRESULT, "Reduce"),
            COMMETHOD([], HRESULT, "ComposeWith"),
            COMMETHOD([], HRESULT, "Enum"),
            COMMETHOD([], HRESULT, "IsEqual"),
            COMMETHOD([], HRESULT, "Hash"),
            COMMETHOD([], HRESULT, "IsRunning"),
            COMMETHOD([], HRESULT, "GetTimeOfLastChange"),
            COMMETHOD([], HRESULT, "Inverse"),
            COMMETHOD([], HRESULT, "CommonPrefixWith"),
            COMMETHOD([], HRESULT, "RelativePathTo"),
            COMMETHOD([], HRESULT, "GetDisplayName",
                      (["in"], ctypes.c_void_p, "pbc"),
                      (["in"], ctypes.c_void_p, "pmkToLeft"),
                      (["out"], ctypes.POINTER(ctypes.c_wchar_p), "ppszDisplayName")),
            COMMETHOD([], HRESULT, "ParseDisplayName"),
            COMMETHOD([], HRESULT, "IsSystemMoniker"),
        ]

    class IEnumMoniker(IUnknown):
        _iid_ = GUID("{00000102-0000-0000-C000-000000000046}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Next",
                      (["in"], ctypes.c_ulong, "celt"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IMoniker)), "rgelt"),
                      (["out"], ctypes.POINTER(ctypes.c_ulong), "pceltFetched")),
            COMMETHOD([], HRESULT, "Skip", (["in"], ctypes.c_ulong, "celt")),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "Clone"),
        ]

    class ICreateDevEnum(IUnknown):
        _iid_ = GUID("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
        _methods_ = [
            COMMETHOD([], HRESULT, "CreateClassEnumerator",
                      (["in"], ctypes.POINTER(GUID), "clsidDeviceClass"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IEnumMoniker)), "ppEnumMoniker"),
                      (["in"], ctypes.c_ulong, "dwFlags")),
        ]

    class Api:
        pass

    api = Api()
    api.comtypes = comtypes
    api.ICreateDevEnum = ICreateDevEnum
    api.IPropertyBag = IPropertyBag
    api.VARIANT = VARIANT
    api.CLSCTX_INPROC_SERVER = CLSCTX_INPROC_SERVER
    api.CLSID_SystemDeviceEnum = GUID("{62BE5D10-60EB-11D0-BD3B-00A0C911CE86}")
    api.CLSID_VideoInputDeviceCategory = GUID("{860BB310-5D01-11D0-BD3B-00A0C911CE86}")
    _DSHOW["api"] = api
    return api


def directshow_status():
    """DirectShow 枚举是否可用（供诊断脚本/UI 展示）。"""
    api = _load_dshow_api()
    return {"available": api is not None, "error": _DSHOW["error"]}


def enumerate_dshow_cameras():
    """按 DirectShow 顺序返回摄像头（不打开设备）。

    Returns:
        list[dict]: 每项含 order / name / device_path / camera_id / id_source
    """
    api = _load_dshow_api()
    if api is None:
        return []

    result = []
    com_initialized = False
    try:
        api.comtypes.CoInitialize()
        com_initialized = True
        dev_enum = api.comtypes.CoCreateInstance(
            api.CLSID_SystemDeviceEnum,
            interface=api.ICreateDevEnum,
            clsctx=api.CLSCTX_INPROC_SERVER,
        )
        enum = dev_enum.CreateClassEnumerator(ctypes.byref(api.CLSID_VideoInputDeviceCategory), 0)
        if not enum:
            return []

        order = 0
        while True:
            fetched = enum.Next(1)
            moniker = fetched[0] if fetched else None
            if not moniker:
                break

            device_path = ""
            friendly_name = ""
            try:
                display = moniker.GetDisplayName(None, None)
                device_path = display.value if hasattr(display, "value") else (display or "")
            except Exception:
                device_path = ""
            try:
                bag = moniker.BindToStorage(None, None, ctypes.byref(api.IPropertyBag._iid_))
                name = bag.Read("FriendlyName", api.VARIANT(), None)
                if isinstance(name, str):
                    friendly_name = name
                path = bag.Read("DevicePath", api.VARIANT(), None)
                if isinstance(path, str) and path:
                    device_path = path
            except Exception:
                pass

            camera_id = stable_id("cam-", device_path) if device_path else ""
            result.append({
                "order": order,
                "name": friendly_name or f"摄像头 {order}",
                "device_path": device_path,
                "camera_id": camera_id,
                "id_source": "directshow" if camera_id else "none",
            })
            order += 1
    except Exception:
        return []
    finally:
        if com_initialized:
            try:
                api.comtypes.CoUninitialize()
            except Exception:
                pass
    return result


# ---------------------------------------------------------------------------
# 屏幕枚举：WMI + EnumDisplayDevices
# ---------------------------------------------------------------------------


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("DeviceName", wt.WCHAR * 32),
        ("DeviceString", wt.WCHAR * 128),
        ("StateFlags", wt.DWORD),
        ("DeviceID", wt.WCHAR * 128),
        ("DeviceKey", wt.WCHAR * 128),
    ]


def _new_display_device():
    dd = _DISPLAY_DEVICE()
    dd.cb = ctypes.sizeof(_DISPLAY_DEVICE)
    return dd


def enumerate_display_devices():
    """EnumDisplayDevices：返回适配器下的显示器接口列表。

    Returns:
        list[dict]: display_name（`\\\\.\\DISPLAYn`）/ device_id（EDID 接口路径）/ device_string
    """
    user32 = ctypes.windll.user32
    result = []
    dd = _new_display_device()
    adapter_index = 0
    while user32.EnumDisplayDevicesW(None, adapter_index, ctypes.byref(dd), 0):
        adapter_name = dd.DeviceName
        monitor = _new_display_device()
        monitor_index = 0
        while user32.EnumDisplayDevicesW(
            adapter_name, monitor_index, ctypes.byref(monitor), _EDD_GET_DEVICE_INTERFACE_NAME
        ):
            if monitor.DeviceID:
                result.append({
                    "display_name": adapter_name,
                    "device_id": monitor.DeviceID,
                    "device_string": monitor.DeviceString,
                })
            monitor_index += 1
            monitor = _new_display_device()
        adapter_index += 1
        dd = _new_display_device()
    return result


def _normalize_instance_name(instance_name):
    """`DISPLAY\\BOE095F\\5&...&0&UID4352_0` → `DISPLAY#BOE095F#5&...&0&UID4352`。"""
    text = (instance_name or "").strip().upper()
    if text.endswith("_0"):
        text = text[:-2]
    return text.replace("\\", "#")


def _normalize_device_id(device_id):
    """`\\\\?\\DISPLAY#BOE095F#5&...#{GUID}` → `DISPLAY#BOE095F#5&...`。"""
    text = (device_id or "").strip().upper()
    if text.startswith("\\\\?\\"):
        text = text[4:]
    if "#{" in text:
        text = text.split("#{", 1)[0]
    return text


def _decode_edid_name(raw):
    """WmiMonitorID.UserFriendlyName 是 uint16 数组，按 ASCII/UTF-8 尽力解码。"""
    if raw is None:
        return ""
    try:
        data = bytes(int(v) & 0xFF for v in raw if int(v) not in (0,))
        for encoding in ("utf-8", "ascii", "latin-1"):
            try:
                return data.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# 屏幕参数建档（EDID / 显示模式 / 连接类型）
# ---------------------------------------------------------------------------

# 常见 EDID 厂商三字母代码（不在表里的原样显示）
_PNP_BRANDS = {
    "DEL": "DELL", "SAM": "Samsung", "SMD": "Samsung", "GSM": "LG",
    "LGD": "LG Display", "BOE": "BOE", "AUO": "AU Optronics", "HWP": "HP",
    "HPN": "HP", "ACR": "Acer", "AUS": "ASUS", "BEN": "BenQ",
    "PHI": "Philips", "IVM": "iiyama", "VSC": "ViewSonic", "MSI": "MSI",
    "LEN": "Lenovo", "SHP": "Sharp", "PAN": "Panasonic", "NEC": "NEC",
    "EIZ": "EIZO", "HKC": "HKC", "INN": "Innolux", "CMN": "Innolux",
    "CSO": "CSOT", "APL": "Apple", "APP": "Apple", "HSN": "Hasee",
}

# D3DKMDT_VIDEO_OUTPUT_TECHNOLOGY → 可读名称（WMI VideoOutputTechnology）
_CONNECTION_NAMES = {
    -2: "未初始化", -1: "其他", 0: "VGA", 1: "S-Video", 2: "复合视频",
    3: "分量视频", 4: "DVI", 5: "HDMI", 6: "LVDS", 8: "D-JPN", 9: "SDI",
    10: "DisplayPort", 11: "DisplayPort(内置)", 12: "UDI", 13: "UDI(内置)",
    14: "SDTV Dongle", 15: "Miracast", 0x80000000: "内置屏",
}


def _pnp_id_to_text(first, second):
    """EDID 厂商 ID：两个字节按 5bit 拆成三个字母。"""
    value = (int(first) << 8) | int(second)
    if value == 0:
        return ""
    letters = "".join(chr(((value >> shift) & 0x1F) + 0x40) for shift in (10, 5, 0))
    return letters if letters.isalpha() else ""


def parse_edid_bytes(data):
    """解析 128 字节基础 EDID。

    Returns:
        dict: manufacturer_id / manufacturer / product_code / serial / year / week /
              size_cm / model / preferred_resolution（解析不到就不放对应键）
    """
    if not data or len(data) < 128:
        return {}
    header = [0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00]
    if [int(v) & 0xFF for v in data[:8]] != header:
        return {}

    info = {}
    pnp = _pnp_id_to_text(data[8], data[9])
    if pnp:
        info["manufacturer_id"] = pnp
        info["manufacturer"] = _PNP_BRANDS.get(pnp, pnp)
    info["product_code"] = f"{(int(data[10]) | (int(data[11]) << 8)):04X}"
    serial_number = int.from_bytes(bytes(int(v) & 0xFF for v in data[12:16]), "little")
    if serial_number:
        info["serial_number"] = serial_number

    week = int(data[16])
    year = 1990 + int(data[17])
    if year >= 1990:
        info["year"] = year
        if week:
            info["week"] = week

    width, height = int(data[21]), int(data[22])
    if width and height:
        info["size_cm"] = f"{width}x{height}"

    for offset in (54, 72, 90, 108):
        if [int(v) for v in data[offset:offset + 3]] != [0, 0, 0]:
            continue
        tag = int(data[offset + 3])
        text = bytes(int(v) & 0xFF for v in data[offset + 5:offset + 18])
        text = text.split(b"\x00")[0].decode("ascii", "ignore").strip()
        if not text:
            continue
        if tag == 0xFC:
            info["model"] = text
        elif tag == 0xFF:
            info["serial"] = text
        elif tag == 0xFE:
            info["note"] = text

    # 首选详细时序里的分辨率
    h_active = int(data[56]) | ((int(data[58]) & 0xF0) << 4)
    v_active = int(data[59]) | ((int(data[61]) & 0xF0) << 4)
    if h_active and v_active:
        info["preferred_resolution"] = f"{h_active}x{v_active}"

    # 部分便携屏/面板只在 0xFE 里写型号
    if "model" not in info and info.get("note"):
        info["model"] = info["note"]
    return info


def read_edid(device_id):
    """从注册表读取显示器的 EDID 二进制；读不到返回 None。"""
    try:
        import winreg
    except Exception:
        return None

    text = str(device_id or "")
    body = text[4:] if text.startswith("\\\\?\\") else text
    parts = body.split("#")
    if len(parts) < 3:
        return None
    manufacturer, instance = parts[1], parts[2]
    base = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{manufacturer}"
    candidates = [rf"{base}\{instance}\Device Parameters"]
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        try:
            index = 0
            while True:
                try:
                    subkey = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                if subkey.lower().startswith(instance.lower()):
                    candidates.append(rf"{base}\{subkey}\Device Parameters")
        finally:
            winreg.CloseKey(key)
    except OSError:
        pass

    for path in candidates:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
            try:
                data, _ = winreg.QueryValueEx(key, "EDID")
                return bytes(int(v) & 0xFF for v in data)
            finally:
                winreg.CloseKey(key)
        except OSError:
            continue
    return None


class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wt.WCHAR * 32),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wt.DWORD),
        ("dmDisplayFixedOutput", wt.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wt.WCHAR * 32),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
        ("dmICMMethod", wt.DWORD),
        ("dmICMIntent", wt.DWORD),
        ("dmMediaType", wt.DWORD),
        ("dmDitherType", wt.DWORD),
        ("dmReserved1", wt.DWORD),
        ("dmReserved2", wt.DWORD),
        ("dmPanningWidth", wt.DWORD),
        ("dmPanningHeight", wt.DWORD),
    ]


def display_mode(display_name):
    """当前显示模式：分辨率 + 刷新率。"""
    if not display_name:
        return {}
    mode = _DEVMODEW()
    mode.dmSize = ctypes.sizeof(_DEVMODEW)
    try:
        ok = ctypes.windll.user32.EnumDisplaySettingsW(str(display_name), -1, ctypes.byref(mode))
    except Exception:
        return {}
    if not ok:
        return {}
    result = {}
    if mode.dmPelsWidth and mode.dmPelsHeight:
        result["resolution"] = f"{mode.dmPelsWidth}x{mode.dmPelsHeight}"
    if mode.dmDisplayFrequency:
        result["refresh_hz"] = int(mode.dmDisplayFrequency)
    return result


def _connection_name(value):
    if value is None:
        return ""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    # WMI 可能给有符号或无符号，先按原值查一次（0x80000000 = 内置屏），
    # 再在 32 位有/无符号之间转换后重试。
    if number in _CONNECTION_NAMES:
        return _CONNECTION_NAMES[number]
    converted = number + 0x100000000 if number < 0 else number - 0x100000000
    if converted in _CONNECTION_NAMES:
        return _CONNECTION_NAMES[converted]
    return f"未知({number})"


def _screen_display_name(params, device):
    """优先「厂商 + 型号」，避免所有屏都叫 Generic PnP Monitor。"""
    manufacturer = str(params.get("manufacturer") or "").strip()
    model = str(params.get("model") or "").strip()
    device_string = str((device or {}).get("device_string") or "").strip()

    if model and model.lower() != "generic pnp monitor":
        if manufacturer and manufacturer.lower() not in model.lower():
            return f"{manufacturer} {model}"
        return model
    if manufacturer:
        return manufacturer
    if device_string and device_string.lower() != "generic pnp monitor":
        return device_string
    return "显示器"


def _screen_params(device, wmi_monitor=None, connection=""):
    """把 EDID、WMI、显示模式合并成一份屏幕参数档案。"""
    device = device or {}
    params = {
        "display_name": device.get("display_name", ""),
        "device_id": device.get("device_id", ""),
        "device_string": device.get("device_string", ""),
    }

    raw_edid = read_edid(device.get("device_id"))
    if raw_edid:
        params["edid"] = True
        for key, value in parse_edid_bytes(raw_edid).items():
            if value not in (None, ""):
                params[key] = value
    else:
        params["edid"] = False

    if wmi_monitor is not None:
        pnp = _decode_edid_name(getattr(wmi_monitor, "ManufacturerName", None))
        if pnp:
            params["manufacturer_id"] = pnp
            params["manufacturer"] = _PNP_BRANDS.get(pnp, pnp)
        friendly = _decode_edid_name(getattr(wmi_monitor, "UserFriendlyName", None))
        if friendly and friendly.lower() != "generic pnp monitor":
            params["model"] = friendly
        year = getattr(wmi_monitor, "YearOfManufacture", None)
        if year:
            try:
                params["year"] = int(year)
            except (TypeError, ValueError):
                pass

    if connection:
        params["connection"] = connection
        params["connection_source"] = "wmi"
    else:
        params["connection_source"] = "unknown"

    params.update(display_mode(device.get("display_name")))
    return params


def _dedupe_screen_names(screens):
    """同名屏幕追加 (1)/(2) 序号。"""
    counts = {}
    for screen in screens:
        base = screen.get("name") or "显示器"
        counts[base] = counts.get(base, 0) + 1
    seen = {}
    for screen in screens:
        base = screen.get("name") or "显示器"
        if counts.get(base, 1) > 1:
            seen[base] = seen.get(base, 0) + 1
            screen["name"] = f"{base} ({seen[base]})"
    return screens


def _screen_fallback():
    """WMI 不可用时的兜底：按显示设备列出，全部标记为不支持软件调光。"""
    screens = []
    for index, device in enumerate(enumerate_display_devices()):
        params = _screen_params(device)
        name = _screen_display_name(params, device)
        screens.append({
            "id": stable_id("scr-", device.get("device_id") or device.get("display_name") or str(index)),
            "instance_name": "",
            "name": name,
            "display_name": device.get("display_name", ""),
            "supported": False,
            "backend": "none",
            "current": None,
            "id_source": "display-device",
            "params": params,
        })
    return _dedupe_screen_names(screens)


def enumerate_wmi_screens():
    """枚举屏幕（WMI 主键 + 显示设备映射 + EDID 参数档案）。

    Returns:
        list[dict]: id / instance_name / name / display_name / supported / backend / current / params
    """
    screens = []
    com_initialized = False
    try:
        import pythoncom
        import wmi
    except Exception:
        return _screen_fallback()

    try:
        pythoncom.CoInitialize()
        com_initialized = True
        w = wmi.WMI(namespace="root\\wmi")
        try:
            monitor_ids = list(w.WmiMonitorID())
        except Exception:
            monitor_ids = []
        if not monitor_ids:
            return _screen_fallback()

        try:
            method_names = {str(m.InstanceName).upper() for m in w.WmiMonitorBrightnessMethods()}
        except Exception:
            method_names = set()
        try:
            current_levels = {
                str(b.InstanceName).upper(): int(b.CurrentBrightness)
                for b in w.WmiMonitorBrightness()
            }
        except Exception:
            current_levels = {}
        try:
            connections = {
                str(c.InstanceName).upper(): getattr(c, "VideoOutputTechnology", None)
                for c in w.WmiMonitorConnectionParams()
            }
        except Exception:
            connections = {}

        display_devices = enumerate_display_devices()
        display_map = {}
        for device in display_devices:
            key = _normalize_device_id(device.get("device_id"))
            if key:
                display_map[key] = device

        # 先按 InstanceName ↔ DeviceID 精确匹配
        matched_devices = []
        matched_keys = set()
        for monitor in monitor_ids:
            key = _normalize_instance_name(str(monitor.InstanceName))
            device = display_map.get(key)
            if device is not None:
                matched_keys.add(key)
            matched_devices.append(device)

        # 有没匹配上的：用剩余显示设备按枚举顺序补齐（两个枚举通常同序），
        # 保证每块屏都能拿到 display_name，通知定位 / DDC / 伽马才不会退回主屏
        unmatched_indices = [index for index, device in enumerate(matched_devices) if device is None]
        remaining_devices = [device for key, device in display_map.items() if key not in matched_keys]
        if unmatched_indices and len(unmatched_indices) == len(remaining_devices):
            for index, device in zip(unmatched_indices, remaining_devices):
                matched_devices[index] = device

        for index, monitor in enumerate(monitor_ids):
            instance_name = str(monitor.InstanceName)
            device = matched_devices[index] or {}
            params = _screen_params(
                device,
                wmi_monitor=monitor,
                connection=_connection_name(connections.get(instance_name.upper())),
            )
            name = _screen_display_name(params, device)
            supported = instance_name.upper() in method_names
            screens.append({
                "id": stable_id("scr-", instance_name),
                "instance_name": instance_name,
                "name": name,
                "display_name": device.get("display_name", ""),
                "supported": supported,
                "backend": "wmi" if supported else "none",
                "current": current_levels.get(instance_name.upper()),
                "id_source": "wmi-instance",
                "params": params,
            })
        return _dedupe_screen_names(screens)
    except Exception:
        return _screen_fallback()
    finally:
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass



# ---------------------------------------------------------------------------
# 对外：摄像头（仅 DirectShow 枚举，不打开设备）
# ---------------------------------------------------------------------------


def enumerate_cameras(force_refresh=False):
    """枚举摄像头：只用 DirectShow，不打开任何设备。

    打开设备（哪怕只读一帧）会让部分虚拟摄像头启动——例如手机投屏会亮起手机相机，
    OBS 虚拟摄像头也会开始出画面。所以扫描阶段绝不调用 VideoCapture；
    「能不能打开」留到真正采集时判断，失败时给出明确的错误提示。

    Returns:
        list[dict]: index（DirectShow 枚举顺序）/ name / camera_id / device_path /
                    resolution / id_source / is_virtual
    """
    with _CACHE_LOCK:
        if not force_refresh and _CAMERA_CACHE["data"]:
            if time.time() - _CAMERA_CACHE["timestamp"] < _CACHE_SECONDS:
                return list(_CAMERA_CACHE["data"])
        _CAMERA_CACHE["timestamp"] = time.time()

    cameras = []
    for item in enumerate_dshow_cameras():
        cameras.append({
            "index": item["order"],
            "name": item["name"],
            "camera_id": item["camera_id"] or camera_fingerprint(item["name"], 0, 0),
            "device_path": item["device_path"],
            "resolution": "",  # 扫描阶段不打开设备，分辨率只能在采集时才知道
            "id_source": item["id_source"] if item["camera_id"] else "fingerprint",
        })

    if not cameras:
        # DirectShow 枚举不到时才给占位，避免 UI 空列表；不会去打开 index 0
        cameras.append({
            "index": 0,
            "name": "默认摄像头",
            "camera_id": camera_fingerprint("默认摄像头", 0, 0),
            "device_path": "",
            "resolution": "0x0",
            "id_source": "placeholder",
        })

    for camera in cameras:
        camera["is_virtual"] = is_virtual_camera(camera)

    with _CACHE_LOCK:
        _CAMERA_CACHE["data"] = cameras
    return list(cameras)



_VIRTUAL_CAMERA_HINTS = (
    "vcamdevapi",      # Windows 手机连接（Phone Link）虚拟摄像头
    "virtual",
    "obs",
    "manycam",
    "snap camera",
    "虚拟",
)


def is_virtual_camera(camera):
    """判断是否为虚拟摄像头（手机投屏 / OBS 等）。

    虚拟摄像头的画面来自另一台设备，作为环境光测量源不可靠，默认选择时应避开。
    """
    path = str((camera or {}).get("device_path") or "").lower()
    name = str((camera or {}).get("name") or "").lower()
    haystack = f"{path} {name}"
    return any(hint in haystack for hint in _VIRTUAL_CAMERA_HINTS)


def choose_default_camera(cameras, saved_id=None, saved_name=None):
    """保守地选择默认摄像头。

    规则：
    1. 有保存的 ID / 名称且当前在线 → 直接用保存的；
    2. 有保存记录但设备不在线 → 返回 None（不自动换到别的摄像头，
       尤其不能悄悄换到手机虚拟摄像头）；
    3. 没有保存记录（首次运行）→ 优先第一台物理摄像头；全是虚拟摄像头时才退回第一台。
    """
    cameras = list(cameras or [])
    if not cameras:
        return None

    if saved_id:
        for camera in cameras:
            if camera.get("camera_id") == saved_id:
                return camera
    if saved_name:
        for camera in cameras:
            if camera.get("name") == saved_name:
                return camera
    if saved_id or saved_name:
        return None

    physical = [camera for camera in cameras if not is_virtual_camera(camera)]
    return physical[0] if physical else cameras[0]


def enumerate_screens(force_refresh=False):
    """枚举屏幕（带 60 秒缓存）。"""
    with _CACHE_LOCK:
        if not force_refresh and _SCREEN_CACHE["data"]:
            if time.time() - _SCREEN_CACHE["timestamp"] < _CACHE_SECONDS:
                return list(_SCREEN_CACHE["data"])
        _SCREEN_CACHE["timestamp"] = time.time()

    screens = enumerate_wmi_screens()
    if not screens:
        screens = _screen_fallback()
    with _CACHE_LOCK:
        _SCREEN_CACHE["data"] = screens
    return list(screens)


# ---------------------------------------------------------------------------
# devices.json 登记
# ---------------------------------------------------------------------------


def default_registry():
    return {"schema": SCHEMA_VERSION, "updated_at": now_iso(), "cameras": {}, "screens": {}}


def load_registry(path):
    """读取登记文件；损坏时备份为 .bak 并返回空登记。"""
    if not os.path.exists(path):
        return default_registry()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or "cameras" not in data or "screens" not in data:
            raise ValueError("登记文件结构不正确")
        data.setdefault("schema", SCHEMA_VERSION)
        data.setdefault("updated_at", now_iso())
        return data
    except Exception:
        try:
            os.replace(path, path + ".bak")
        except Exception:
            pass
        return default_registry()


def save_registry(path, registry):
    """原子写入登记文件。"""
    registry["schema"] = SCHEMA_VERSION
    registry["updated_at"] = now_iso()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def register(registry, cameras, screens):
    """把本次枚举到的设备合并进登记（不删除旧记录）。"""
    timestamp = now_iso()
    cam_map = registry.setdefault("cameras", {})
    for camera in cameras:
        camera_id = camera.get("camera_id")
        if not camera_id:
            continue
        entry = cam_map.setdefault(camera_id, {
            "first_seen": timestamp,
            "trained": False,
            "sample_count": 0,
        })
        entry.update({
            "name": camera.get("name", ""),
            "device_path": camera.get("device_path", ""),
            "resolution": camera.get("resolution", ""),
            "id_source": camera.get("id_source", ""),
            "last_seen": timestamp,
            "connected": True,
        })

    scr_map = registry.setdefault("screens", {})
    for screen in screens:
        screen_id = screen.get("id")
        if not screen_id:
            continue
        entry = scr_map.setdefault(screen_id, {
            "first_seen": timestamp,
            "trained": False,
            "sample_count": 0,
        })
        entry.update({
            "name": screen.get("name", ""),
            "instance_name": screen.get("instance_name", ""),
            "display_name": screen.get("display_name", ""),
            "backends": [screen.get("backend", "none")],
            "last_seen": timestamp,
            "connected": True,
        })
        if screen.get("params"):
            # 屏幕参数档案：EDID 厂商/型号/序列号/尺寸、分辨率、连接类型、调光能力
            entry["params"] = {**entry.get("params", {}), **screen["params"]}
        if screen.get("current") is not None:
            entry["last_brightness"] = int(screen["current"])

    connected_cameras = {c.get("camera_id") for c in cameras}
    connected_screens = {s.get("id") for s in screens}
    for camera_id, entry in cam_map.items():
        entry["connected"] = camera_id in connected_cameras
    for screen_id, entry in scr_map.items():
        entry["connected"] = screen_id in connected_screens
    return registry


def mark_camera_trained(registry, camera_id, sample_count=None):
    entry = registry.setdefault("cameras", {}).setdefault(camera_id, {})
    entry["trained"] = True
    if sample_count is not None:
        entry["sample_count"] = int(sample_count)


def mark_screen_trained(registry, screen_id, sample_count=None):
    entry = registry.setdefault("screens", {}).setdefault(screen_id, {})
    entry["trained"] = True
    if sample_count is not None:
        entry["sample_count"] = int(sample_count)
