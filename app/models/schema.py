import warnings
from enum import Enum
import re
from typing import Any, List, Optional, Union

import pydantic
from pydantic import BaseModel, Field, field_validator

from app.config import config

# 忽略 Pydantic 的特定警告
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    message="Field name.*shadows an attribute in parent.*",
)


class VideoConcatMode(str, Enum):
    random = "random"
    sequential = "sequential"


class VideoTransitionMode(str, Enum):
    none = None
    shuffle = "Shuffle"
    fade_in = "FadeIn"
    fade_out = "FadeOut"
    slide_in = "SlideIn"
    slide_out = "SlideOut"


class VideoAspect(str, Enum):
    landscape = "16:9"
    portrait = "9:16"
    square = "1:1"

    def to_resolution(self):
        if self == VideoAspect.landscape:
            return 1920, 1080
        elif self == VideoAspect.portrait:
            return 1080, 1920
        elif self == VideoAspect.square:
            return 1080, 1080
        raise ValueError(f"unsupported video aspect: {self}")


class _Config:
    arbitrary_types_allowed = True


_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
_VIDEO_SOURCES = {"pexels", "pixabay", "coverr", "local"}
_BGM_TYPES = {"", "random", "custom"}
_SUBTITLE_POSITIONS = {"top", "center", "bottom", "custom"}
_MATERIAL_LOCALES = {"auto", "global", "china"}
_MATERIAL_PEOPLE_FILTERS = {"auto", "avoid", "allow"}
_MATERIAL_SOURCE_MODES = {"fallback", "mixed"}


class ValidatedBaseModel(BaseModel):
    @field_validator("video_source", check_fields=False)
    @classmethod
    def validate_video_source(cls, value):
        if value is None:
            return value
        if value not in _VIDEO_SOURCES:
            raise ValueError(
                f"video_source must be one of {sorted(_VIDEO_SOURCES)}"
            )
        return value

    @field_validator("bgm_type", check_fields=False)
    @classmethod
    def validate_bgm_type(cls, value):
        if value is None:
            return value
        if value not in _BGM_TYPES:
            raise ValueError(f"bgm_type must be one of {sorted(_BGM_TYPES)}")
        return value

    @field_validator("subtitle_position", check_fields=False)
    @classmethod
    def validate_subtitle_position(cls, value):
        if value is None:
            return value
        if value not in _SUBTITLE_POSITIONS:
            raise ValueError(
                f"subtitle_position must be one of {sorted(_SUBTITLE_POSITIONS)}"
            )
        return value

    @field_validator("text_fore_color", "stroke_color", check_fields=False)
    @classmethod
    def validate_hex_color(cls, value):
        if value is None:
            return value
        if not _HEX_COLOR_PATTERN.match(value):
            raise ValueError("color must use #RRGGBB format")
        return value

    @field_validator("text_background_color", check_fields=False)
    @classmethod
    def validate_text_background_color(cls, value):
        if isinstance(value, bool) or value is None:
            return value
        if not _HEX_COLOR_PATTERN.match(value):
            raise ValueError("text_background_color must be a boolean or #RRGGBB")
        return value

    @field_validator("material_locale", check_fields=False)
    @classmethod
    def validate_material_locale(cls, value):
        if value is None:
            return "auto"
        if value not in _MATERIAL_LOCALES:
            raise ValueError(
                f"material_locale must be one of {sorted(_MATERIAL_LOCALES)}"
            )
        return value

    @field_validator("material_people_filter", check_fields=False)
    @classmethod
    def validate_material_people_filter(cls, value):
        if value is None:
            return "auto"
        if value not in _MATERIAL_PEOPLE_FILTERS:
            raise ValueError(
                "material_people_filter must be one of "
                f"{sorted(_MATERIAL_PEOPLE_FILTERS)}"
            )
        return value

    @field_validator("material_source_mode", check_fields=False, mode="before")
    @classmethod
    def validate_material_source_mode(cls, value):
        if value is None:
            return "fallback"
        if value not in _MATERIAL_SOURCE_MODES:
            raise ValueError(
                f"material_source_mode must be one of {sorted(_MATERIAL_SOURCE_MODES)}"
            )
        return value

    @field_validator("video_sources", check_fields=False, mode="before")
    @classmethod
    def validate_video_sources(cls, value):
        if value is None:
            return value
        if isinstance(value, str):
            value = [source.strip() for source in re.split(r"[,，]", value)]
        sources = []
        for source in value:
            if not source:
                continue
            if source not in _VIDEO_SOURCES:
                raise ValueError(
                    f"video_sources must only contain {sorted(_VIDEO_SOURCES)}"
                )
            if source not in sources:
                sources.append(source)
        return sources or None


