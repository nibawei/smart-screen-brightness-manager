import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import liangdu  # noqa: E402


class GammaRampTest(unittest.TestCase):
    def test_identity_at_100(self):
        ramp = liangdu._build_gamma_ramp(100)
        self.assertEqual(ramp[0], 0)
        self.assertEqual(ramp[255], 65535)
        for index in (10, 64, 128, 200):
            self.assertAlmostEqual(ramp[index], round(65535 * index / 255), delta=2)

    def test_dimmer_is_darker_but_not_black(self):
        dim = liangdu._build_gamma_ramp(40)
        full = liangdu._build_gamma_ramp(100)
        self.assertLess(dim[128], full[128])
        self.assertGreater(dim[255], 0)

    def test_white_is_dimmed_too(self):
        # 比例压暗：白点也要下降，否则用户设低亮度仍觉得屏幕亮
        dim = liangdu._build_gamma_ramp(50)
        self.assertLess(dim[255], 65535 * 0.7)
        self.assertAlmostEqual(dim[255], round(65535 * (0.06 + 0.94 * 0.5)), delta=2)

    def test_monotone_and_clamped(self):
        for level in (-10, 0, 25, 50, 75, 100, 150):
            ramp = liangdu._build_gamma_ramp(level)
            self.assertTrue(all(ramp[i] <= ramp[i + 1] for i in range(255)))


class BackendOrderTest(unittest.TestCase):
    def test_external_prefers_ddc(self):
        self.assertEqual(
            liangdu._backend_order({"params": {"connection": "HDMI"}}),
            ["ddc", "wmi", "gamma"],
        )

    def test_internal_prefers_wmi(self):
        self.assertEqual(
            liangdu._backend_order({"params": {"connection": "内置屏"}}),
            ["wmi", "ddc", "gamma"],
        )

    def test_unknown_defaults_to_ddc(self):
        self.assertEqual(
            liangdu._backend_order({"params": {}}),
            ["ddc", "wmi", "gamma"],
        )


class CapabilitiesTest(unittest.TestCase):
    def test_external_screen_gets_ddc_first(self):
        original_monitors = liangdu.get_available_monitors
        original_ddc = liangdu._ddc_probe_display
        liangdu.get_available_monitors = lambda force_refresh=False: [{
            "id": "scr-hdmi", "name": "APT HDMI", "display_name": r"\\.\DISPLAY2",
            "supported": True, "current": 40, "params": {"connection": "HDMI"},
        }]
        liangdu._ddc_probe_display = lambda display_name: {
            "available": True, "current": 40, "max": 100,
        }
        try:
            caps = liangdu.list_brightness_capabilities(force_refresh=True)
        finally:
            liangdu.get_available_monitors = original_monitors
            liangdu._ddc_probe_display = original_ddc

        screen = caps[0]
        self.assertEqual(screen["backends"], ["ddc", "wmi", "gamma"])
        self.assertEqual(screen["backend"], "ddc")
        self.assertTrue(screen["supported"])
        self.assertFalse(screen["software_only"])
        self.assertEqual(screen["params"]["brightness_max"], 100)

    def test_software_only_screen(self):
        original_monitors = liangdu.get_available_monitors
        original_ddc = liangdu._ddc_probe_display
        liangdu.get_available_monitors = lambda force_refresh=False: [{
            "id": "scr-x", "name": "Unknown", "display_name": r"\\.\DISPLAY3",
            "supported": False, "current": None, "params": {},
        }]
        liangdu._ddc_probe_display = lambda display_name: {
            "available": False, "current": None, "max": None,
        }
        try:
            caps = liangdu.list_brightness_capabilities(force_refresh=True)
        finally:
            liangdu.get_available_monitors = original_monitors
            liangdu._ddc_probe_display = original_ddc

        screen = caps[0]
        self.assertEqual(screen["backends"], ["gamma"])
        self.assertTrue(screen["supported"])
        self.assertTrue(screen["software_only"])


