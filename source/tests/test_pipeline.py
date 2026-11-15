import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
import devices  # noqa: E402
import models  # noqa: E402
from liangdu import generate_synthetic_data  # noqa: E402


class FakeRoot:
    """记录 after 回调但不执行，避免测试里碰 Tk。"""

    def __init__(self):
        self.callbacks = []

    def after(self, delay, func=None):
        if func is not None:
            self.callbacks.append(func)


def make_app(data_root):
    obj = object.__new__(app_module.BrightnessManagerApp)
    obj.data_root = data_root
    obj.devices_registry_path = os.path.join(data_root, "devices.json")
    obj.legacy_samples_path = os.path.join(data_root, "training_samples.npz")
    obj.devices_registry = devices.default_registry()
    obj.camera_models = {}
    obj.screen_curves = {}
    obj.screen_states = {}
    obj.active_screen_id = "scr-a"
    obj.pending_capture = None
    obj.available_cameras = [{
        "index": 0, "name": "Test Cam", "camera_id": "cam-test",
        "device_path": "p", "resolution": "640x480", "id_source": "directshow",
    }]
    obj.available_monitors = [
        {"index": 0, "id": "scr-a", "name": "Screen A", "instance_name": "A",
         "display_name": "\\\\.\\DISPLAY1", "supported": True, "backend": "wmi", "current": 50},
        {"index": 1, "id": "scr-b", "name": "Screen B", "instance_name": "B",
         "display_name": "\\\\.\\DISPLAY2", "supported": True, "backend": "wmi", "current": 40},
    ]
    obj.nn_lock = threading.Lock()
    obj.data_lock = threading.Lock()
    obj.training_lock = threading.Lock()
    obj.root = FakeRoot()
    obj.logs = []
    obj.log_info = lambda message: obj.logs.append(("INFO", message))
    obj.log_warning = lambda message: obj.logs.append(("WARN", message))
    obj.log_error = lambda message: obj.logs.append(("ERR", message))
    return obj


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="light-pipeline-")
        self.app = make_app(self.tmp)
        self.camera = self.app.available_cameras[0]
        features, _ = generate_synthetic_data(num_samples=5, seed=2)
        self.features = features[0]
        self.app.pending_capture = {
            "features": self.features,
            "camera_id": "cam-test",
            "timestamp": time.time(),
        }

    def test_predict_uses_curve(self):
        brightness, reference = self.app.predict_for_screen("cam-test", "scr-a", self.features)
        self.assertEqual(brightness, reference)  # 默认曲线是恒等

    def test_train_worker_records_per_screen(self):
        self.app._train_worker(self.camera, {"scr-a": 30, "scr-b": 70}, fine_tune_camera=False)

        labels_a = models.load_samples(models.sample_path(self.tmp, "cam-test", "scr-a"))[1]
        labels_b = models.load_samples(models.sample_path(self.tmp, "cam-test", "scr-b"))[1]
        self.assertEqual(labels_a, [30.0])
        self.assertEqual(labels_b, [70.0])

        curve_a = models.load_screen_curve(os.path.join(self.tmp, "screens", "scr-a.json"))
        curve_b = models.load_screen_curve(os.path.join(self.tmp, "screens", "scr-b.json"))
        self.assertNotEqual(curve_a.brightness.tolist(), curve_b.brightness.tolist())

        pred_a, _ = self.app.predict_for_screen("cam-test", "scr-a", self.features)
        pred_b, _ = self.app.predict_for_screen("cam-test", "scr-b", self.features)
        self.assertLess(abs(pred_a - 30), 5)
        self.assertLess(abs(pred_b - 70), 5)

    def test_registry_marked_trained(self):
        self.app._train_worker(self.camera, {"scr-a": 45}, fine_tune_camera=False)
        self.assertTrue(self.app.devices_registry["screens"]["scr-a"]["trained"])
        self.assertEqual(self.app.devices_registry["screens"]["scr-a"]["sample_count"], 1)

    def test_pending_capture_expires(self):
        self.app.pending_capture["timestamp"] = time.time() - 3600
        self.assertIsNone(self.app._pending_features(self.camera))

    def test_camera_models_are_per_camera(self):
        model_a = self.app.get_camera_model("cam-a")
        model_b = self.app.get_camera_model("cam-b")
        self.assertIsNot(model_a, model_b)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "cameras", "cam-a.npz")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "cameras", "cam-b.npz")))

    def test_legacy_samples_affect_fit(self):
        import numpy as np

        self.app._train_worker(self.camera, {"scr-a": 60}, fine_tune_camera=False)
        without_legacy = models.load_screen_curve(
            os.path.join(self.tmp, "screens", "scr-a.json")
        ).brightness.tolist()

        # 换一个干净的目录，加入 legacy 后再训练同一条样本
        other = tempfile.mkdtemp(prefix="light-legacy-")
        second = make_app(other)
        second.pending_capture = self.app.pending_capture
        legacy_X = [[120.0] + [30.0] * 13 for _ in range(4)]
        np.savez(second.legacy_samples_path, X=np.array(legacy_X), y=np.array([10.0, 12.0, 14.0, 16.0]))
        second._train_worker(second.available_cameras[0], {"scr-a": 60}, fine_tune_camera=False)
        with_legacy = models.load_screen_curve(
            os.path.join(other, "screens", "scr-a.json")
        ).brightness.tolist()

        self.assertNotEqual(without_legacy, with_legacy)


