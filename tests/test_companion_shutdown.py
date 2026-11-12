import threading
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw
from companion_shutdown import CompanionShutdown, Control, Snapshot, classify, WindowsCompanionBackend


def frame(label="结束直播", dialog=False, *, identity=(1, 0, 0, 1280, 720), button=True):
    controls = [Control(label, 870, 640, 80, 22, button)]
    if dialog:
        controls = [Control("确定要结束直播吗？", 400, 230, 200, 30),
                    Control(label, 680, 425, 90, 25, button)]
    return Snapshot(identity, 1280, 720, controls, 1.)


class CompanionShutdownTests(unittest.TestCase):
    def run_flow(self, frames, *, cancelled=False):
        backend = Mock()
        last = frames[-1]
        source = iter(frames)
        backend.snapshot.side_effect = lambda: next(source, last)
        ticks = iter(range(200))
        cancel = Mock(is_set=Mock(return_value=cancelled), wait=Mock(return_value=False))
        result = CompanionShutdown(backend, clock=lambda:next(ticks)).end_live(cancel)
        return result, backend

    def test_idle_is_observed_twice_without_clicking_start(self):
        result, backend = self.run_flow([frame("开始直播"), frame("开始直播")])
        self.assertTrue(result[0])
        backend.click.assert_not_called()

    def test_live_confirmation_and_two_idle_frames_end_once_each(self):
        live, confirm, idle = frame(), frame("结束直播", dialog=True), frame("开始直播")
        result, backend = self.run_flow([live, live, confirm, confirm, idle, idle])
        self.assertTrue(result[0])
        self.assertEqual(backend.click.call_count, 2)
        self.assertEqual([call.args[1].text for call in backend.click.call_args_list], ["结束直播"]*2)

    def test_direct_end_needs_no_extra_confirmation_and_unknown_transition_can_settle(self):
        live, idle = frame(), frame("开始直播")
        result, backend = self.run_flow([live, live, frame("…", button=False), idle, idle])
        self.assertTrue(result[0])
        self.assertEqual(backend.click.call_count, 1)

    def test_unknown_preview_text_or_ambiguous_controls_never_click(self):
        ambiguous = frame()
        ambiguous.controls.append(Control("开始直播", 1000, 640, 80, 22, True))
        for value in (frame("退出"), frame(button=False), ambiguous,
                      Snapshot((1,), 1280, 720, [Control("结束直播", 400, 300, 80, 25, True)], 1)):
            result, backend = self.run_flow([value])
            self.assertFalse(result[0])
            backend.click.assert_not_called()

    def test_generic_confirm_requires_end_live_dialog_context(self):
        self.assertEqual(classify(frame("确认", dialog=True))[0], "confirm")
        no_context = frame("确认", dialog=True)
        no_context.controls[0] = Control("确认退出账号？", 400, 230, 200, 30)
        self.assertEqual(classify(no_context)[0], "unknown")

    def test_moved_window_needs_new_matching_evidence_and_cancel_never_clicks(self):
        other = frame(identity=(2, 0, 0, 1280, 720))
        result, backend = self.run_flow([frame(), other, frame("开始直播"), frame("开始直播")])
        self.assertTrue(result[0])
        backend.click.assert_not_called()
        _, backend = self.run_flow([frame()], cancelled=True)
        backend.snapshot.assert_not_called()
        backend.click.assert_not_called()

    def test_confirmation_stuck_does_not_repeat_click_or_claim_success(self):
        live, confirm = frame(), frame("确认", dialog=True)
        result, backend = self.run_flow([live, live, confirm, confirm])
        self.assertFalse(result[0])
        self.assertEqual(backend.click.call_count, 2)

    def test_backend_requires_filled_red_surface_and_rejects_plain_text(self):
        image = Image.new("RGB", (1280,720), (30,30,30))
        box = (880,640,940,662)
        self.assertFalse(WindowsCompanionBackend._button_surface(image,box))
        ImageDraw.Draw(image).rectangle((860,630,960,675), fill=(232,43,92))
        self.assertTrue(WindowsCompanionBackend._button_surface(image,box))


if __name__ == "__main__":
    unittest.main()
