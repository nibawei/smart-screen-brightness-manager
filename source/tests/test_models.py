import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models  # noqa: E402


class ScreenCurveTest(unittest.TestCase):
    def test_default_is_identity(self):
        curve = models.ScreenCurve()
        self.assertEqual(curve.evaluate(0), 0.0)
        self.assertEqual(curve.evaluate(50), 50.0)
        self.assertEqual(curve.evaluate(100), 100.0)

    def test_inverse_roundtrip(self):
        curve = models.ScreenCurve([0, 20, 45, 70, 90])
        for value in (10, 30, 55, 80):
            self.assertAlmostEqual(float(curve.evaluate(curve.inverse(value))), value, delta=0.5)

    def test_single_sample_reaches_label(self):
        curve = models.ScreenCurve()
        curve.fit([84.0], [30.0], ridge=1.0, sample_count=1)
        self.assertLess(abs(curve.evaluate(84.0) - 30.0), 3.0)

    def test_fit_keeps_monotone(self):
        curve = models.ScreenCurve()
        curve.fit([20.0, 80.0], [70.0, 40.0], ridge=1.0, sample_count=2)
        self.assertTrue(np.all(np.diff(curve.brightness) >= 0))

    def test_fit_clips_extremes(self):
        curve = models.ScreenCurve()
        curve.fit([50.0], [100.0], ridge=0.1, sample_count=1)
        self.assertTrue(np.all(curve.brightness >= 0))
        self.assertTrue(np.all(curve.brightness <= 100))

    def test_save_and_load(self):
        tmp = tempfile.mkdtemp(prefix="curve-test-")
        path = os.path.join(tmp, "screens", "scr-a.json")
        curve = models.ScreenCurve([0, 10, 20, 30, 40], sample_count=3)
        models.save_screen_curve(path, curve)
        loaded = models.load_screen_curve(path)
        self.assertEqual(loaded.brightness.tolist(), curve.brightness.tolist())
        self.assertEqual(loaded.sample_count, 3)

    def test_save_includes_device_profile(self):
        tmp = tempfile.mkdtemp(prefix="curve-test-")
        path = os.path.join(tmp, "scr-hdmi.json")
        curve = models.ScreenCurve()
        models.save_screen_curve(path, curve, device={
            "manufacturer": "APT", "model": "HDMI", "connection": "HDMI",
            "resolution": "1920x1080",
        })
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["device"]["model"], "HDMI")
        self.assertEqual(data["device"]["connection"], "HDMI")

    def test_corrupt_profile_is_backed_up(self):
        tmp = tempfile.mkdtemp(prefix="curve-test-")
        path = os.path.join(tmp, "scr.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        loaded = models.load_screen_curve(path)
        self.assertEqual(loaded.brightness.tolist(), models.DEFAULT_CURVE.tolist())
        self.assertTrue(os.path.exists(path + ".bak"))


class SampleTest(unittest.TestCase):
    def test_append_and_load(self):
        tmp = tempfile.mkdtemp(prefix="samples-test-")
        path = models.sample_path(tmp, "cam-a", "scr-b")
        feature = [1.0] * 14
        models.append_samples(path, feature, 40)
        features, labels = models.append_samples(path, [2.0] * 14, 60)
        self.assertEqual(len(features), 2)
        self.assertEqual(labels, [40.0, 60.0])
        self.assertEqual(models.load_samples(path)[1], [40.0, 60.0])

    def test_legacy_weighted_is_halved(self):
        legacy_X = [[float(i)] * 14 for i in range(10)]
        legacy_y = [float(i) for i in range(10)]
        picked_X, picked_y = models.legacy_weighted(legacy_X, legacy_y, 0.5)
        self.assertEqual(len(picked_X), 5)
        self.assertEqual(len(picked_y), 5)

    def test_legacy_weighted_keeps_at_least_one(self):
        picked_X, _ = models.legacy_weighted([[0.0] * 14], [10.0], 0.5)
        self.assertEqual(len(picked_X), 1)


if __name__ == "__main__":
    unittest.main()
