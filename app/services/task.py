import math
import os.path
import re
from os import path

from loguru import logger

from app.config import config
from app.models import const
from app.models.schema import VideoConcatMode, VideoParams
from app.services import llm, material, material_policy, subtitle, video, voice, upload_post
from app.services import state as sm
from app.utils import file_security, utils


def mark_task_failed(task_id: str, message: str, failed_stage: str = ""):
    current_task = sm.state.get_task(task_id) or {}
    progress = current_task.get("progress", 0)
    sm.state.update_task(
        task_id,
        state=const.TASK_STATE_FAILED,
        progress=progress,
        error_message=message,
        failed_stage=failed_stage,
    )


def generate_script(task_id, params):
    logger.info("\n\n## generating video script")
    video_script = params.video_script.strip()
    if not video_script:
        video_script = llm.generate_script(
            video_subject=params.video_subject,
            language=params.video_language,
            paragraph_number=params.paragraph_number,
            video_script_prompt=params.video_script_prompt,
            custom_system_prompt=params.custom_system_prompt,
        )
    else:
        logger.debug(f"video script: \n{video_script}")

    if not video_script:
        message = "failed to generate video script."
        mark_task_failed(task_id, message, "script")
        logger.error(message)
        return None

    return video_script


def generate_terms(task_id, params, video_script):
    logger.info("\n\n## generating video terms")
    video_terms = params.video_terms
    policy = material_policy.resolve_material_policy(
        video_language=getattr(params, "video_language", ""),
        video_subject=getattr(params, "video_subject", ""),
        video_script=video_script,
        material_locale=getattr(params, "material_locale", "auto"),
        people_filter=getattr(params, "material_people_filter", "auto"),
    )
    if not video_terms:
        # 开启素材按文案顺序匹配后，关键词本身也必须按脚本叙事顺序生成；
        # 否则后续即使顺序下载和顺序拼接，也只能复用一组全局主题词，
        # 无法改善“后面内容的画面提前出现”的问题。
        video_terms = llm.generate_terms(
            video_subject=params.video_subject,
            video_script=video_script,
            amount=8 if params.match_materials_to_script else 5,
            match_script_order=params.match_materials_to_script,
            material_locale=(
                "china" if policy.is_china_context else policy.material_locale
            ),
            avoid_people=policy.avoid_people,
        )
        if isinstance(video_terms, str) and "Error: " in video_terms:
            mark_task_failed(task_id, video_terms, "terms")
            logger.error(video_terms)
            return None
    else:
        if isinstance(video_terms, str):
            video_terms = [term.strip() for term in re.split(r"[,，]", video_terms)]
        elif isinstance(video_terms, list):
            video_terms = [term.strip() for term in video_terms]
        else:
            raise ValueError("video_terms must be a string or a list of strings.")

        logger.debug(f"video terms: {utils.to_json(video_terms)}")

    video_terms = material_policy.adapt_search_terms_for_policy(video_terms, policy)
    logger.info(
        "material policy resolved: "
        f"locale={policy.material_locale}, avoid_people={policy.avoid_people}, "
        f"is_chinese_content={policy.is_chinese_content}, "
        f"is_china_context={policy.is_china_context}, reason={policy.reason}"
    )

    if not video_terms:
        message = "failed to generate video terms."
        mark_task_failed(task_id, message, "terms")
        logger.error(message)
        return None

    return video_terms


def save_script_data(task_id, video_script, video_terms, params):
    script_file = path.join(utils.task_dir(task_id), "script.json")
    script_data = {
        "script": video_script,
        "search_terms": video_terms,
        "params": params,
    }

    with open(script_file, "w", encoding="utf-8") as f:
        f.write(utils.to_json(script_data))


