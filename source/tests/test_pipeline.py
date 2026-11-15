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


if __name__ == "__main__":
    unittest.main()
