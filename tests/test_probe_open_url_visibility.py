import unittest

from scripts.probe_open_url import evaluate_window_visibility


class TestWindowVisibilityMetrics(unittest.TestCase):
    def setUp(self):
        self.browser = {
            "hwnd": 10,
            "pid": 100,
            "title": "Example Domain - Personal - Microsoft Edge",
            "visible": True,
            "iconic": False,
            "cloaked": False,
            "rect": (100, 100, 900, 700),
            "monitor_rects": [(0, 0, 1920, 1080)],
            "foreground": True,
            "z_order": 2,
        }

    def test_visible_foreground_window_is_in_front(self):
        result = evaluate_window_visibility(10, [self.browser])

        self.assertTrue(result["on_monitor"])
        self.assertTrue(result["visible_according_to_criteria"])
        self.assertTrue(result["foreground"])
        self.assertTrue(result["z_order_topmost_intersecting"])
        self.assertTrue(result["visible_in_front"])

    def test_minimized_cloaked_and_off_monitor_are_not_visible(self):
        for change in (
            {"iconic": True},
            {"cloaked": True},
            {"rect": (2100, 100, 2900, 700)},
        ):
            with self.subTest(change=change):
                window = {**self.browser, **change, "foreground": False}
                result = evaluate_window_visibility(10, [window])
                self.assertFalse(result["visible_according_to_criteria"])
                self.assertFalse(result["visible_in_front"])

    def test_overlapping_foreground_window_occludes_browser_for_front_metric(self):
        covering = {
            "hwnd": 20,
            "visible": True,
            "iconic": False,
            "cloaked": False,
            "rect": (300, 200, 1200, 800),
            "z_order": 1,
        }

        result = evaluate_window_visibility(10, [self.browser, covering])

        self.assertTrue(result["visible_according_to_criteria"])
        self.assertEqual(result["overlapping_hwnds"], [20])
        self.assertFalse(result["z_order_topmost_intersecting"])
        self.assertTrue(result["visible_in_front"])

        browser_behind = {**self.browser, "foreground": False}
        result = evaluate_window_visibility(10, [browser_behind, covering])
        self.assertFalse(result["visible_in_front"])

    def test_offscreen_or_nonvisible_windows_do_not_occlude_z_order(self):
        other = {
            "hwnd": 20,
            "visible": False,
            "iconic": False,
            "cloaked": False,
            "rect": (100, 100, 900, 700),
            "z_order": 1,
        }

        result = evaluate_window_visibility(10, [self.browser, other])

        self.assertEqual(result["overlapping_hwnds"], [])
        self.assertTrue(result["z_order_topmost_intersecting"])

    def test_empty_rect_is_not_on_monitor_or_an_occluding_window(self):
        empty_window = {**self.browser, "rect": (0, 0, 0, 0), "foreground": False}
        covering = {
            "hwnd": 20,
            "visible": True,
            "iconic": False,
            "cloaked": False,
            "rect": (0, 0, 100, 100),
            "z_order": 1,
        }

        result = evaluate_window_visibility(10, [empty_window, covering])

        self.assertFalse(result["on_monitor"])
        self.assertFalse(result["visible_according_to_criteria"])
        self.assertEqual(result["overlapping_hwnds"], [])
        self.assertTrue(result["z_order_topmost_intersecting"])

    def test_unknown_cloaking_state_does_not_count_as_visible(self):
        window = {**self.browser, "cloaked": None}

        result = evaluate_window_visibility(10, [window])

        self.assertFalse(result["visible_according_to_criteria"])
        self.assertFalse(result["visible_in_front"])


if __name__ == "__main__":
    unittest.main()
