import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notification  # noqa: E402


class FakeNotification:
    created = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeNotification.created.append(kwargs)


class NotificationForwardingTest(unittest.TestCase):
    def setUp(self):
        self.original = notification.Notification
        notification.Notification = FakeNotification
        FakeNotification.created = []
        self.addCleanup(setattr, notification, "Notification", self.original)

    def test_window_notification_forwards_parent(self):
        notification.show_window_notification("窗口通知", parent="PARENT", level="info")
        self.assertEqual(len(FakeNotification.created), 1)
        self.assertEqual(FakeNotification.created[0]["parent"], "PARENT")
        self.assertEqual(FakeNotification.created[0]["screen_mode"], "widget")

    def test_show_notification_forwards_parent(self):
        notification.show_notification("消息", parent="PARENT", level="warning")
        self.assertEqual(FakeNotification.created[0]["parent"], "PARENT")

    def test_all_screens_creates_one_per_screen(self):
        original = notification.get_all_screens_indices
        notification.get_all_screens_indices = lambda: [0, 1]
        try:
            notification.show_all_screens_notification("所有屏幕")
        finally:
            notification.get_all_screens_indices = original
        self.assertEqual(len(FakeNotification.created), 2)
        self.assertEqual([item["screen_index"] for item in FakeNotification.created], [0, 1])
        self.assertTrue(all(item["screen_mode"] == "custom" for item in FakeNotification.created))
        self.assertTrue(all(item["position"] == "top" for item in FakeNotification.created))


if __name__ == "__main__":
    unittest.main()