@pydantic.dataclasses.dataclass(config=_Config)
class MaterialInfo:
    provider: str = "pexels"
    url: str = ""
    duration: int = 0


class VideoParams(ValidatedBaseModel):
    """
    {
      "video_subject": "",
      "video_aspect": "横屏 16:9（西瓜视频）",
      "voice_name": "女生-晓晓",
      "bgm_name": "random",
      "font_name": "STHeitiMedium 黑体-中",
      "text_color": "#FFFFFF",
      "font_size": 60,
      "stroke_color": "#000000",
      "stroke_width": 1.5
    }
    """

    video_subject: str
    video_script: str = ""  # Script used to generate the video
    video_terms: Optional[str | list] = None  # Keywords used to generate the video
    video_aspect: Optional[VideoAspect] = VideoAspect.portrait.value
    video_concat_mode: Optional[VideoConcatMode] = VideoConcatMode.random.value
    video_transition_mode: Optional[VideoTransitionMode] = None
    video_clip_duration: int = Field(default=5, ge=1, le=60)
    match_materials_to_script: bool = False
    video_count: int = Field(default=1, ge=1, le=10)

    video_source: Optional[str] = "pexels"
    video_sources: Optional[List[str]] = None
    material_source_mode: str = "fallback"
    material_locale: str = "auto"
    material_people_filter: str = "auto"
    video_materials: Optional[List[MaterialInfo]] = (
        None  # Materials used to generate the video
    )
    
    custom_audio_file: Optional[str] = None  # Custom audio file path
    video_language: Optional[str] = ""  # auto detect

    voice_name: Optional[str] = ""
    voice_volume: float = Field(default=1.0, ge=0.0, le=5.0)
    voice_rate: float = Field(default=1.0, ge=0.25, le=4.0)
    bgm_type: Optional[str] = "random"
    bgm_file: Optional[str] = ""
    bgm_volume: float = Field(default=0.2, ge=0.0, le=5.0)

    subtitle_enabled: bool = True
    subtitle_position: Optional[str] = config.ui.get("subtitle_position", "bottom")  # top, bottom, center, custom
    custom_position: float = Field(default=config.ui.get("custom_position", 70.0), ge=0.0, le=100.0)
    font_name: Optional[str] = "STHeitiMedium.ttc"
    text_fore_color: Optional[str] = "#FFFFFF"
    text_background_color: Union[bool, str] = True
    rounded_subtitle_background: bool = False

    font_size: int = Field(default=60, ge=12, le=200)
    stroke_color: Optional[str] = "#000000"
    stroke_width: float = Field(default=1.5, ge=0.0, le=20.0)
    n_threads: int = Field(default=2, ge=1, le=16)
    paragraph_number: int = Field(default=1, ge=1, le=10)
    video_script_prompt: str = Field(default="", max_length=2000)
    custom_system_prompt: str = Field(default="", max_length=8000)


class SubtitleRequest(ValidatedBaseModel):
    video_script: str
    video_language: Optional[str] = ""
    voice_name: Optional[str] = "zh-CN-XiaoxiaoNeural-Female"
    voice_volume: float = Field(default=1.0, ge=0.0, le=5.0)
    voice_rate: float = Field(default=1.2, ge=0.25, le=4.0)
    bgm_type: Optional[str] = "random"
    bgm_file: Optional[str] = ""
    bgm_volume: float = Field(default=0.2, ge=0.0, le=5.0)
    subtitle_position: Optional[str] = config.ui.get("subtitle_position", "bottom")
    font_name: Optional[str] = "STHeitiMedium.ttc"
    text_fore_color: Optional[str] = "#FFFFFF"
    text_background_color: Union[bool, str] = True
    rounded_subtitle_background: bool = False
    font_size: int = Field(default=60, ge=12, le=200)
    stroke_color: Optional[str] = "#000000"
    stroke_width: float = Field(default=1.5, ge=0.0, le=20.0)
    video_source: Optional[str] = "local"
    subtitle_enabled: bool = True


class AudioRequest(ValidatedBaseModel):
    video_script: str
    video_language: Optional[str] = ""
    voice_name: Optional[str] = "zh-CN-XiaoxiaoNeural-Female"
    voice_volume: float = Field(default=1.0, ge=0.0, le=5.0)
    voice_rate: float = Field(default=1.2, ge=0.25, le=4.0)
    bgm_type: Optional[str] = "random"
    bgm_file: Optional[str] = ""
    bgm_volume: float = Field(default=0.2, ge=0.0, le=5.0)
    video_source: Optional[str] = "local"