class SetTargetsTest(unittest.TestCase):
    @staticmethod
    def screen(backends):
        return {
            "id": "scr-a", "display_name": r"\\.\DISPLAY2", "instance_name": "X",
            "index": 1, "backend": backends[0], "backends": backends, "params": {},
        }

    def test_wmi_failure_falls_back_to_ddc(self):
        calls = []
        original_wmi, original_ddc = liangdu._wmi_set, liangdu._ddc_set
        liangdu._wmi_set = lambda *args, **kwargs: (calls.append("wmi"), (False, None, "no wmi"))[1]
        liangdu._ddc_set = lambda *args, **kwargs: (calls.append("ddc"), (True, 50, ""))[1]
        try:
            result = liangdu.set_brightness_targets(
                {"scr-a": 50}, screens=[self.screen(["wmi", "ddc", "gamma"])]
            )
        finally:
            liangdu._wmi_set, liangdu._ddc_set = original_wmi, original_ddc

        self.assertTrue(result["scr-a"]["ok"])
        self.assertEqual(result["scr-a"]["backend"], "ddc")
        self.assertEqual(calls, ["wmi", "ddc"])

    def test_gamma_fallback_when_physical_fail(self):
        calls = []
        originals = (liangdu._wmi_set, liangdu._ddc_set, liangdu._gamma_set)
        liangdu._wmi_set = lambda *args, **kwargs: (calls.append("wmi"), (False, None, "no"))[1]
        liangdu._ddc_set = lambda *args, **kwargs: (calls.append("ddc"), (False, None, "no"))[1]
        liangdu._gamma_set = lambda *args, **kwargs: (calls.append("gamma"), (True, 45, ""))[1]
        try:
            result = liangdu.set_brightness_targets(
                {"scr-a": 45}, screens=[self.screen(["ddc", "wmi", "gamma"])]
            )
        finally:
            liangdu._wmi_set, liangdu._ddc_set, liangdu._gamma_set = originals

        self.assertTrue(result["scr-a"]["ok"])
        self.assertEqual(result["scr-a"]["backend"], "gamma")
        self.assertEqual(calls, ["ddc", "wmi", "gamma"])

    def test_all_backends_fail(self):
        originals = (liangdu._wmi_set, liangdu._ddc_set, liangdu._gamma_set)
        liangdu._wmi_set = lambda *args, **kwargs: (False, None, "wmi err")
        liangdu._ddc_set = lambda *args, **kwargs: (False, None, "ddc err")
        liangdu._gamma_set = lambda *args, **kwargs: (False, None, "gamma err")
        try:
            result = liangdu.set_brightness_targets(
                {"scr-a": 50}, screens=[self.screen(["ddc", "wmi", "gamma"])]
            )
        finally:
            liangdu._wmi_set, liangdu._ddc_set, liangdu._gamma_set = originals

        self.assertFalse(result["scr-a"]["ok"])
        self.assertIn("ddc: ddc err", result["scr-a"]["error"])
        self.assertIn("gamma: gamma err", result["scr-a"]["error"])

    def test_readback_mismatch_triggers_fallback(self):
        calls = []
        originals = (liangdu._wmi_set, liangdu._ddc_set)
        liangdu._wmi_set = lambda *args, **kwargs: (calls.append("wmi"), (True, 90, ""))[1]
        liangdu._ddc_set = lambda *args, **kwargs: (calls.append("ddc"), (True, 50, ""))[1]
        try:
            result = liangdu.set_brightness_targets(
                {"scr-a": 50}, screens=[self.screen(["wmi", "ddc"])], verify=True
            )
        finally:
            liangdu._wmi_set, liangdu._ddc_set = originals

        self.assertTrue(result["scr-a"]["ok"])
        self.assertEqual(result["scr-a"]["backend"], "ddc")
        self.assertEqual(calls, ["wmi", "ddc"])

    def test_external_wmi_without_readback_falls_back(self):
        # 外接屏 WMI「调用成功但读不到回读值」时不能算成功，否则屏幕看起来没变（偏亮）
        calls = []
        originals = (liangdu._wmi_set, liangdu._gamma_set)
        liangdu._wmi_set = lambda *args, **kwargs: (calls.append("wmi"), (True, None, ""))[1]
        liangdu._gamma_set = lambda *args, **kwargs: (calls.append("gamma"), (True, 50, ""))[1]
        screen = self.screen(["wmi", "gamma"])
        screen["params"] = {"connection": "HDMI"}
        try:
            result = liangdu.set_brightness_targets({"scr-a": 50}, screens=[screen], verify=True)
        finally:
            liangdu._wmi_set, liangdu._gamma_set = originals

        self.assertTrue(result["scr-a"]["ok"])
        self.assertEqual(result["scr-a"]["backend"], "gamma")
        self.assertEqual(calls, ["wmi", "gamma"])

    def test_internal_wmi_without_readback_is_accepted(self):
        originals = liangdu._wmi_set
        liangdu._wmi_set = lambda *args, **kwargs: (True, None, "")
        screen = self.screen(["wmi", "gamma"])
        screen["params"] = {"connection": "内置屏"}
        try:
            result = liangdu.set_brightness_targets({"scr-a": 50}, screens=[screen], verify=True)
        finally:
            liangdu._wmi_set = originals

        self.assertTrue(result["scr-a"]["ok"])
        self.assertEqual(result["scr-a"]["backend"], "wmi")


if __name__ == "__main__":
    unittest.main()
