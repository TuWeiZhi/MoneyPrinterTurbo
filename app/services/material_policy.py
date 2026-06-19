import re
from dataclasses import dataclass
from typing import Iterable, Sequence


MATERIAL_LOCALES = {"auto", "global", "china"}
MATERIAL_PEOPLE_FILTERS = {"auto", "avoid", "allow"}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TEXT_RE = re.compile(r"[\w\u4e00-\u9fff]", re.UNICODE)

_CHINESE_LANGUAGE_MARKERS = (
    "zh",
    "cn",
    "chinese",
    "\u4e2d\u6587",
    "\u6c49\u8bed",
    "\u6f22\u8a9e",
)

_CHINA_CONTEXT_KEYWORDS = (
    "\u4e0a\u6d77",
    "\u5317\u4eac",
    "\u5e7f\u5dde",
    "\u5ee3\u5dde",
    "\u6df1\u5733",
    "\u676d\u5dde",
    "\u6210\u90fd",
    "\u91cd\u5e86",
    "\u91cd\u6176",
    "\u6b66\u6c49",
    "\u897f\u5b89",
    "\u5357\u4eac",
    "\u5929\u6d25",
    "\u82cf\u5dde",
    "\u8607\u5dde",
    "\u4e2d\u56fd",
    "\u4e2d\u570b",
    "\u56fd\u5185",
    "\u570b\u5167",
    "\u5730\u94c1",
    "\u5730\u9435",
    "\u9ad8\u94c1",
    "\u9ad8\u9435",
    "\u5916\u5356",
    "\u5916\u8ce3",
    "\u5c0f\u533a",
    "\u5c0f\u5340",
    "\u83dc\u5e02\u573a",
    "\u83dc\u5e02\u5834",
    "\u5976\u8336",
    "\u706b\u9505",
    "\u706b\u934b",
    "\u62d6\u97f3",
    "\u5c0f\u7ea2\u4e66",
    "\u5c0f\u7d05\u66f8",
    "\u5fae\u4fe1",
    "\u6dd8\u5b9d",
    "\u6dd8\u5bf6",
    "\u76f4\u64ad\u5e26\u8d27",
    "\u76f4\u64ad\u5e36\u8ca8",
    "\u9ad8\u8003",
    "\u8003\u7814",
    "\u516c\u52a1\u5458",
    "\u516c\u52d9\u54e1",
    "\u793e\u4fdd",
    "\u516c\u79ef\u91d1",
    "\u516c\u7a4d\u91d1",
    "\u6253\u5de5\u4eba",
    "\u5b9d\u5988",
    "\u5bf6\u5abd",
    "\u79c1\u57df",
)

_PERSON_WORDS = (
    "person",
    "people",
    "man",
    "woman",
    "men",
    "women",
    "family",
    "child",
    "children",
    "kid",
    "kids",
    "face",
    "portrait",
    "smiling",
    "businessman",
    "businesswoman",
    "businessperson",
    "worker",
    "employee",
    "student",
    "customer",
    "couple",
    "crowd",
    "model",
)

_VISUAL_FALLBACKS = (
    ("office", "office desk"),
    ("business", "office desk"),
    ("work", "hands working"),
    ("food", "food close up"),
    ("restaurant", "restaurant table"),
    ("city", "city street"),
    ("street", "empty street"),
    ("shopping", "store shelves"),
    ("education", "classroom desk"),
    ("school", "classroom desk"),
    ("technology", "computer screen"),
    ("phone", "phone close up"),
    ("health", "medical equipment"),
    ("travel", "city skyline"),
)


@dataclass(frozen=True)
class MaterialPolicy:
    material_locale: str
    people_filter: str
    avoid_people: bool
    is_chinese_content: bool
    is_china_context: bool
    reason: str


def normalize_material_locale(value: str | None) -> str:
    normalized = (value or "auto").strip().lower()
    return normalized if normalized in MATERIAL_LOCALES else "auto"


def normalize_people_filter(value: str | None) -> str:
    normalized = (value or "auto").strip().lower()
    return normalized if normalized in MATERIAL_PEOPLE_FILTERS else "auto"


def is_chinese_language(language: str | None) -> bool:
    normalized = (language or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _CHINESE_LANGUAGE_MARKERS)


def chinese_char_ratio(text: str | None) -> float:
    if not text:
        return 0.0
    text_units = _TEXT_RE.findall(text)
    if not text_units:
        return 0.0
    cjk_units = _CJK_RE.findall(text)
    return len(cjk_units) / len(text_units)


def has_chinese_text(text: str | None) -> bool:
    if not text:
        return False
    return len(_CJK_RE.findall(text)) >= 4 or chinese_char_ratio(text) >= 0.15


def has_china_context(text: str | None) -> bool:
    normalized = (text or "").lower()
    return any(keyword.lower() in normalized for keyword in _CHINA_CONTEXT_KEYWORDS)


def resolve_material_policy(
    *,
    video_language: str | None = "",
    video_subject: str | None = "",
    video_script: str | None = "",
    material_locale: str | None = "auto",
    people_filter: str | None = "auto",
) -> MaterialPolicy:
    locale = normalize_material_locale(material_locale)
    people = normalize_people_filter(people_filter)
    combined_text = "\n".join(part for part in (video_subject, video_script) if part)

    language_is_chinese = is_chinese_language(video_language)
    text_is_chinese = has_chinese_text(combined_text)
    keyword_is_china_context = has_china_context(combined_text)
    is_chinese_content = language_is_chinese or text_is_chinese
    is_china_context = locale == "china" or keyword_is_china_context

    if locale == "global":
        is_china_context = False

    if people == "avoid":
        avoid_people = True
        reason = "explicit_people_filter"
    elif people == "allow":
        avoid_people = False
        reason = "explicit_people_filter"
    elif locale == "global":
        avoid_people = False
        reason = "global_locale"
    elif locale == "china":
        avoid_people = True
        reason = "china_locale"
    elif is_chinese_content:
        avoid_people = True
        reason = "chinese_content_auto"
    else:
        avoid_people = False
        reason = "default_global_auto"

    return MaterialPolicy(
        material_locale=locale,
        people_filter=people,
        avoid_people=avoid_people,
        is_chinese_content=is_chinese_content,
        is_china_context=is_china_context,
        reason=reason,
    )


def _contains_person_word(term: str) -> bool:
    normalized = f" {term.lower()} "
    return any(re.search(rf"\b{re.escape(word)}\b", normalized) for word in _PERSON_WORDS)


def _visual_fallback_for(term: str) -> str:
    normalized = term.lower()
    for marker, fallback in _VISUAL_FALLBACKS:
        if marker in normalized:
            return fallback
    return "environment detail"


def adapt_search_terms_for_policy(
    search_terms: Sequence[str] | str | None,
    policy: MaterialPolicy,
) -> list[str]:
    if not search_terms:
        return []
    if isinstance(search_terms, str):
        raw_terms: Iterable[str] = re.split(r"[,，]", search_terms)
    else:
        raw_terms = search_terms

    adapted_terms = []
    seen_terms = set()
    for raw_term in raw_terms:
        term = str(raw_term).strip()
        if not term:
            continue
        if policy.avoid_people and _contains_person_word(term):
            term = _visual_fallback_for(term)
        if policy.avoid_people and "no people" not in term.lower():
            term = f"{term} no people"
        normalized = term.lower()
        if normalized in seen_terms:
            continue
        adapted_terms.append(term)
        seen_terms.add(normalized)
    return adapted_terms
