"""设备与调光诊断脚本（实机验证用，默认只读）。

用法：
    python tools/device_probe.py                 # 只读：列出设备、身份、调光能力
    python tools/device_probe.py --set 40        # 把每块支持调光的屏设为 40%，并回读校验
    python tools/device_probe.py --set 40 --id scr-xxxxxxxxxxxx

需要在 source/ 目录下运行：
    cd source
    python tools/device_probe.py
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import devices  # noqa: E402
import liangdu  # noqa: E402


def dump(title, data):
    print(f"\n=== {title} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def wmi_alignment():
    """对比 WmiMonitorID / WmiMonitorBrightnessMethods / WmiMonitorBrightness 的 InstanceName。"""
    try:
        import pythoncom
        import wmi
    except Exception as exc:
        return {"error": f"WMI 不可用: {exc}"}
    initialized = False
    try:
        pythoncom.CoInitialize()
        initialized = True
        client = wmi.WMI(namespace="root\\wmi")
        ids = [str(item.InstanceName) for item in client.WmiMonitorID()]
        methods = [str(item.InstanceName) for item in client.WmiMonitorBrightnessMethods()]
        levels = {
            str(item.InstanceName): int(item.CurrentBrightness)
            for item in client.WmiMonitorBrightness()
        }
        return {
            "monitor_id": ids,
            "brightness_methods": methods,
            "brightness_current": levels,
            "id_without_method": [name for name in ids if name not in methods],
            "method_without_id": [name for name in methods if name not in ids],
            "wmi_positional_match": ids == methods,
        }
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="设备与调光诊断")
    parser.add_argument("--set", type=int, default=None, help="写入的目标亮度 0-100（默认只读）")
    parser.add_argument("--id", dest="screen_id", default=None, help="只对指定屏幕 ID 写入")
    args = parser.parse_args()

    dump("DirectShow 状态", devices.directshow_status())
    dump("DirectShow 摄像头", devices.enumerate_dshow_cameras())
    dump("OpenCV 合并后的摄像头", liangdu.get_available_cameras(force_refresh=True))
    dump("显示设备（EnumDisplayDevices）", devices.enumerate_display_devices())
    dump("WMI InstanceName 对齐", wmi_alignment())

    screens = liangdu.get_available_monitors(force_refresh=True)
    dump("屏幕（WMI 身份 + DDC 回退）", screens)

    capabilities = liangdu.list_brightness_capabilities(force_refresh=True)
    dump("调光能力", capabilities)

    if args.set is None:
        return

    targets = {
        screen["id"]: args.set
        for screen in capabilities
        if screen.get("supported") and (args.screen_id is None or screen["id"] == args.screen_id)
    }
    if not targets:
        print("\n没有可写入的屏幕（都不支持软件调光，或 ID 不匹配）")
        return

    print(f"\n=== 写入亮度 {args.set}% ===")
    results = liangdu.set_brightness_targets(targets, screens=capabilities, verify=True)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
