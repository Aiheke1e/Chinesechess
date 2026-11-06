import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch

from board import Board,START_FEN
from broadcast_flow import BroadcastFlow,GAP_KIND,TRANSITION_KINDS
from live_host import LiveController,LiveSettings,SpeechPipeline
import player
from tests.test_sandbox_presentation import plan_fixture


class BroadcastFlowTests(unittest.TestCase):
    def test_end_and_start_rotate_but_duplicate_events_are_silent(self):
        flow=BroadcastFlow();endings=[];openings=[]
        for _ in range(4):
            flow.start({})
            event={"fen":START_FEN,"my_color":"b","own_moves":0}
            openings.append(flow.position(event))
            self.assertEqual(flow.position(event),"")
            ending={"result":"finished","reason":"jj_result_screen"}
            endings.append(flow.end(ending))
            self.assertEqual(flow.end(ending),"")
        self.assertEqual(len(set(endings)),4)
        self.assertEqual(len(set(openings)),4)
        self.assertTrue(all("执黑" in text for text in openings))
        self.assertFalse(any("赢" in text for text in endings))

    def test_resume_is_not_announced_as_a_new_game_and_pauses_are_not_results(self):
        flow=BroadcastFlow();flow.start({})
        self.assertIn("继续当前这盘",flow.position({"fen":START_FEN,"own_moves":18}))
        self.assertEqual(flow.end({"result":"stopped","reason":"user_stop"}),"")
        self.assertEqual(flow.end({"result":"speech_pause"}),"")
        flow.start({"dry_run":True})
        self.assertEqual(flow.position({"fen":START_FEN}),"")
        self.assertEqual(flow.end({"result":"won"}),"")

    def test_fillers_are_bounded_rotating_and_stop_at_the_next_game(self):
        now=[100.];flow=BroadcastFlow(clock=lambda:now[0]);seen=[]
        flow.end({"result":"won"})
        self.assertEqual(flow.gap(),"")
        now[0]+=12;seen.append(flow.gap())
        self.assertEqual(flow.gap(),"")
        now[0]+=25;seen.append(flow.gap())
        now[0]+=25;self.assertEqual(flow.gap(),"")
        flow.start({});self.assertEqual(flow.gap(),"")
        flow.end({"result":"lost"});now[0]+=12;seen.append(flow.gap())
        self.assertEqual(len(set(seen)),3)

    def test_recent_chat_or_working_pipeline_does_not_get_an_extra_filler(self):
        now=[112.];controller=object.__new__(LiveController)
        controller.settings=LiveSettings();controller.suspended=controller.chess_suspended=False
        controller.pipeline=Mock();controller.pipeline.balance.chess_active=False
        controller.pipeline.activity_snapshot.return_value={}
        controller.broadcast_flow=BroadcastFlow(clock=lambda:now[0])
        controller.broadcast_flow.ended_at=100;controller.broadcast_flow.next_gap=112
        controller.last_spoken_until=105
        with patch('live_host.time.monotonic',side_effect=lambda:now[0]):
            controller._between_games_tick();controller.pipeline.submit.assert_not_called()
            now[0]=118;controller.pipeline.activity_snapshot.return_value={"playing":True}
            controller._between_games_tick();controller.pipeline.submit.assert_not_called()
            controller.pipeline.activity_snapshot.return_value={}
            controller._between_games_tick()
        self.assertEqual(controller.pipeline.submit.call_args.args[1],GAP_KIND)

    def test_a_new_move_cannot_drop_a_queued_closing_line(self):
        with patch.object(SpeechPipeline,'_ensure_worker',return_value=True),patch('live_host.winsound.PlaySound'):
            pipeline=SpeechPipeline(LiveSettings(),Mock())
            try:
                pipeline.submit(0,'获胜收官',{},direct_text='这盘拿下。')
                pipeline.submit(1,'我方走棋播报',{},direct_text='马二进三。')
                self.assertIn('获胜收官',[job[4] for job in pipeline.jobs])
                pipeline.invalidate_chess()
                self.assertEqual([job[4] for job in pipeline.jobs],['获胜收官'])
            finally:pipeline.close()

    def test_an_already_playing_move_finishes_across_the_opponent_board_update(self):
        finished=threading.Event();heard=threading.Event();records=[]
        def notify(kind,text,data):
            records.append((kind,text,data))
            if kind=='reply':
                heard.set();pipeline.invalidate_chess()
        with patch('live_host.winsound.PlaySound') as play,patch.object(SpeechPipeline,'_audio_duration',return_value=.12),patch.object(SpeechPipeline,'_write_speech_marker'),patch.object(SpeechPipeline,'_mark_speech_finished',side_effect=lambda data:finished.set()):
            pipeline=SpeechPipeline(LiveSettings(speech_enabled=True,cooldown=0),notify,tts=lambda *a:'mock.wav')
            try:
                pipeline.prepare_move('关键走棋解说',{'speech_token':'short-current'},'车八进一。')
                pipeline.commit_move('short-current')
                self.assertTrue(heard.wait(1));self.assertTrue(finished.wait(1))
                self.assertFalse(any(row[0]=='speech_interrupted' for row in records))
                self.assertFalse(any(call.args[0] is None for call in play.call_args_list))
            finally:pipeline.close()

    def test_manual_pause_still_stops_the_clip_and_records_why(self):
        heard=threading.Event();interrupted=threading.Event();records=[]
        def notify(kind,text,data):
            records.append((kind,text,data))
            if kind=='reply':heard.set()
            if kind=='speech_interrupted':interrupted.set()
        with patch('live_host.winsound.PlaySound'),patch.object(SpeechPipeline,'_audio_duration',return_value=1),patch.object(SpeechPipeline,'_write_speech_marker'):
            pipeline=SpeechPipeline(LiveSettings(speech_enabled=True,cooldown=0),notify,tts=lambda *a:'mock.wav')
            try:
                pipeline.prepare_move('关键走棋解说',{'speech_token':'paused'},'车八进一。');pipeline.commit_move('paused')
                self.assertTrue(heard.wait(1));pipeline.pause_chess()
                self.assertTrue(interrupted.wait(1))
                self.assertIn('停止或暂停',[data['reason'] for kind,_,data in records if kind=='speech_interrupted'])
            finally:pipeline.close()

    def test_sandbox_does_not_start_a_sentence_that_cannot_finish_in_budget(self):
        with patch('live_host.winsound.PlaySound') as play,patch.object(SpeechPipeline,'_audio_duration',return_value=3),patch.object(SpeechPipeline,'_mark_speech_failed') as failed:
            pipeline=SpeechPipeline(LiveSettings(speech_enabled=True),Mock())
            try:
                state={'epoch':pipeline.cancel_epoch,'chess_epoch':pipeline.chess_epoch,'approved':True,'deadline':time.monotonic()+.2}
                self.assertFalse(pipeline._play_synced_audio('mock.wav',{'_sandbox_cue':True,'speech_token':'budget'},state,'总结。','沙盘推演解说'))
                play.assert_not_called();failed.assert_called_once()
            finally:pipeline.close()

    def test_after_move_demo_waits_for_its_real_move_sentence_to_finish(self):
        board,_,plan=plan_fixture();order=[]
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'live.json';config.write_text(json.dumps({'speech_enabled':True,'commentary_enabled':True}),encoding='utf-8')
            states=iter(({'token':'main','status':'started'},{'token':'main','status':'finished'}))
            def marker(*a):
                value=next(states);order.append(value['status']);return value
            with patch.object(player,'LIVE_CFG_PATH',str(config)),patch.object(player,'_read_speech_marker',side_effect=marker),patch.object(player,'write_runtime_status'),patch('sandbox_narration.prepared',return_value=True),patch('sandbox_narration.question_budget',return_value=None),patch('game_sandbox.enabled',return_value=True),patch('game_sandbox.play_plan',side_effect=lambda *a,**kw:(order.append('open') or {'entered':True,'safe_exit':True})),patch.object(player.time,'sleep'):
                player.run_game_sandbox('mock',board,'red_bottom',plan,'main',8,Mock(),lambda:False)
        self.assertEqual(order,['started','finished','open'])
