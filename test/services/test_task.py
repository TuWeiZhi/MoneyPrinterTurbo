import unittest
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.services import task as tm
from app.models.schema import MaterialInfo, VideoParams
from app.utils import utils

resources_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")
RUN_INTEGRATION_TESTS = os.environ.get("MPT_RUN_INTEGRATION_TESTS", "").lower() in {
    "1",
    "true",
    "yes",
}

class TestTaskService(unittest.TestCase):
    def setUp(self):
        pass
    
    def tearDown(self):
        pass

    def test_generate_script_forwards_advanced_prompt_options(self):
        """
        任务生成入口和 WebUI/API 共用 VideoParams。这里验证自动生成文案时，
        高级提示词参数会继续传到 LLM 服务层，避免只在 /scripts 接口生效。
        """
        params = VideoParams(
            video_subject="咖啡",
            video_script="",
            video_language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

        with patch.object(tm.llm, "generate_script", return_value="生成的文案") as generate:
            result = tm.generate_script("task-id", params)

        self.assertEqual(result, "生成的文案")
        generate.assert_called_once_with(
            video_subject="咖啡",
            language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

    def test_generate_terms_uses_script_order_mode_when_enabled(self):
        """
        默认模式不受影响；只有用户显式开启素材按文案顺序匹配时，任务层才
        要求 LLM 生成有序关键词，并适当增加关键词数量以覆盖更多脚本片段。
        """
        params = VideoParams(
            video_subject="城市通勤",
            video_script="",
            match_materials_to_script=True,
        )

        with patch.object(tm.llm, "generate_terms", return_value=["city", "train"]) as generate:
            result = tm.generate_terms("task-id", params, "先城市，再地铁")

        self.assertEqual(result, ["city no people", "train no people"])
        generate.assert_called_once_with(
            video_subject="城市通勤",
            video_script="先城市，再地铁",
            amount=8,
            match_script_order=True,
            material_locale="china",
            avoid_people=True,
        )
    
    def test_generate_terms_avoids_people_for_chinese_context(self):
        params = VideoParams(
            video_subject="\u4e0a\u6d77\u804c\u573a\u6548\u7387",
            video_script="",
            video_language="zh-CN",
            material_locale="auto",
            material_people_filter="auto",
        )

        with patch.object(
            tm.llm,
            "generate_terms",
            return_value=["businessman office", "city skyline"],
        ) as generate:
            result = tm.generate_terms(
                "task-id",
                params,
                "\u4e0a\u6d77\u4e0a\u73ed\u65cf\u7684\u4e00\u5929",
            )

        self.assertEqual(
            result,
            ["office desk no people", "city skyline no people"],
        )
        generate.assert_called_once_with(
            video_subject="\u4e0a\u6d77\u804c\u573a\u6548\u7387",
            video_script="\u4e0a\u6d77\u4e0a\u73ed\u65cf\u7684\u4e00\u5929",
            amount=5,
            match_script_order=False,
            material_locale="china",
            avoid_people=True,
        )

    def test_generate_audio_uses_custom_file_inside_task_directory(self):
        task_id = "test-custom-audio-safe"
        task_dir = utils.task_dir(task_id)
        custom_audio_file = os.path.join(task_dir, "custom-audio.mp3")
        with open(custom_audio_file, "wb") as audio:
            audio.write(b"fake audio")

        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=custom_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.voice, "get_audio_duration", return_value=7),
            ):
                audio_file, audio_duration, sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, os.path.realpath(custom_audio_file))
        self.assertEqual(audio_duration, 7)
        self.assertIsNone(sub_maker)
        tts.assert_not_called()

    def test_generate_audio_accepts_server_side_custom_file(self):
        task_id = "test-custom-audio-server-side"
        task_dir = utils.task_dir(task_id)

        with tempfile.NamedTemporaryFile(suffix=".mp3") as server_audio:
            server_audio.write(b"fake audio")
            server_audio.flush()
            params = VideoParams(
                video_subject="custom audio",
                video_script="",
                custom_audio_file=server_audio.name,
                voice_name="test-voice",
            )

            try:
                with (
                    patch.object(tm.voice, "tts") as tts,
                    patch.object(tm.voice, "get_audio_duration", return_value=6),
                ):
                    audio_file, audio_duration, result_sub_maker = tm.generate_audio(
                        task_id, params, "script"
                    )
            finally:
                shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, os.path.realpath(server_audio.name))
        self.assertEqual(audio_duration, 6)
        self.assertIsNone(result_sub_maker)
        tts.assert_not_called()

    def test_generate_audio_rejects_missing_custom_file_without_tts(self):
        task_id = "test-custom-audio-missing"
        task_dir = utils.task_dir(task_id)
        missing_audio_file = os.path.join(task_dir, "missing.mp3")
        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=missing_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                audio_file, audio_duration, result_sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertIsNone(audio_file)
        self.assertIsNone(audio_duration)
        self.assertIsNone(result_sub_maker)
        tts.assert_not_called()
        update_task.assert_called_with(
            task_id,
            state=tm.const.TASK_STATE_FAILED,
            progress=0,
            error_message="custom audio file does not exist or is not a file",
            failed_stage="audio",
        )

    def test_generate_audio_forwards_voice_volume_to_tts(self):
        params = VideoParams(
            video_subject="tts",
            video_script="",
            voice_name="zh-CN-XiaoxiaoNeural-Female",
            voice_volume=1.8,
            voice_rate=1.1,
        )

        with (
            patch.object(tm.voice, "parse_voice_name", return_value="voice") as parse,
            patch.object(tm.voice, "tts", return_value=object()) as tts,
            patch.object(tm.voice, "get_audio_duration", return_value=4),
        ):
            audio_file, audio_duration, sub_maker = tm.generate_audio(
                "test-tts-volume", params, "script"
            )

        self.assertTrue(audio_file.endswith("audio.mp3"))
        self.assertEqual(audio_duration, 4)
        self.assertIsNotNone(sub_maker)
        parse.assert_called_once_with("zh-CN-XiaoxiaoNeural-Female")
        self.assertEqual(tts.call_args.kwargs["voice_volume"], 1.8)

    def test_generate_subtitle_allows_custom_audio_with_whisper(self):
        original_app_config = dict(config.app)
        config.app["subtitle_provider"] = "whisper"
        try:
            with (
                patch.object(tm.subtitle, "create") as create,
                patch.object(tm.subtitle, "correct") as correct,
                patch.object(tm.subtitle, "file_to_subtitles", return_value=[(1, "00:00:00,000 --> 00:00:01,000", "hello")]),
            ):
                subtitle_path = tm.generate_subtitle(
                    "test-whisper-custom-audio",
                    VideoParams(
                        video_subject="custom audio",
                        video_script="hello",
                        custom_audio_file="custom.mp3",
                        subtitle_enabled=True,
                    ),
                    "hello",
                    sub_maker=None,
                    audio_file="custom.mp3",
                )
        finally:
            config.app.clear()
            config.app.update(original_app_config)

        self.assertTrue(subtitle_path.endswith("subtitle.srt"))
        create.assert_called_once_with(audio_file="custom.mp3", subtitle_file=subtitle_path)
        correct.assert_called_once_with(subtitle_file=subtitle_path, video_script="hello")

    def test_get_video_materials_forwards_multi_source_options(self):
        params = VideoParams(
            video_subject="city",
            video_script="city commute",
            video_terms=["city"],
            video_source="pexels",
            video_sources=["pixabay", "pexels"],
            material_source_mode="mixed",
            material_locale="global",
        )

        with patch.object(
            tm.material, "download_videos", return_value=["/tmp/video.mp4"]
        ) as download:
            result = tm.get_video_materials(
                "task-multi-source", params, ["city"], audio_duration=12
            )

        self.assertEqual(result, ["/tmp/video.mp4"])
        self.assertEqual(download.call_args.kwargs["sources"], ["pixabay", "pexels"])
        self.assertEqual(download.call_args.kwargs["source_mode"], "mixed")
        self.assertEqual(download.call_args.kwargs["audio_duration"], 12)

    def test_get_video_materials_uses_local_first_then_online_fallback(self):
        params = VideoParams(
            video_subject="city",
            video_script="city commute",
            video_terms=["city"],
            video_source="pixabay",
            video_sources=["local", "pixabay"],
            material_source_mode="fallback",
            video_clip_duration=3,
            material_locale="global",
            video_materials=[
                MaterialInfo(provider="local", url="local-a.mp4", duration=0),
                MaterialInfo(provider="local", url="local-b.mp4", duration=0),
            ],
        )
        local_materials = [
            MaterialInfo(provider="local", url="/tmp/local-a.mp4", duration=0),
            MaterialInfo(provider="local", url="/tmp/local-b.mp4", duration=0),
        ]

        with (
            patch.object(tm.video, "preprocess_video", return_value=local_materials),
            patch.object(tm.material, "download_videos", return_value=["/tmp/online.mp4"]) as download,
        ):
            result = tm.get_video_materials(
                "task-local-online", params, ["city"], audio_duration=10
            )

        self.assertEqual(
            result, ["/tmp/local-a.mp4", "/tmp/local-b.mp4", "/tmp/online.mp4"]
        )
        self.assertEqual(download.call_args.kwargs["sources"], ["pixabay"])
        self.assertEqual(download.call_args.kwargs["source_mode"], "fallback")
        self.assertEqual(download.call_args.kwargs["audio_duration"], 4)

    def test_get_video_materials_returns_local_when_fallback_local_is_enough(self):
        params = VideoParams(
            video_subject="city",
            video_script="city commute",
            video_terms=["city"],
            video_source="pixabay",
            video_sources=["local", "pixabay"],
            material_source_mode="fallback",
            video_clip_duration=5,
            material_locale="global",
            video_materials=[
                MaterialInfo(provider="local", url="local-a.mp4", duration=0),
                MaterialInfo(provider="local", url="local-b.mp4", duration=0),
            ],
        )
        local_materials = [
            MaterialInfo(provider="local", url="/tmp/local-a.mp4", duration=0),
            MaterialInfo(provider="local", url="/tmp/local-b.mp4", duration=0),
        ]

        with (
            patch.object(tm.video, "preprocess_video", return_value=local_materials),
            patch.object(tm.material, "download_videos") as download,
        ):
            result = tm.get_video_materials(
                "task-local-enough", params, ["city"], audio_duration=8
            )

        self.assertEqual(result, ["/tmp/local-a.mp4", "/tmp/local-b.mp4"])
        download.assert_not_called()

    @unittest.skipUnless(
        RUN_INTEGRATION_TESTS,
        "MPT_RUN_INTEGRATION_TESTS not set",
    )
    def test_task_local_materials(self):
        task_id = "00000000-0000-0000-0000-000000000000"
        video_materials=[]
        for i in range(1, 4):
            video_materials.append(MaterialInfo(
                provider="local",
                url=os.path.join(resources_dir, f"{i}.png"),
                duration=0
            ))

        params = VideoParams(
            video_subject="金钱的作用",
            video_script="金钱不仅是交换媒介，更是社会资源的分配工具。它能满足基本生存需求，如食物和住房，也能提供教育、医疗等提升生活品质的机会。拥有足够的金钱意味着更多选择权，比如职业自由或创业可能。但金钱的作用也有边界，它无法直接购买幸福、健康或真诚的人际关系。过度追逐财富可能导致价值观扭曲，忽视精神层面的需求。理想的状态是理性看待金钱，将其作为实现目标的工具而非终极目的。",
            video_terms="money importance, wealth and society, financial freedom, money and happiness, role of money",
            video_aspect="9:16",
            video_concat_mode="random",
            video_transition_mode="None",
            video_clip_duration=3,
            video_count=1,
            video_source="local",
            video_materials=video_materials,
            video_language="",
            voice_name="zh-CN-XiaoxiaoNeural-Female",
            voice_volume=1.0,
            voice_rate=1.0,
            bgm_type="random",
            bgm_file="",
            bgm_volume=0.2,
            subtitle_enabled=True,
            subtitle_position="bottom",
            custom_position=70.0,
            font_name="MicrosoftYaHeiBold.ttc",
            text_fore_color="#FFFFFF",
            text_background_color=True,
            font_size=60,
            stroke_color="#000000",
            stroke_width=1.5,
            n_threads=2,
            paragraph_number=1
        )
        result = tm.start(task_id=task_id, params=params)
        print(result)
    

if __name__ == "__main__":
    unittest.main()