def resolve_custom_audio_file(task_id: str, custom_audio_file: str | None) -> str:
    requested_file = (custom_audio_file or "").strip()
    if not requested_file:
        return ""

    task_dir = utils.task_dir(task_id)
    try:
        return file_security.resolve_path_within_directory(
            task_dir,
            requested_file,
        )
    except ValueError as exc:
        task_dir_error = exc

    server_audio_file = path.realpath(
        requested_file
        if path.isabs(requested_file)
        else path.join(utils.root_dir(), requested_file)
    )
    if not path.isabs(requested_file):
        project_root = path.realpath(utils.root_dir())
        try:
            if path.commonpath([project_root, server_audio_file]) != project_root:
                raise ValueError(
                    "relative custom audio paths must stay within the project directory"
                )
        except ValueError as exc:
            raise ValueError(
                "custom audio file must be task-local or an existing server-side file"
            ) from exc

    if not path.isfile(server_audio_file):
        raise ValueError(
            "custom audio file does not exist or is not a file"
        ) from task_dir_error

    return server_audio_file


def generate_audio(task_id, params, video_script):
    '''
    Generate audio for the video script.
    If a custom audio file is provided, it will be used directly.
    There will be no subtitle maker object returned in this case.
    Otherwise, TTS will be used to generate the audio.
    Returns:
        - audio_file: path to the generated or provided audio file
        - audio_duration: duration of the audio in seconds
        - sub_maker: subtitle maker object if TTS is used, None otherwise
    '''
    logger.info("\n\n## generating audio")
    # /audio 和 /subtitle 请求模型不包含 custom_audio_file，
    # 这里统一做兼容读取，避免直调接口时抛属性错误。
    requested_custom_audio_file = getattr(params, "custom_audio_file", None)
    try:
        custom_audio_file = resolve_custom_audio_file(
            task_id, requested_custom_audio_file
        )
    except ValueError as exc:
        logger.error(
            "custom audio file is invalid, "
            f"task_id: {task_id}, path: {requested_custom_audio_file}, error: {str(exc)}"
        )
        mark_task_failed(task_id, str(exc), "audio")
        return None, None, None

    if not custom_audio_file:
        logger.info("no custom audio file provided, using TTS to generate audio.")
        audio_file = path.join(utils.task_dir(task_id), "audio.mp3")
        sub_maker = voice.tts(
            text=video_script,
            voice_name=voice.parse_voice_name(params.voice_name),
            voice_rate=params.voice_rate,
            voice_volume=getattr(params, "voice_volume", 1.0),
            voice_file=audio_file,
        )
        if sub_maker is None:
            message = """failed to generate audio:
1. check if the language of the voice matches the language of the video script.
2. check if the network is available. If you are in China, it is recommended to use a VPN and enable the global traffic mode.
            """.strip()
            mark_task_failed(task_id, message, "audio")
            logger.error(message)
            return None, None, None
        audio_duration = math.ceil(voice.get_audio_duration(sub_maker))
        if audio_duration == 0:
            message = "failed to get audio duration."
            mark_task_failed(task_id, message, "audio")
            logger.error(message)
            return None, None, None
        return audio_file, audio_duration, sub_maker
    else:
        logger.info(f"using custom audio file: {custom_audio_file}")
        audio_duration = voice.get_audio_duration(custom_audio_file)
        if audio_duration == 0:
            message = "failed to get audio duration from custom audio file."
            mark_task_failed(task_id, message, "audio")
            logger.error(message)
            return None, None, None
        return custom_audio_file, audio_duration, None