class VideoScriptParams:
    """
    {
      "video_subject": "春天的花海",
      "video_language": "",
      "paragraph_number": 1,
      "video_script_prompt": "",
      "custom_system_prompt": ""
    }
    """

    video_subject: Optional[str] = "春天的花海"
    video_language: Optional[str] = ""
    paragraph_number: int = Field(default=1, ge=1, le=10)
    video_script_prompt: str = Field(default="", max_length=2000)
    custom_system_prompt: str = Field(default="", max_length=8000)


class VideoTermsParams:
    """
    {
      "video_subject": "",
      "video_script": "",
      "amount": 5
    }
    """

    video_subject: Optional[str] = "春天的花海"
    video_script: Optional[str] = (
        "春天的花海，如诗如画般展现在眼前。万物复苏的季节里，大地披上了一袭绚丽多彩的盛装。金黄的迎春、粉嫩的樱花、洁白的梨花、艳丽的郁金香……"
    )
    amount: Optional[int] = 5
    video_language: Optional[str] = ""
    match_script_order: bool = False
    material_locale: str = "auto"
    material_people_filter: str = "auto"


class VideoSocialMetadataParams:
    """
    {
      "video_subject": "A day in Shanghai",
      "video_script": "",
      "language": "auto",
      "platform": "tiktok"
    }
    """

    video_subject: Optional[str] = Field(default="A day in Shanghai", max_length=500)
    video_script: Optional[str] = Field(default="", max_length=8000)
    language: Optional[str] = Field(default="auto", max_length=64)
    platform: Optional[str] = Field(default="tiktok", max_length=64)


class BaseResponse(BaseModel):
    status: int = 200
    message: Optional[str] = "success"
    data: Any = None


class TaskVideoRequest(VideoParams, BaseModel):
    pass


class TaskQueryRequest(BaseModel):
    pass


class VideoScriptRequest(VideoScriptParams, BaseModel):
    pass


class VideoTermsRequest(VideoTermsParams, BaseModel):
    pass


class VideoSocialMetadataRequest(VideoSocialMetadataParams, BaseModel):
    pass


######################################################################################################
######################################################################################################
######################################################################################################
######################################################################################################
class TaskResponse(BaseResponse):
    class TaskResponseData(BaseModel):
        task_id: str

    data: TaskResponseData

    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {"task_id": "6c85c8cc-a77a-42b9-bc30-947815aa0558"},
            },
        }


class TaskQueryResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "state": 1,
                    "progress": 100,
                    "videos": [
                        "http://127.0.0.1:8080/tasks/6c85c8cc-a77a-42b9-bc30-947815aa0558/final-1.mp4"
                    ],
                    "combined_videos": [
                        "http://127.0.0.1:8080/tasks/6c85c8cc-a77a-42b9-bc30-947815aa0558/combined-1.mp4"
                    ],
                },
            },
        }


class TaskDeletionResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "state": 1,
                    "progress": 100,
                    "videos": [
                        "http://127.0.0.1:8080/tasks/6c85c8cc-a77a-42b9-bc30-947815aa0558/final-1.mp4"
                    ],
                    "combined_videos": [
                        "http://127.0.0.1:8080/tasks/6c85c8cc-a77a-42b9-bc30-947815aa0558/combined-1.mp4"
                    ],
                },
            },
        }


class VideoScriptResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "video_script": "春天的花海，是大自然的一幅美丽画卷。在这个季节里，大地复苏，万物生长，花朵争相绽放，形成了一片五彩斑斓的花海..."
                },
            },
        }


class VideoTermsResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {"video_terms": ["sky", "tree"]},
            },
        }


class VideoSocialMetadataResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "title": "A Day in Shanghai You Should Not Miss",
                    "caption": "Save this quick Shanghai inspiration and follow for more short travel ideas.",
                    "hashtags": ["#shorts", "#travel", "#shanghai", "#viral", "#fyp"],
                },
            },
        }


class BgmRetrieveResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "files": [
                        {
                            "name": "output013.mp3",
                            "size": 1891269,
                            "file": "/MoneyPrinterTurbo/resource/songs/output013.mp3",
                        }
                    ]
                },
            },
        }


class BgmUploadResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {"file": "/MoneyPrinterTurbo/resource/songs/example.mp3"},
            },
        }

class VideoMaterialRetrieveResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "files": [
                        {
                            "name": "example.mp4",
                            "size": 12345678,
                            "file": "/MoneyPrinterTurbo/resource/videos/example.mp4",
                        }
                    ]
                },
            },
        }

class VideoMaterialUploadResponse(BaseResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "status": 200,
                "message": "success",
                "data": {
                    "file": "/MoneyPrinterTurbo/resource/videos/example.mp4",
                },
            },
        }
