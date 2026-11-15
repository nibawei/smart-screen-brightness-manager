import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import devices  # noqa: E402


class DeviceIdentityTest(unittest.TestCase):
    def test_stable_id_is_deterministic(self):
        first = devices.stable_id("cam-", "abc")
        second = devices.stable_id("cam-", "abc")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("cam-"))
        self.assertEqual(len(first), len("cam-") + 12)

    def test_fingerprint_changes_with_resolution(self):
        self.assertNotEqual(
            devices.camera_fingerprint("cam", 640, 480),
            devices.camera_fingerprint("cam", 1280, 720),
        )

    def test_normalize_instance_and_device_id_match(self):
        instance = "DISPLAY\\BOE095F\\5&2b1b0b3&0&UID4352_0"
        device_id = "\\\\?\\DISPLAY#BOE095F#5&2b1b0b3&0&UID4352#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
        self.assertEqual(
            devices._normalize_instance_name(instance),
            devices._normalize_device_id(device_id),
        )

    def test_decode_edid_name_strips_nulls(self):
        self.assertEqual(devices._decode_edid_name((66, 79, 69, 0, 0)), "BOE")


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="devices-test-")
        self.path = os.path.join(self.tmp, "devices.json")

    def test_register_and_reload(self):
        registry = devices.load_registry(self.path)
        cameras = [{
            "camera_id": "cam-1", "name": "Cam", "device_path": "p",
            "resolution": "640x480", "id_source": "directshow",
        }]
        screens = [{
            "id": "scr-1", "name": "Screen", "instance_name": "DISPLAY\\A",
            "display_name": "\\\\.\\DISPLAY1", "backend": "wmi", "supported": True,
        }]
        devices.register(registry, cameras, screens)
        devices.mark_camera_trained(registry, "cam-1")
        devices.mark_screen_trained(registry, "scr-1", 7)
        devices.save_registry(self.path, registry)

        loaded = devices.load_registry(self.path)
        self.assertTrue(loaded["cameras"]["cam-1"]["trained"])
        self.assertEqual(loaded["screens"]["scr-1"]["sample_count"], 7)
        self.assertTrue(loaded["cameras"]["cam-1"]["connected"])

    def test_missing_device_marked_disconnected(self):
        registry = devices.load_registry(self.path)
        devices.register(registry, [{"camera_id": "cam-1", "name": "A"}], [{"id": "scr-1", "name": "S"}])
        devices.register(registry, [], [])
        self.assertFalse(registry["cameras"]["cam-1"]["connected"])
        self.assertFalse(registry["screens"]["scr-1"]["connected"])

    def test_corrupt_registry_is_backed_up(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        registry = devices.load_registry(self.path)
        self.assertEqual(registry["cameras"], {})
        self.assertTrue(os.path.exists(self.path + ".bak"))


class CameraSelectionTest(unittest.TestCase):
    """保守的默认摄像头选择：优先物理摄像头，不会悄悄切到手机虚拟摄像头。"""

    def setUp(self):
        self.cameras = [
            {
                "index": 0, "camera_id": "cam-phone", "name": "Phone (虚拟摄像头)",
                "device_path": "\\\\?\\swd#vcamdevapi#3771fad6#...", "resolution": "1280x720",
            },
            {
                "index": 1, "camera_id": "cam-usb", "name": "USB Webcam",
                "device_path": "\\\\?\\usb#vid_1bcf&pid_2cdd#...", "resolution": "640x480",
            },
        ]

    def test_virtual_detection(self):
        self.assertTrue(devices.is_virtual_camera(self.cameras[0]))
        self.assertFalse(devices.is_virtual_camera(self.cameras[1]))

    def test_first_run_prefers_physical(self):
        chosen = devices.choose_default_camera(self.cameras)
        self.assertEqual(chosen["camera_id"], "cam-usb")

    def test_saved_device_wins(self):
        chosen = devices.choose_default_camera(self.cameras, saved_id="cam-phone")
        self.assertEqual(chosen["camera_id"], "cam-phone")

    def test_saved_name_fallback(self):
        chosen = devices.choose_default_camera(self.cameras, saved_name="Phone (虚拟摄像头)")
        self.assertEqual(chosen["camera_id"], "cam-phone")

    def test_missing_saved_device_does_not_switch(self):
        # 上次用的摄像头这次不在线：返回 None，绝不自动切到手机摄像头
        self.assertIsNone(devices.choose_default_camera(self.cameras, saved_id="cam-gone"))

    def test_only_virtual_cameras_fall_back(self):
        chosen = devices.choose_default_camera([self.cameras[0]])
        self.assertEqual(chosen["camera_id"], "cam-phone")

    def test_no_cameras(self):
        self.assertIsNone(devices.choose_default_camera([]))


class ConnectionNameTest(unittest.TestCase):
    def test_known_connections(self):
        self.assertEqual(devices._connection_name(5), "HDMI")
        self.assertEqual(devices._connection_name(10), "DisplayPort")
        self.assertEqual(devices._connection_name(4), "DVI")

    def test_internal_accepts_signed_and_unsigned(self):
        self.assertEqual(devices._connection_name(0x80000000), "内置屏")
        self.assertEqual(devices._connection_name(-2147483648), "内置屏")

    def test_unknown_and_missing(self):
        self.assertEqual(devices._connection_name(None), "")
        self.assertIn("未知", devices._connection_name(999))


class EdidTest(unittest.TestCase):
    """EDID 解析与屏幕参数档案（HDMI 屏建档用）。"""

    @staticmethod
    def sample_edid():
        data = bytearray(128)
        data[0:8] = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
        value = ((ord("D") - 0x40) << 10) | ((ord("E") - 0x40) << 5) | (ord("L") - 0x40)
        data[8] = value >> 8
        data[9] = value & 0xFF
        data[10] = 0x34  # product 0x1234
        data[11] = 0x12
        data[12:16] = (0x78).to_bytes(4, "little")
        data[16] = 20   # week
        data[17] = 33   # 1990 + 33 = 2023
        data[21] = 52   # width cm
        data[22] = 29   # height cm
        # 首选时序：1920x1080（高 4 位分别在 data[58]/data[61] 的高半字节）
        data[56], data[57], data[58] = 0x80, 0x07, 0x70
        data[59], data[60], data[61] = 0x38, 0x04, 0x40
        # 显示器名称描述符放在第三个槽位
        data[90:95] = b"\x00\x00\x00\xFC\x00"
        data[95:102] = b"U2419H\n"
        return bytes(data)

    def test_parse_basic_fields(self):
        info = devices.parse_edid_bytes(self.sample_edid())
        self.assertEqual(info["manufacturer_id"], "DEL")
        self.assertEqual(info["manufacturer"], "DELL")
        self.assertEqual(info["product_code"], "1234")
        self.assertEqual(info["serial_number"], 0x78)
        self.assertEqual(info["year"], 2023)
        self.assertEqual(info["week"], 20)
        self.assertEqual(info["size_cm"], "52x29")
        self.assertEqual(info["model"], "U2419H")
        self.assertEqual(info["preferred_resolution"], "1920x1080")

    def test_reject_bad_header(self):
        self.assertEqual(devices.parse_edid_bytes(bytes(128)), {})
        self.assertEqual(devices.parse_edid_bytes(b""), {})

    def test_model_falls_back_to_note_descriptor(self):
        data = bytearray(self.sample_edid())
        data[90:95] = b"\x00\x00\x00\xFE\x00"
        data[95:102] = b"NE140Q\n"
        info = devices.parse_edid_bytes(bytes(data))
        self.assertEqual(info["note"], "NE140Q")
        self.assertEqual(info["model"], "NE140Q")

    def test_display_name_prefers_brand_and_model(self):
        params = {"manufacturer": "DELL", "model": "U2419H"}
        self.assertEqual(devices._screen_display_name(params, {}), "DELL U2419H")

    def test_screen_params_merge(self):
        original_read = devices.read_edid
        original_mode = devices.display_mode
        devices.read_edid = lambda device_id: self.sample_edid()
        devices.display_mode = lambda display_name: {"resolution": "1920x1080", "refresh_hz": 60}
        try:
            params = devices._screen_params({
                "display_name": r"\\.\DISPLAY2",
                "device_id": "x",
                "device_string": "Generic PnP Monitor",
            })
        finally:
            devices.read_edid = original_read
            devices.display_mode = original_mode
        self.assertTrue(params["edid"])
        self.assertEqual(params["manufacturer"], "DELL")
        self.assertEqual(params["model"], "U2419H")
        self.assertEqual(params["resolution"], "1920x1080")
        self.assertEqual(params["refresh_hz"], 60)

    def test_registry_stores_screen_params(self):
        tmp = tempfile.mkdtemp(prefix="devices-edid-")
        path = os.path.join(tmp, "devices.json")
        registry = devices.load_registry(path)
        devices.register(registry, [], [{
            "id": "scr-hdmi", "name": "APT HDMI", "instance_name": "DISPLAY\\APT0464\\...",
            "display_name": r"\\.\DISPLAY2", "backend": "ddc", "current": 20,
            "params": {"manufacturer": "APT", "model": "HDMI", "connection": "HDMI", "resolution": "1920x1080"},
        }])
        devices.save_registry(path, registry)
        loaded = devices.load_registry(path)
        params = loaded["screens"]["scr-hdmi"]["params"]
        self.assertEqual(params["model"], "HDMI")
        self.assertEqual(params["connection"], "HDMI")
        self.assertEqual(loaded["screens"]["scr-hdmi"]["last_brightness"], 20)


class CameraEnumerationTest(unittest.TestCase):
    """扫描阶段不能打开设备，否则虚拟摄像头会被意外启动。"""

    def test_scan_uses_directshow_without_opening(self):
        original = devices.enumerate_dshow_cameras
        devices.enumerate_dshow_cameras = lambda: [
            {
                "order": 0, "name": "USB Webcam", "device_path": "\\\\?\\usb#x",
                "camera_id": "cam-usb", "id_source": "directshow",
            },
            {
                "order": 1, "name": "A57 (Windows 虚拟摄像头)",
                "device_path": "\\\\?\\swd#vcamdevapi#y",
                "camera_id": "cam-phone", "id_source": "directshow",
            },
        ]
        try:
            cameras = devices.enumerate_cameras(force_refresh=True)
        finally:
            devices.enumerate_dshow_cameras = original

        self.assertEqual([camera["index"] for camera in cameras], [0, 1])
        self.assertEqual([camera["camera_id"] for camera in cameras], ["cam-usb", "cam-phone"])
        # 扫描阶段没有打开设备，所以分辨率保持为空，等真正采集后再补
        self.assertEqual(cameras[0]["resolution"], "")
        self.assertFalse(cameras[0]["is_virtual"])
        self.assertTrue(cameras[1]["is_virtual"])
        # 回归保护：探测函数已删除，扫描时不可能调用 VideoCapture
        self.assertFalse(hasattr(devices, "_probe_opencv_cameras"))


if __name__ == "__main__":
    unittest.main()