def generate_subtitle(task_id, params, video_script, sub_maker, audio_file):
    '''
    Generate subtitle for the video script.
    If subtitle generation is disabled or no subtitle maker is provided, it will return an empty string.
    Otherwise, it will generate the subtitle using the specified provider.
    Returns:
        - subtitle_path: path to the generated subtitle file
    '''
    logger.info("\n\n## generating subtitle")
    if not params.subtitle_enabled:
        return ""

    subtitle_path = path.join(utils.task_dir(task_id), "subtitle.srt")
    subtitle_provider = config.app.get("subtitle_provider", "edge").strip().lower()
    logger.info(f"\n\n## generating subtitle, provider: {subtitle_provider}")

    if sub_maker is None and subtitle_provider != "whisper":
        logger.warning(
            "subtitle maker is unavailable; use subtitle_provider='whisper' "
            "to generate subtitles from an existing audio file"
        )
        return ""

    subtitle_fallback = False
    if subtitle_provider == "edge":
        voice.create_subtitle(
            text=video_script, sub_maker=sub_maker, subtitle_file=subtitle_path
        )
        if not os.path.exists(subtitle_path):
            subtitle_fallback = True
            logger.warning("subtitle file not found, fallback to whisper")

    if subtitle_provider == "whisper" or subtitle_fallback:
        subtitle.create(audio_file=audio_file, subtitle_file=subtitle_path)
        logger.info("\n\n## correcting subtitle")
        subtitle.correct(subtitle_file=subtitle_path, video_script=video_script)

    subtitle_lines = subtitle.file_to_subtitles(subtitle_path)
    if not subtitle_lines:
        logger.warning(f"subtitle file is invalid: {subtitle_path}")
        return ""

    return subtitle_path


def _get_requested_video_sources(params):
    sources = getattr(params, "video_sources", None)
    if sources:
        if isinstance(sources, str):
            requested_sources = [
                source.strip() for source in re.split(r"[,，]", sources)
            ]
        else:
            requested_sources = list(sources)
    else:
        requested_sources = [getattr(params, "video_source", "pexels")]

    primary_source = getattr(params, "video_source", "pexels")
    if primary_source and primary_source not in requested_sources:
        requested_sources.insert(0, primary_source)

    normalized_sources = []
    for source in requested_sources:
        source_name = (source or "").strip().lower()
        if source_name and source_name not in normalized_sources:
            normalized_sources.append(source_name)
    return normalized_sources


def get_video_materials(task_id, params, video_terms, audio_duration):
    requested_sources = _get_requested_video_sources(params)
    online_sources = [
        source for source in requested_sources if source in {"pexels", "pixabay", "coverr"}
    ]
    local_requested = "local" in requested_sources
    source_mode = getattr(params, "material_source_mode", "fallback")
    source_mode = source_mode if source_mode in {"fallback", "mixed"} else "fallback"
    required_duration = audio_duration * params.video_count
    local_video_paths = []
    local_duration = 0.0

    if local_requested:
        logger.info("\n\n## preprocess local materials")
        materials = video.preprocess_video(
            materials=params.video_materials, clip_duration=params.video_clip_duration
        )
        if not materials:
            if not online_sources:
                message = "no valid materials found, please check the materials and try again."
                mark_task_failed(task_id, message, "materials")
                logger.error(message)
                return None
            logger.warning("no valid local materials found, fallback to online sources")
        else:
            local_video_paths = [material_info.url for material_info in materials]
            local_duration = len(local_video_paths) * params.video_clip_duration
            if source_mode == "fallback" and local_duration >= required_duration:
                return local_video_paths

    if online_sources:
        logger.info(
            f"\n\n## downloading videos from {online_sources}, mode: {source_mode}"
        )
        policy = material_policy.resolve_material_policy(
            video_language=getattr(params, "video_language", ""),
            video_subject=getattr(params, "video_subject", ""),
            video_script=getattr(params, "video_script", ""),
            material_locale=getattr(params, "material_locale", "auto"),
            people_filter=getattr(params, "material_people_filter", "auto"),
        )
        # 顺序匹配模式只在用户显式开启时生效。这里强制素材下载按关键词顺序
        # 轮询，避免某个早期关键词下载太多素材，把后续脚本主题挤出最终时间线。
        downloaded_videos = material.download_videos(
            task_id=task_id,
            search_terms=video_terms,
            source=params.video_source,
            video_aspect=params.video_aspect,
            video_concat_mode=(
                VideoConcatMode.sequential
                if params.match_materials_to_script
                else params.video_concat_mode
            ),
            sources=online_sources,
            source_mode=source_mode,
            audio_duration=(
                max(required_duration - local_duration, 0.0)
                if source_mode == "fallback"
                else required_duration
            ),
            max_clip_duration=params.video_clip_duration,
            match_script_order=params.match_materials_to_script,
            material_locale=(
                "china" if policy.is_china_context else policy.material_locale
            ),
            avoid_people=policy.avoid_people,
        )
        if not downloaded_videos:
            if local_video_paths:
                return local_video_paths
            message = "failed to download videos, maybe the network is not available. if you are in China, please use a VPN."
            mark_task_failed(task_id, message, "materials")
            logger.error(message)
            return None
        return local_video_paths + downloaded_videos

    if local_video_paths:
        return local_video_paths

    message = "please select a valid video source."
    mark_task_failed(task_id, message, "materials")
    logger.error(message)
    return None


