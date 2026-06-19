import os
import random
import threading
from typing import List
from urllib.parse import urlencode

import requests
from loguru import logger
from moviepy.video.io.VideoFileClip import VideoFileClip

from app.config import config
from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode
from app.services import material_policy
from app.utils import utils

# Thread-safe counter for API key rotation
_api_key_counter = 0
_api_key_lock = threading.Lock()

ONLINE_VIDEO_SOURCES = ("pexels", "pixabay", "coverr")
GLOBAL_SOURCE_PRIORITY = ("pexels", "pixabay", "coverr")
CHINA_SOURCE_PRIORITY = ("pixabay", "pexels", "coverr")


def _get_tls_verify() -> bool:
    # 默认开启 TLS 证书校验，防止素材搜索和下载过程被中间人篡改。
    # 仅在企业代理、自签证书等明确需要的场景下，允许用户通过
    # `config.toml` 显式设置 `tls_verify = false` 临时关闭。
    tls_verify = config.app.get("tls_verify", True)
    if isinstance(tls_verify, str):
        tls_verify = tls_verify.strip().lower() not in ("0", "false", "no", "off")

    if not tls_verify:
        logger.warning(
            "TLS certificate verification is disabled by config.app.tls_verify=false. "
            "Only use this in trusted proxy environments."
        )

    return bool(tls_verify)


def get_api_key(cfg_key: str):
    api_keys = config.app.get(cfg_key)
    if not api_keys:
        raise ValueError(
            f"\n\n##### {cfg_key} is not set #####\n\nPlease set it in the config.toml file: {config.config_file}\n\n"
            f"{utils.to_json(config.app)}"
        )

    # if only one key is provided, return it
    if isinstance(api_keys, str):
        return api_keys

    global _api_key_counter
    with _api_key_lock:
        _api_key_counter += 1
        return api_keys[_api_key_counter % len(api_keys)]