class ScreenSwitchTest(unittest.TestCase):
    """下拉框切换屏幕后，应用必须写到新选中的那块屏（而不是主屏）。"""

    class FakeVar:
        def __init__(self, value=None):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class FakeLabel:
        def __init__(self):
            self.text = ""

        def config(self, **kwargs):
            self.text = kwargs.get("text", self.text)

    def setUp(self):
        original = app_module.config_manager

        class DummyConfig:
            def __init__(self):
                self.data = {}

            def set(self, key, value):
                self.data[key] = value

            def get(self, key, default=None):
                return self.data.get(key, default)

            def save_config(self):
                pass

        app_module.config_manager = DummyConfig()
        self.addCleanup(setattr, app_module, "config_manager", original)

    def make(self):
        obj = object.__new__(app_module.BrightnessManagerApp)
        obj.available_monitors = [
            {"id": "scr-a", "name": "Screen A", "display_name": "\\\\.\\DISPLAY1",
             "supported": True, "current": 50, "params": {}},
            {"id": "scr-b", "name": "Screen B", "display_name": "\\\\.\\DISPLAY2",
             "supported": True, "current": 40, "params": {}},
        ]
        obj.active_screen_id = "scr-a"
        obj.screen_states = {}
        obj.screen_var = self.FakeVar("Screen B")
        obj.brightness_var = self.FakeVar(0)
        obj.brightness_label = self.FakeLabel()
        obj.screen_status_label = self.FakeLabel()
        obj.log_info = lambda message: None
        return obj

    def test_switch_updates_active_screen(self):
        obj = self.make()
        obj.on_screen_change()
        self.assertEqual(obj.active_screen_id, "scr-b")
        self.assertEqual(obj.brightness_var.get(), 40)

    def test_active_screen_after_switch(self):
        obj = self.make()
        obj.on_screen_change()
        self.assertEqual(obj._active_screen()["id"], "scr-b")
        self.assertEqual(obj._active_screen()["display_name"], "\\\\.\\DISPLAY2")


class NotifyRoutingTest(unittest.TestCase):
    """附屏通知要落到附屏，而不是永远主屏。"""

    @staticmethod
    def make(qt_index):
        obj = object.__new__(app_module.BrightnessManagerApp)
        obj.notification_position = 'bottom'
        obj._notify_warned = set()
        obj.log_warning = lambda message: None
        obj._qt_screen_index = lambda screen: qt_index
        return obj

    @staticmethod
    def call(obj, screen):
        captured = {}
        original = app_module.show_notification

        def fake(message, **kwargs):
            captured["message"] = message
            captured.update(kwargs)
            return []

        app_module.show_notification = fake
        try:
            obj._notify_screen(screen, "亮度已调整为 40%", "success")
        finally:
            app_module.show_notification = original
        return captured

    def test_secondary_screen_notification_uses_custom_index(self):
        captured = self.call(self.make(1), {"display_name": "\\\\.\\DISPLAY2", "name": "B"})
        self.assertEqual(captured["message"], "亮度已调整为 40%")
        self.assertEqual(captured["screen_mode"], "custom")
        self.assertEqual(captured["screen_index"], 1)
        self.assertEqual(captured["level"], "success")

    def test_unmapped_screen_falls_back_to_primary(self):
        captured = self.call(self.make(None), {"display_name": "", "name": "X"})
        self.assertNotIn("screen_mode", captured)

    def test_display_name_normalization(self):
        self.assertEqual(app_module._normalize_display_name("\\\\.\\DISPLAY2"), "DISPLAY2")
        self.assertEqual(app_module._normalize_display_name("display2"), "DISPLAY2")


if __name__ == "__main__":
    unittest.main()
