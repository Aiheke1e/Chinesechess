import threading
import unittest
from unittest.mock import Mock, patch

from board import Board
from chat_selection import ChatSelector
from commentary_style import CommentaryStyle
from interaction_chess import chess_comment, grounded_reply
from interaction_text import local_reply, spoken_reply
from live_host import LiveSettings, SpeechPipeline
from notation import move_description
from tests.test_mate_patterns import POSITIONS, predecessor


class InteractionFactualityTests(unittest.TestCase):
    def test_logged_chess_remarks_cannot_make_social_model_invent_board_facts(self):
        for comment in ("冲中兵", "上边马", "马卧错方向了", "一打一叫杀[捂脸]", "你一杀一吃算什么棋规？",
                        "双头蛇压得马难受", "三步虎转列炮，黑亏", "丢子了", "均势", "终于要赢一局了吗？",
                        "小老要开胡了", "你抓炮他先打你卒怎么办？", "抽你车"):
            with self.subTest(comment=comment):
                reply = local_reply(comment)
                self.assertIsNotNone(reply)
                for invented in ("这招挺猛", "挺稳", "胜利就在眼前", "黑棋亏了", "好戏", "美食大会", "压得够呛"):
                    self.assertNotIn(invented, reply)
        self.assertIn("平台规则", local_reply("你一杀一吃算什么棋规？"))
        self.assertIn("不能只凭开局名字", local_reply("三步虎转列炮，黑亏"))

    def test_purchase_person_and_title_questions_have_honest_local_answers(self):
        self.assertIn("购买来源我这边没有", local_reply("老师你的 马头 手机支架 在哪里买的"))
        self.assertIn("不乱报品牌", local_reply("你用的是什么耳机"))
        self.assertIn("没法替你把人叫来", local_reply("快呼叫小龙"))
        self.assertIn("哪位棋手", local_reply("光头是特大吗"))
        text = spoken_reply("小龙：豆包，我来啦！", payload={"观众": "小张", "弹幕": "快呼叫小龙"})
        self.assertNotIn("小龙：", text)
        self.assertNotIn("我来啦", text)

    def test_complete_move_without_a_board_is_acknowledged_instead_of_asking_which_piece(self):
        text = local_reply("炮2平3")
        self.assertIn("炮2平3", text)
        self.assertIn("核对当前棋形", text)
        self.assertNotIn("哪枚子", text)

    def test_verified_stalemate_is_not_claimed_to_have_legal_replies(self):
        event = predecessor(Board.from_fen(POSITIONS["困毙"]))
        before = Board.from_fen(event["fen_before"])
        notation = move_description(before, Board.from_ucci(event["ucci"])).split(" ", 1)[1].split("，", 1)[0]
        original = before.to_fen()
        text = grounded_reply(notation + "就赢了吗？", {"fen": original})
        self.assertIn("困毙取胜", text)
        self.assertNotIn("还有合法应法", text)
        self.assertNotIn("将死", text)
        self.assertEqual(before.to_fen(), original)

    def test_fragments_are_skipped_while_complete_questions_and_corrections_remain(self):
        selector = ChatSelector(clock=lambda: 100)
        for comment in ("冲中兵", "上边马", "推起来", "推演磊", "三把手", "准备niania"):
            self.assertFalse(selector.offer({"user": comment, "text": comment}), comment)
        for comment in ("冲中兵会不会漏杀？", "上边马走错了吧？", "马6进4才对", "老师你的手机支架在哪里买的"):
            self.assertTrue(selector.offer({"user": comment, "text": comment}), comment)

    def test_social_replies_do_not_append_invented_chess_drama(self):
        self.assertFalse(chess_comment("上班马上开播吗？"))
        raw = "咱们这棋局真是热闹非凡，真有节目效果啊！下一招可得小心点，别让对手偷袭了。"
        text = spoken_reply(raw, payload={"观众": "小张", "弹幕": "真有节目效果"})
        self.assertNotIn("偷袭", text)
        self.assertNotIn("棋局", text)
        self.assertTrue(text.startswith("小张，"))
        text = spoken_reply("老师，晚上好。", payload={"观众": "小张", "弹幕": "老师晚上好"})
        self.assertEqual(text, "小张，晚上好。")

    def test_repeated_openers_are_removed_without_learning_a_viewer_name_as_a_topic(self):
        style = CommentaryStyle()
        style.remember("小王，好嘞，收到你的问题。")
        self.assertEqual(style.decorate("弹幕回复", "好嘞，晚上好。", {}, "幽默"), "晚上好。")
        pipeline = SpeechPipeline(LiveSettings(), Mock())
        try:
            pipeline._remember_speech("哈哈，晚上好。", "弹幕回复", {"观众": "哈哈"})
            self.assertNotIn("哈哈", pipeline.style.openers())
            pipeline._remember_speech("财务大师，晚上好。", "弹幕回复", {"观众": "财务大师"})
            self.assertNotIn("财务", pipeline.style.topics())
        finally: pipeline.close()

    def test_local_fact_boundary_runs_before_the_model_and_preserves_display_source(self):
        heard = threading.Event()
        records = []
        def notify(kind, text, data):
            if kind == "reply":
                records.append((text, data))
                heard.set()
        llm = Mock(side_effect=AssertionError("无需模型编购买来源"))
        pipeline = SpeechPipeline(LiveSettings(speech_enabled=False, cooldown=0), notify, llm=llm)
        try:
            comment = "老师你的 马头 手机支架 在哪里买的"
            pipeline.submit(3, "弹幕回复", {"观众": "小张", "弹幕": comment, "互动标识": "shop"})
            self.assertTrue(heard.wait(1))
            llm.assert_not_called()
            self.assertIn("购买来源我这边没有", records[0][0])
            self.assertEqual(records[0][1]["观众"], "小张")
            self.assertEqual(records[0][1]["弹幕"], comment)
        finally: pipeline.close()


if __name__ == "__main__":
    unittest.main()