def search_videos_pexels(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)
    video_orientation = aspect.name
    video_width, video_height = aspect.to_resolution()
    api_key = get_api_key("pexels_api_keys")
    headers = {
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    }
    # Build URL
    params = {"query": search_term, "per_page": 20, "orientation": video_orientation}
    query_url = f"https://api.pexels.com/videos/search?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers=headers,
            proxies=config.proxy,
            verify=_get_tls_verify(),
            timeout=(30, 60),
        )
        response = r.json()
        video_items = []
        if "videos" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["videos"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video_files = v["video_files"]
            # loop through each url to determine the best quality
            for video in video_files:
                w = int(video["width"])
                h = int(video["height"])
                if w == video_width and h == video_height:
                    item = MaterialInfo()
                    item.provider = "pexels"
                    item.url = video["link"]
                    item.duration = duration
                    video_items.append(item)
                    break
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def search_videos_pixabay(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    aspect = VideoAspect(video_aspect)

    video_width, video_height = aspect.to_resolution()

    api_key = get_api_key("pixabay_api_keys")
    # Build URL
    params = {
        "q": search_term,
        "video_type": "all",  # Accepted values: "all", "film", "animation"
        "per_page": 50,
        "key": api_key,
    }
    query_url = f"https://pixabay.com/api/videos/?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url, proxies=config.proxy, verify=_get_tls_verify(), timeout=(30, 60)
        )
        response = r.json()
        video_items = []
        if "hits" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items
        videos = response["hits"]
        # loop through each video in the result
        for v in videos:
            duration = v["duration"]
            # check if video has desired minimum duration
            if duration < minimum_duration:
                continue
            video_files = v["videos"]
            # loop through each url to determine the best quality
            for video_type in video_files:
                video = video_files[video_type]
                w = int(video["width"])
                # h = int(video["height"])
                if w >= video_width:
                    item = MaterialInfo()
                    item.provider = "pixabay"
                    item.url = video["url"]
                    item.duration = duration
                    video_items.append(item)
                    break
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def search_videos_coverr(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> List[MaterialInfo]:
    """
    Coverr (https://coverr.co) - free HD/4K stock videos,
    subject to Coverr license terms (https://coverr.co/license).

    Coverr API notes (based on official docs at api.coverr.co/docs/):
      - 鉴权: Authorization: Bearer <api_key>
      - 搜索端点: GET /videos?query=...,响应结构 {"hits": [...], ...}
      - 加 ?urls=true 在搜索响应里直接返回 mp4 直链
      - URL 是 signed JWT(绑定 API key,无过期时间)
      - Coverr 库以 16:9 横屏为主,9:16 portrait 占比极低(约 1%)
        因此本函数不做 aspect_ratio 过滤,由下游 video.py 的
        resize + letterbox 逻辑统一处理
      - duration 字段同时存在 number 和 string 两种形态,本函数都接受

    本函数使用 urls.mp4_download 字段作为下载地址 —— 按 Coverr 官方文档
    (https://api.coverr.co/docs/videos/#download-a-video) 的说法,
    GET 这个 URL 本身就被 Coverr 当作一次合法的 download 事件计入统计,
    无需再调用 PATCH /videos/:id/stats/downloads。
    """
    api_key = get_api_key("coverr_api_keys")
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {
        "query": search_term,
        "page_size": 20,
        "urls": "true",
        "sort": "popular",
    }
    query_url = f"https://api.coverr.co/videos?{urlencode(params)}"
    logger.info(f"searching videos: {query_url}, with proxies: {config.proxy}")

    try:
        r = requests.get(
            query_url,
            headers=headers,
            proxies=config.proxy,
            verify=_get_tls_verify(),
            timeout=(30, 60),
        )
        response = r.json()
        video_items: List[MaterialInfo] = []

        if not isinstance(response, dict) or "hits" not in response:
            logger.error(f"search videos failed: {response}")
            return video_items

        for v in response["hits"]:
            # duration 在不同响应里可能是 number(11.625) 或 string("10.500000")
            try:
                duration = int(float(v.get("duration") or 0))
            except (TypeError, ValueError):
                continue
            if duration < minimum_duration:
                continue

            video_id = v.get("id")
            mp4_download_url = (v.get("urls") or {}).get("mp4_download")
            if not video_id or not mp4_download_url:
                continue

            item = MaterialInfo()
            item.provider = "coverr"
            item.url = mp4_download_url
            item.duration = duration
            video_items.append(item)
        return video_items
    except Exception as e:
        logger.error(f"search videos failed: {str(e)}")

    return []


def save_video(video_url: str, save_dir: str = "") -> str:
    if not save_dir:
        save_dir = utils.storage_dir("cache_videos")

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    url_hash = utils.md5(video_url)
    video_id = f"vid-{url_hash}"
    video_path = f"{save_dir}/{video_id}.mp4"

    # if video already exists, return the path
    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        logger.info(f"video already exists: {video_path}")
        return video_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    response = requests.get(
        video_url,
        headers=headers,
        proxies=config.proxy,
        verify=_get_tls_verify(),
        timeout=(60, 240),
    )
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()

    content_type = ""
    if hasattr(response, "headers"):
        content_type = response.headers.get("Content-Type", "").lower()
    if content_type.startswith(("text/", "application/json")):
        logger.error(
            f"downloaded response is not a video, url: {video_url}, content-type: {content_type}"
        )
        return ""

    content = getattr(response, "content", b"")
    if not content:
        logger.error(f"downloaded empty video response, url: {video_url}")
        return ""

    # if video does not exist, download it
    with open(video_path, "wb") as f:
        f.write(content)

    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        clip = None
        try:
            clip = VideoFileClip(video_path)
            duration = clip.duration
            fps = clip.fps
            if duration > 0 and fps > 0:
                return video_path
        except Exception as e:
            logger.warning(f"invalid video file: {video_path} => {str(e)}")
            try:
                os.remove(video_path)
            except Exception as remove_error:
                logger.warning(
                    f"failed to remove invalid video file: {video_path}, error: {str(remove_error)}"
                )
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception as close_error:
                    logger.warning(
                        f"failed to close video clip: {video_path}, error: {str(close_error)}"
                    )
    return ""


def _get_search_videos(source: str):
    if source == "pixabay":
        return search_videos_pixabay
    if source == "coverr":
        return search_videos_coverr
    return search_videos_pexels


def _normalize_online_sources(
    source: str = "pexels",
    sources: List[str] | str | None = None,
    material_locale: str = "auto",
) -> List[str]:
    if sources is None:
        raw_sources = [source]
    elif isinstance(sources, str):
        raw_sources = [item.strip() for item in sources.replace("，", ",").split(",")]
    else:
        raw_sources = list(sources)

    selected_sources = []
    for item in raw_sources:
        normalized = (item or "").strip().lower()
        if normalized == "local":
            continue
        if normalized in ONLINE_VIDEO_SOURCES and normalized not in selected_sources:
            selected_sources.append(normalized)

    if not selected_sources:
        fallback_source = (source or "pexels").strip().lower()
        selected_sources = [
            fallback_source if fallback_source in ONLINE_VIDEO_SOURCES else "pexels"
        ]

    priority = (
        CHINA_SOURCE_PRIORITY
        if material_locale == "china"
        else GLOBAL_SOURCE_PRIORITY
    )
    return sorted(
        selected_sources,
        key=lambda item: priority.index(item) if item in priority else len(priority),
    )


def _search_video_items(
    *,
    source: str,
    search_terms: List[str],
    video_aspect: VideoAspect,
    max_clip_duration: int,
    seen_urls: set[str] | None = None,
) -> tuple[List[MaterialInfo], float]:
    search_videos = _get_search_videos(source)
    if seen_urls is None:
        seen_urls = set()
    valid_video_items = []
    found_duration = 0.0
    for search_term in search_terms:
        video_items = search_videos(
            search_term=search_term,
            minimum_duration=max_clip_duration,
            video_aspect=video_aspect,
        )
        logger.info(f"found {len(video_items)} videos from {source} for '{search_term}'")

        for item in video_items:
            if item.url in seen_urls:
                continue
            valid_video_items.append(item)
            seen_urls.add(item.url)
            found_duration += item.duration
    return valid_video_items, found_duration


def _download_video_items(
    *,
    video_items: List[MaterialInfo],
    material_directory: str,
    max_clip_duration: int,
    audio_duration: float,
    existing_duration: float = 0.0,
) -> tuple[List[str], float]:
    video_paths = []
    total_duration = existing_duration
    for item in video_items:
        try:
            logger.info(f"downloading video: {item.url}")
            saved_video_path = save_video(
                video_url=item.url, save_dir=material_directory
            )
            if saved_video_path:
                logger.info(f"video saved: {saved_video_path}")
                video_paths.append(saved_video_path)
                total_duration += min(max_clip_duration, item.duration)
                if total_duration > audio_duration:
                    logger.info(
                        f"total duration of downloaded videos: {total_duration} seconds, skip downloading more"
                    )
                    break
        except Exception as e:
            logger.error(f"failed to download video: {utils.to_json(item)} => {str(e)}")
    return video_paths, total_duration


def _make_multi_source_searcher(sources: List[str]):
    def search_videos(search_term: str, minimum_duration: int, video_aspect: VideoAspect):
        merged_items = []
        seen_urls = set()
        for source in sources:
            source_items = _get_search_videos(source)(
                search_term=search_term,
                minimum_duration=minimum_duration,
                video_aspect=video_aspect,
            )
            logger.info(
                f"found {len(source_items)} videos from {source} for '{search_term}'"
            )
            for item in source_items:
                if item.url in seen_urls:
                    continue
                merged_items.append(item)
                seen_urls.add(item.url)
        return merged_items

    return search_videos


def download_videos(
    task_id: str,
    search_terms: List[str],
    source: str = "pexels",
    sources: List[str] | str | None = None,
    source_mode: str = "fallback",
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    audio_duration: float = 0.0,
    max_clip_duration: int = 5,
    match_script_order: bool = False,
    material_locale: str = "auto",
    avoid_people: bool = False,
) -> List[str]:
    material_directory = config.app.get("material_directory", "").strip()
    if material_directory == "task":
        material_directory = utils.task_dir(task_id)
    elif material_directory and not os.path.isdir(material_directory):
        material_directory = ""

    policy = material_policy.MaterialPolicy(
        material_locale=material_policy.normalize_material_locale(material_locale),
        people_filter="avoid" if avoid_people else "allow",
        avoid_people=avoid_people,
        is_chinese_content=False,
        is_china_context=material_locale == "china",
        reason="download_videos",
    )
    search_terms = material_policy.adapt_search_terms_for_policy(search_terms, policy)
    if avoid_people:
        logger.info(
            f"adapted material search terms for people-free stock footage: {search_terms}"
        )

    source_mode = source_mode if source_mode in {"fallback", "mixed"} else "fallback"
    source_names = _normalize_online_sources(
        source=source,
        sources=sources,
        material_locale=policy.material_locale,
    )
    logger.info(
        f"using online material sources: {source_names}, mode: {source_mode}"
    )

    if match_script_order:
        if source_mode == "fallback":
            video_paths = []
            total_downloaded_duration = 0.0
            for source_name in source_names:
                source_paths, total_downloaded_duration = _download_videos_by_script_order(
                    task_id=task_id,
                    search_terms=search_terms,
                    search_videos=_get_search_videos(source_name),
                    video_aspect=video_aspect,
                    audio_duration=audio_duration,
                    max_clip_duration=max_clip_duration,
                    material_directory=material_directory,
                    source_label=source_name,
                    existing_duration=total_downloaded_duration,
                )
                video_paths.extend(source_paths)
                if total_downloaded_duration > audio_duration:
                    return video_paths
            return video_paths

        video_paths, _ = _download_videos_by_script_order(
            task_id=task_id,
            search_terms=search_terms,
            search_videos=_make_multi_source_searcher(source_names),
            video_aspect=video_aspect,
            audio_duration=audio_duration,
            max_clip_duration=max_clip_duration,
            material_directory=material_directory,
            source_label="mixed",
        )
        return video_paths

    seen_urls = set()
    total_found_duration = 0.0
    total_downloaded_duration = 0.0
    video_paths = []

    if source_mode == "mixed":
        valid_video_items = []
        for source_name in source_names:
            source_items, found_duration = _search_video_items(
                source=source_name,
                search_terms=search_terms,
                video_aspect=video_aspect,
                max_clip_duration=max_clip_duration,
                seen_urls=seen_urls,
            )
            valid_video_items.extend(source_items)
            total_found_duration += found_duration
        logger.info(
            f"found total videos: {len(valid_video_items)}, required duration: {audio_duration} seconds, found duration: {total_found_duration} seconds"
        )
        concat_mode_value = getattr(video_concat_mode, "value", video_concat_mode)
        if concat_mode_value == VideoConcatMode.random.value:
            random.shuffle(valid_video_items)
        video_paths, total_downloaded_duration = _download_video_items(
            video_items=valid_video_items,
            material_directory=material_directory,
            max_clip_duration=max_clip_duration,
            audio_duration=audio_duration,
        )
    else:
        for source_name in source_names:
            source_items, found_duration = _search_video_items(
                source=source_name,
                search_terms=search_terms,
                video_aspect=video_aspect,
                max_clip_duration=max_clip_duration,
                seen_urls=seen_urls,
            )
            total_found_duration += found_duration
            logger.info(
                f"found total videos from {source_name}: {len(source_items)}, required duration: {audio_duration} seconds, found duration: {found_duration} seconds"
            )
            concat_mode_value = getattr(video_concat_mode, "value", video_concat_mode)
            if concat_mode_value == VideoConcatMode.random.value:
                random.shuffle(source_items)
            source_paths, total_downloaded_duration = _download_video_items(
                video_items=source_items,
                material_directory=material_directory,
                max_clip_duration=max_clip_duration,
                audio_duration=audio_duration,
                existing_duration=total_downloaded_duration,
            )
            video_paths.extend(source_paths)
            if total_downloaded_duration > audio_duration:
                break

    logger.success(f"downloaded {len(video_paths)} videos")
    return video_paths


def _download_videos_by_script_order(
    task_id: str,
    search_terms: List[str],
    search_videos,
    video_aspect: VideoAspect,
    audio_duration: float,
    max_clip_duration: int,
    material_directory: str,
    source_label: str = "",
    existing_duration: float = 0.0,
) -> tuple[List[str], float]:
    """
    按脚本文案顺序下载素材。

    默认下载逻辑会把所有关键词的候选素材合并成一个大列表；如果第一个
    关键词返回很多结果，最终下载时可能一直消耗这个关键词的素材，后续
    脚本主题就排不上时间线。这里按关键词分组后轮询下载：
    第 1 轮取每个关键词的第 1 个候选，第 2 轮取每个关键词的第 2 个候选。
    这样在不重写视频合成引擎的前提下，尽量保证素材顺序贴近文案顺序。
    """
    logger.info("downloading videos with script-order material matching")
    candidate_groups = []
    valid_video_urls = set()
    found_duration = 0.0

    for search_term in search_terms:
        video_items = search_videos(
            search_term=search_term,
            minimum_duration=max_clip_duration,
            video_aspect=video_aspect,
        )
        logger.info(
            f"found {len(video_items)} videos"
            f"{f' from {source_label}' if source_label else ''} for '{search_term}'"
        )

        term_items = []
        for item in video_items:
            if item.url in valid_video_urls:
                continue
            term_items.append(item)
            valid_video_urls.add(item.url)
            found_duration += item.duration

        if term_items:
            candidate_groups.append((search_term, term_items))

    logger.info(
        f"found total ordered video candidates: {sum(len(items) for _, items in candidate_groups)}, "
        f"required duration: {audio_duration} seconds, found duration: {found_duration} seconds"
    )

    video_paths = []
    total_duration = existing_duration
    candidate_index = 0
    while candidate_groups and total_duration <= audio_duration:
        has_candidate = False
        for search_term, term_items in candidate_groups:
            if candidate_index >= len(term_items):
                continue

            has_candidate = True
            item = term_items[candidate_index]
            try:
                logger.info(
                    f"downloading ordered video for '{search_term}': {item.url}"
                )
                saved_video_path = save_video(
                    video_url=item.url, save_dir=material_directory
                )
                if saved_video_path:
                    logger.info(f"video saved: {saved_video_path}")
                    video_paths.append(saved_video_path)
                    total_duration += min(max_clip_duration, item.duration)
                    if total_duration > audio_duration:
                        logger.info(
                            f"total duration of downloaded videos: {total_duration} seconds, skip downloading more"
                        )
                        break
            except Exception as e:
                logger.error(
                    f"failed to download ordered video: {utils.to_json(item)} => {str(e)}"
                )

        if not has_candidate:
            break
        candidate_index += 1

    logger.success(f"downloaded {len(video_paths)} ordered videos")
    return video_paths, total_duration


if __name__ == "__main__":
    download_videos(
        "test123", ["Money Exchange Medium"], audio_duration=100, source="pixabay"
    )