def generate_final_videos(
    task_id, params, downloaded_videos, audio_file, subtitle_path
):
    final_video_paths = []
    combined_video_paths = []
    # 多视频生成默认会打散素材以增加差异；但“按文案顺序匹配素材”追求的是
    # 时间线稳定性和可解释性，所以开启后所有输出都使用顺序拼接。
    if params.match_materials_to_script:
        video_concat_mode = VideoConcatMode.sequential
    elif params.video_count == 1:
        video_concat_mode = params.video_concat_mode
    else:
        video_concat_mode = VideoConcatMode.random
    video_transition_mode = params.video_transition_mode

    _progress = 50
    for i in range(params.video_count):
        index = i + 1
        combined_video_path = path.join(
            utils.task_dir(task_id), f"combined-{index}.mp4"
        )
        logger.info(f"\n\n## combining video: {index} => {combined_video_path}")
        video.combine_videos(
            combined_video_path=combined_video_path,
            video_paths=downloaded_videos,
            audio_file=audio_file,
            video_aspect=params.video_aspect,
            video_concat_mode=video_concat_mode,
            video_transition_mode=video_transition_mode,
            max_clip_duration=params.video_clip_duration,
            threads=params.n_threads,
        )

        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_path = path.join(utils.task_dir(task_id), f"final-{index}.mp4")

        logger.info(f"\n\n## generating video: {index} => {final_video_path}")
        video.generate_video(
            video_path=combined_video_path,
            audio_path=audio_file,
            subtitle_path=subtitle_path,
            output_file=final_video_path,
            params=params,
        )

        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_paths.append(final_video_path)
        combined_video_paths.append(combined_video_path)

    return final_video_paths, combined_video_paths


