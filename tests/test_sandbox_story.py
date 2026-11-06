from copy import deepcopy
import unittest
from unittest.mock import Mock

from board import Board
import game_sandbox as gs
from notation import move_description
from sandbox_narration import script
from sandbox_story import SandboxStory
from live_host import LiveController, LiveSettings
from tests.test_sandbox_presentation import plan_fixture


class SandboxStoryTests(unittest.TestCase):
    def build(self, fen, moves):
        board = Board.from_fen(fen)
        probe, line = board.copy(), []
        for ucci in moves:
            move = Board.from_ucci(ucci)
            self.assertIn(move, probe.legal_moves())
            line.append((ucci, move_description(probe, move)))
            probe.apply(move)
        return gs.build_plan(line[0][1], {}, line, line[0][0], board)

    def test_recorded_pawn_trade_and_check_shuttle_do_not_open_a_sandbox(self):
        self.assertIsNone(self.build("1rbakabnr/9/1cn4c1/p3p1p1p/2p6/1R7/P1P1P1P1P/2N1C2C1/9/2BAKABNR b",
                                    ("h9g7","c3c4","c5c4","b4c4","b7b8")))
        self.assertIsNone(self.build("2bakab2/8r/2n3R2/p3p1p1p/9/3N5/P3P1P1P/4C4/4A4/1rc1KABNR b",
                                    ("c0c4","e1d0","c4c0","d0e1","i8d8","g7c7","d8d4")))

    def test_same_mate_suffix_is_not_retried_after_a_failed_attempt(self):
        now = [100.]
        cadence = gs.SandboxCadence(clock=lambda:now[0])
        plan = {"kind":"mate","route":[{"ucci":u} for u in ("a0a1","b9b8","c0c1","d9d8")]}
        cadence.record(10, plan)
        now[0] += 50
        self.assertFalse(cadence.allowed({**plan,"route":plan["route"][2:]}, 12))
        self.assertTrue(cadence.allowed({"kind":"mate","route":[{"ucci":"h0g2"},{"ucci":"h9g7"}]},12))

    def shown_fixture(self):
        board, line, plan = plan_fixture()
        board.apply(Board.from_ucci(line[0][0]))
        plan = {**plan,"fen":board.to_fen()}
        story = SandboxStory(clock=lambda:100)
        story.prepare("shown",plan)
        story.finished("shown",{"entered":True,"safe_exit":True,"completed":len(plan["route"])})
        return story,board,plan

    def test_actual_shown_line_gets_a_short_real_move_confirmation(self):
        story,board,plan = self.shown_fixture()
        board.apply(Board.from_ucci(plan["route"][0]["ucci"]))
        event={"fen_before":board.to_fen(),"ucci":plan["route"][1]["ucci"],"notation":plan["route"][1]["notation"]}
        original=deepcopy(event)
        self.assertTrue(story.move_call(event).startswith("实战走"))
        self.assertEqual(event,original)

    def test_actual_alternative_is_announced_once_and_the_phrasing_rotates(self):
        texts=[]
        story,board,plan = self.shown_fixture()
        for _ in range(4):
            story.forecast={"fen":board.to_fen(),"ucci":plan["route"][0]["ucci"],"at":100}
            move=next(m for m in board.legal_moves() if Board.ucci(m)!=plan["route"][0]["ucci"])
            after=board.copy();after.apply(move)
            notation=move_description(board,move)
            story.reply({"ucci":Board.ucci(move),"notation":notation,"fen":after.to_fen()})
            our=after.legal_moves()[0]
            event={"fen_before":after.to_fen(),"ucci":Board.ucci(our),"notation":move_description(after,our)}
            text=story.move_call(event);texts.append(text)
            self.assertIn(notation.split(' ',1)[-1],text)
            self.assertEqual(story.move_call(event),"")
            self.assertNotIn("高手",text)
        self.assertEqual(len(set(texts)),4)

    def test_wrong_position_or_failed_entry_cannot_generate_a_branch_notice(self):
        story,board,plan=self.shown_fixture()
        story.reply({"ucci":"a0a1","fen":Board().to_fen(),"notation":"红方 车九进一"})
        self.assertIsNone(story.branch)
        story.clear();story.prepare("not-entered",plan)
        story.finished("not-entered",{"entered":False,"completed":4})
        self.assertFalse(story.shown)
        self.assertIsNone(story.forecast)

    def test_introductions_rotate_without_changing_the_route(self):
        _,_,plan=plan_fixture();story=SandboxStory()
        texts=[script(story.prepare(str(i),plan))["intro"] for i in range(4)]
        self.assertEqual(len(set(texts)),4)
        self.assertNotIn("narration_variant",plan)

    def test_controller_confirms_a_previewed_move_without_repeating_its_explanation(self):
        story,board,plan=self.shown_fixture()
        board.apply(Board.from_ucci(plan["route"][0]["ucci"]))
        event={"event":"engine_analysis","number":17,"speech_token":"actual-17",
               "fen_before":board.to_fen(),"ucci":plan["route"][1]["ucci"],
               "notation":plan["route"][1]["notation"],"side":board.turn,
               "analysis":{"score_type":"cp","score":25},"sandbox_plan":None}
        controller=object.__new__(LiveController)
        controller.settings=LiveSettings(speech_enabled=True,sandtable_enabled=False)
        controller.pipeline,controller._notify=Mock(),Mock()
        controller.sandbox_story=story
        controller.submit_chess(event)
        self.assertTrue(controller.pipeline.prepare_move.call_args.kwargs["direct_text"].startswith("实战走"))
        controller.pipeline.prepare_idle.assert_not_called()
        self.assertEqual(controller.commentary_analyses["actual-17"]["ucci"],event["ucci"])
