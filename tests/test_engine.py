import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from ddt_daily.engine import AutomationError, Clock, Runner, load_workflow, locate, profile_lock, validate_anchor


class FakeDesktop:
    def __init__(self, before, after):
        self.current = before
        self.after = after
        self.clicks = []
        self.window_title = "弹弹堂3 - 浏览器"
        self.stopped = False
        self.lose_focus_after_click = False

    def screenshot(self):
        return self.current

    def click(self, x, y):
        self.clicks.append((x, y))
        self.current = self.after
        if self.lose_focus_after_click:
            self.window_title = "其他窗口"

    def title(self):
        return self.window_title

    def check_stop(self):
        if self.stopped:
            raise AutomationError("用户停止")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "workflow.json"
        rng = np.random.default_rng(8)
        self.button = Image.fromarray(rng.integers(0, 256, (24, 32, 3), dtype=np.uint8))
        self.done = Image.fromarray(rng.integers(0, 256, (24, 32, 3), dtype=np.uint8))
        self.before = Image.new("RGB", (320, 220), "#444444")
        self.before.paste(self.button, (40, 50))
        self.after = Image.new("RGB", (320, 220), "#444444")
        self.after.paste(self.done, (150, 100))
        self.button.save(self.root / "before.png")
        self.done.save(self.root / "after.png")
        self.config = {
            "version": 1, "screen_size": [320, 220], "region": [0, 0, 320, 220],
            "window_title": "弹弹堂", "search_margin": 20,
            "steps": [{"name": "测试领取", "action": "click", "timeout": 1,
                       "before": {"image": "before.png", "position": [40, 50]},
                       "after": {"image": "after.png", "position": [150, 100]}}],
        }
        self.save()
        self.desktop = FakeDesktop(self.before, self.after)
        self.elapsed = 0

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")

    def tick(self, seconds):
        self.elapsed += seconds

    def runner(self):
        return Runner(self.path, self.desktop, Clock(lambda: self.elapsed, self.tick), lambda _: None)

    def state(self):
        return json.loads((self.root / "state.json").read_text(encoding="utf-8"))

    def test_matches_shifted_button_and_clicks_its_center(self):
        shifted = Image.new("RGB", (320, 220), "#444444")
        shifted.paste(self.button, (47, 54))
        self.desktop.current = shifted
        self.assertTrue(self.runner().run())
        self.assertEqual(self.desktop.clicks, [(63, 66)])
        self.assertEqual(self.state()["status"], "completed")

    def test_completed_day_skips_without_click(self):
        self.runner().run()
        self.assertFalse(self.runner().run())
        self.assertEqual(len(self.desktop.clicks), 1)

    def test_missing_button_times_out_without_clicking(self):
        self.desktop.current = Image.new("RGB", (320, 220))
        with self.assertRaisesRegex(AutomationError, "等待画面超时"):
            self.runner().run()
        self.assertEqual(self.desktop.clicks, [])
        self.assertEqual(self.state()["status"], "running")
        with self.assertRaisesRegex(AutomationError, "未完成记录"):
            self.runner().run()

    def test_missing_postcondition_does_not_mark_success_or_retry_click(self):
        self.desktop.after = self.before
        with self.assertRaisesRegex(AutomationError, "等待画面超时"):
            self.runner().run()
        self.assertEqual(len(self.desktop.clicks), 1)
        self.assertEqual(self.state()["status"], "running")

    def test_lost_focus_stops_the_run(self):
        self.desktop.lose_focus_after_click = True
        with self.assertRaisesRegex(AutomationError, "前台窗口"):
            self.runner().run()
        self.assertEqual(self.state()["status"], "running")

    def test_wrong_window_is_rejected_before_any_click(self):
        self.desktop.window_title = "终端"
        with self.assertRaisesRegex(AutomationError, "前台窗口"):
            self.runner().run()
        self.assertFalse((self.root / "state.json").exists())
        self.assertEqual(self.desktop.clicks, [])

    def test_total_time_budget_stops_waiting(self):
        self.desktop.current = Image.new("RGB", (320, 220))
        with self.assertRaisesRegex(AutomationError, "总时限"):
            self.runner().run(max_seconds=0.5)
        self.assertLess(self.elapsed, 1)

    def test_user_stop_prevents_click(self):
        self.desktop.stopped = True
        with self.assertRaisesRegex(AutomationError, "用户停止"):
            self.runner().run()
        self.assertEqual(self.desktop.clicks, [])

    def test_resolution_change_is_rejected(self):
        self.desktop.current = Image.new("RGB", (640, 440))
        with self.assertRaisesRegex(AutomationError, "屏幕尺寸"):
            self.runner().run()

    def test_ambiguous_buttons_are_rejected(self):
        self.before.paste(self.button, (90, 50))
        with self.assertRaisesRegex(AutomationError, "多个相似"):
            locate(self.before, self.button, self.config["region"], [40, 50], 80, 0.94)

    def test_does_not_search_outside_recorded_neighborhood(self):
        far = Image.new("RGB", (320, 220))
        far.paste(self.button, (200, 50))
        self.assertIsNone(locate(far, self.button, self.config["region"], [40, 50], 20, 0.94))

    def test_changed_template_requires_explicit_rerun(self):
        self.runner().run()
        self.done.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(self.root / "after.png")
        with self.assertRaisesRegex(AutomationError, "流程已改变"):
            self.runner().run()

    def test_force_reruns_after_manual_reset(self):
        self.runner().run()
        self.desktop.current = self.before
        self.assertTrue(self.runner().run(force=True))
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_uniform_template_rejected(self):
        with self.assertRaisesRegex(AutomationError, "过于单一"):
            validate_anchor(Image.new("RGB", (30, 30), "white"))

    def test_template_outside_game_region_is_rejected(self):
        self.config["region"] = [0, 0, 100, 100]
        self.save()
        with self.assertRaisesRegex(AutomationError, "不在游戏区域"):
            load_workflow(self.path)

    def test_wait_only_step_does_not_click(self):
        self.config["steps"][0]["action"] = "wait"
        self.save()
        self.desktop.current = self.after
        self.assertTrue(self.runner().run())
        self.assertEqual(self.desktop.clicks, [])

    def test_second_runner_cannot_use_the_same_profile(self):
        with profile_lock(self.root):
            with self.assertRaisesRegex(AutomationError, "已有执行进程"):
                self.runner().run()
        # The lock is released; a later run can proceed normally.
        self.assertTrue(self.runner().run())


if __name__ == "__main__":
    unittest.main()