def _start_impl(task_id, params: VideoParams, stop_at: str = "video"):
    logger.info(f"start task: {task_id}, stop_at: {stop_at}")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)

    # 1. Generate script
    video_script = generate_script(task_id, params)
    if not video_script or "Error: " in video_script:
        mark_task_failed(task_id, "video script generation returned no usable content", "script")
        return

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=10)

    if stop_at == "script":
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, script=video_script
        )
        return {"script": video_script}

    # 2. Generate terms
    video_terms = ""
    if params.video_source != "local":
        video_terms = generate_terms(task_id, params, video_script)
        if not video_terms:
            mark_task_failed(task_id, "video terms generation returned no usable content", "terms")
            return

    save_script_data(task_id, video_script, video_terms, params)

    if stop_at == "terms":
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, terms=video_terms
        )
        return {"script": video_script, "terms": video_terms}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=20)

    # 3. Generate audio
    audio_file, audio_duration, sub_maker = generate_audio(
        task_id, params, video_script
    )
    if not audio_file:
        mark_task_failed(task_id, "audio generation returned no usable file", "audio")
        return

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=30)

    if stop_at == "audio":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            audio_file=audio_file,
        )
        return {"audio_file": audio_file, "audio_duration": audio_duration}

    # 4. Generate subtitle
    subtitle_path = generate_subtitle(
        task_id, params, video_script, sub_maker, audio_file
    )

    if stop_at == "subtitle":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            subtitle_path=subtitle_path,
        )
        return {"subtitle_path": subtitle_path}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=40)

    # 5. Get video materials
    downloaded_videos = get_video_materials(
        task_id, params, video_terms, audio_duration
    )
    if not downloaded_videos:
        mark_task_failed(task_id, "material preparation returned no usable files", "materials")
        return

    if stop_at == "materials":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            materials=downloaded_videos,
        )
        return {"materials": downloaded_videos}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=50)

    # 仅完整视频生成流程才需要处理视频拼接模式；
    # 这样可以避免 /subtitle 和 /audio 这类请求访问不存在的字段。
    if type(params.video_concat_mode) is str:
        params.video_concat_mode = VideoConcatMode(params.video_concat_mode)

    # 6. Generate final videos
    final_video_paths, combined_video_paths = generate_final_videos(
        task_id, params, downloaded_videos, audio_file, subtitle_path
    )

    if not final_video_paths:
        mark_task_failed(task_id, "video generation returned no output files", "video")
        return

    logger.success(
        f"task {task_id} finished, generated {len(final_video_paths)} videos."
    )

    # 7. Cross-post to social platforms (if enabled)
    cross_post_results = []
    if upload_post.upload_post_service.is_configured() and upload_post.upload_post_service.auto_upload:
        platforms = upload_post.upload_post_service.platforms
        logger.info(f"\n\n## cross-posting videos to {', '.join(platforms)}")

        youtube_extra = None
        if "youtube" in platforms:
            metadata = llm.generate_social_metadata(
                video_subject=params.video_subject,
                video_script=video_script,
                language=params.video_language or "",
                platform="youtube_shorts",
            )
            youtube_extra = {
                "youtube_title": metadata.get("title", params.video_subject),
                "youtube_description": metadata.get("caption", ""),
                "tags": metadata.get("hashtags", []),
                "privacyStatus": upload_post.upload_post_service.youtube_privacy_status,
                "containsSyntheticMedia": True,
            }

        for video_path in final_video_paths:
            result = upload_post.cross_post_video(
                video_path=video_path,
                title=params.video_subject or "Check out this video! #shorts #viral",
                youtube_extra=youtube_extra,
            )
            cross_post_results.append(result)
            if result.get('success'):
                logger.info(f"Cross-posted: {video_path}")
            else:
                logger.warning(
                    f"Failed to cross-post: {video_path} - {result.get('error', 'Unknown error')}"
                )

    kwargs = {
        "videos": final_video_paths,
        "combined_videos": combined_video_paths,
        "script": video_script,
        "terms": video_terms,
        "audio_file": audio_file,
        "audio_duration": audio_duration,
        "subtitle_path": subtitle_path,
        "materials": downloaded_videos,
        "cross_post_results": cross_post_results if cross_post_results else None,
    }
    sm.state.update_task(
        task_id, state=const.TASK_STATE_COMPLETE, progress=100, **kwargs
    )
    return kwargs


def start(task_id, params: VideoParams, stop_at: str = "video"):
    try:
        return _start_impl(task_id, params, stop_at)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        logger.exception(
            f"task failed with unhandled exception, task_id: {task_id}, stop_at: {stop_at}"
        )
        mark_task_failed(task_id, message, stop_at or "task")
        return None


if __name__ == "__main__":
    task_id = "task_id"
    params = VideoParams(
        video_subject="金钱的作用",
        voice_name="zh-CN-XiaoyiNeural-Female",
        voice_rate=1.0,
    )
    start(task_id, params, stop_at="video")
