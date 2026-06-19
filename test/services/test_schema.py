import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pydantic import ValidationError

from app.models.schema import SubtitleRequest, VideoAspect, VideoParams


class TestVideoAspect(unittest.TestCase):
    def test_to_resolution_known_aspects(self):
        self.assertEqual(VideoAspect.landscape.to_resolution(), (1920, 1080))
        self.assertEqual(VideoAspect.portrait.to_resolution(), (1080, 1920))
        self.assertEqual(VideoAspect.square.to_resolution(), (1080, 1080))

    def test_to_resolution_rejects_unsupported_value(self):
        with self.assertRaises(ValueError):
            VideoAspect.to_resolution("4:5")


class TestRequestValidation(unittest.TestCase):
    def test_video_params_rejects_out_of_range_business_values(self):
        invalid_cases = [
            {"video_count": 0},
            {"video_clip_duration": 0},
            {"n_threads": 0},
            {"voice_rate": 0.1},
            {"voice_volume": 6.0},
            {"bgm_volume": -0.1},
            {"custom_position": 101},
            {"font_size": 5},
            {"stroke_width": -1},
        ]

        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    VideoParams(video_subject="test", **overrides)

    def test_video_params_validates_enums_and_colors(self):
        invalid_cases = [
            {"video_source": "unknown"},
            {"subtitle_position": "left"},
            {"text_fore_color": "white"},
            {"stroke_color": "#12345"},
            {"text_background_color": "black"},
            {"bgm_type": "loud"},
            {"material_locale": "mars"},
            {"material_people_filter": "maybe"},
            {"material_source_mode": "parallel"},
            {"video_sources": ["pexels", "unknown"]},
        ]

        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    VideoParams(video_subject="test", **overrides)

    def test_subtitle_enabled_parses_boolean_strings(self):
        self.assertFalse(
            SubtitleRequest(video_script="hello", subtitle_enabled="false").subtitle_enabled
        )
        self.assertTrue(
            SubtitleRequest(video_script="hello", subtitle_enabled="true").subtitle_enabled
        )

    def test_video_params_normalizes_multi_video_sources(self):
        params = VideoParams(
            video_subject="test",
            video_sources="pixabay, pexels, pixabay, coverr",
            material_source_mode="mixed",
        )

        self.assertEqual(params.video_sources, ["pixabay", "pexels", "coverr"])
        self.assertEqual(params.material_source_mode, "mixed")

    def test_video_params_accepts_local_in_multi_video_sources(self):
        params = VideoParams(
            video_subject="test",
            video_sources=["local", "pixabay", "local"],
        )

        self.assertEqual(params.video_sources, ["local", "pixabay"])


if __name__ == "__main__":
    unittest.main()
