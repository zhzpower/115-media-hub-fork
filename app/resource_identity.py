import re
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Set, Tuple


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_telegram_channel_id_from_input(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.replace("https://t.me/s/", "").replace("http://t.me/s/", "")
    raw = raw.replace("https://t.me/", "").replace("http://t.me/", "")
    raw = raw.replace("telegram.me/s/", "").replace("telegram.me/", "")
    raw = raw.lstrip("@").strip("/")
    return raw


def normalize_tg_proxy_url_prefix(value: Any) -> str:
    """规整反向代理前缀：去掉尾部斜杠；空字符串表示直连。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    # 只保留 http/https，避免 file:// 等意外前缀。
    lowered = raw.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raw = f"https://{raw}"
    return raw.rstrip("/")


def build_telegram_channel_url(channel_id: str, proxy_url_prefix: str = "") -> str:
    normalized = normalize_telegram_channel_id_from_input(channel_id)
    if not normalized:
        return ""
    base = f"https://t.me/s/{normalized}"
    prefix = normalize_tg_proxy_url_prefix(proxy_url_prefix)
    if not prefix:
        return base
    return f"{prefix}/{base}"


def build_telegram_channel_page_url(
    channel_id: str,
    before: str = "",
    query: str = "",
    proxy_url_prefix: str = "",
) -> str:
    base_url = build_telegram_channel_url(channel_id, proxy_url_prefix=proxy_url_prefix)
    cursor = str(before or "").strip()
    keyword = str(query or "").strip()
    if not base_url:
        return base_url
    params: List[Tuple[str, str]] = []
    if keyword:
        params.append(("q", keyword))
    if cursor:
        params.append(("before", cursor))
    if not params:
        return base_url
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def extract_telegram_post_cursor(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"/(\d+)$", raw)
    if match:
        return match.group(1)
    match = re.search(r"/(\d+)(?:\?.*)?$", raw)
    if match:
        return match.group(1)
    return raw


def resolve_resource_item_published_at(item: Dict[str, Any]) -> str:
    payload = item if isinstance(item, dict) else {}
    return str(payload.get("published_at", "") or payload.get("created_at", "")).strip()


def parse_resource_datetime_to_timestamp(value: str) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        try:
            return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            return 0.0


def build_resource_item_identity(item: Dict[str, Any]) -> str:
    payload = item if isinstance(item, dict) else {}
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    source_post_id = str(payload.get("source_post_id", "") or extra.get("source_post_id", "")).strip()
    if source_post_id:
        return f"post:{source_post_id}"
    message_url = str(payload.get("message_url", "")).strip()
    if message_url:
        return f"msg:{message_url}"
    link_url = str(payload.get("link_url", "")).strip()
    if link_url:
        return f"link:{link_url}"
    title = str(payload.get("title", "")).strip()
    raw_text = str(payload.get("raw_text", "")).strip()
    return f"title:{title}|raw:{raw_text[:120]}"


def normalize_resource_identity_mode(value: Any, fallback: str = "message") -> str:
    normalized_fallback = str(fallback or "message").strip().lower()
    normalized = str(value or "").strip().lower()
    if normalized in ("message", "link"):
        return normalized
    return "link" if normalized_fallback == "link" else "message"


def build_resource_item_identity_by_mode(item: Dict[str, Any], identity_mode: str = "message") -> str:
    payload = item if isinstance(item, dict) else {}
    normalized_identity_mode = normalize_resource_identity_mode(identity_mode, fallback="message")
    if normalized_identity_mode == "link":
        link_url = str(payload.get("link_url", "")).strip()
        if link_url:
            return f"link:{link_url}"
    return build_resource_item_identity(payload)


def dedupe_resource_item_dicts(items: List[Dict[str, Any]], identity_mode: str = "message") -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in items:
        key = build_resource_item_identity_by_mode(item, identity_mode=identity_mode)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def build_resource_search_text(item: Dict[str, Any]) -> str:
    payload = item if isinstance(item, dict) else {}
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    parts = [
        payload.get("title", ""),
        payload.get("normalized_title", ""),
        payload.get("raw_text", ""),
        payload.get("source_name", ""),
        payload.get("channel_name", ""),
        payload.get("link_url", ""),
        payload.get("message_url", ""),
        extra.get("source_post_id", ""),
    ]
    return " ".join(str(part or "").strip().lower() for part in parts if str(part or "").strip())


def tokenize_resource_search_keyword(keyword: str) -> List[str]:
    return [token for token in re.split(r"\s+", str(keyword or "").strip().lower()) if token]


def _field_matches_tokens(text: str, tokens: List[str]) -> bool:
    haystack = str(text or "").strip().lower()
    return bool(tokens) and all(token in haystack for token in tokens)


def _first_non_empty_line(text: str) -> str:
    for line in str(text or "").splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized:
            return normalized
    return ""


def _build_resource_match_snippet(text: str, tokens: List[str], radius: int = 36) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    positions = [lowered.find(token) for token in tokens if token and lowered.find(token) >= 0]
    if not positions:
        return normalized[: radius * 2].strip()
    start = max(0, min(positions) - radius)
    end = min(len(normalized), min(positions) + max(len(token) for token in tokens) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(normalized) else ""
    return f"{prefix}{normalized[start:end].strip()}{suffix}"


def build_resource_search_match_info(item: Dict[str, Any], keyword: str) -> Dict[str, Any]:
    payload = item if isinstance(item, dict) else {}
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    tokens = tokenize_resource_search_keyword(keyword)
    if not tokens:
        return {"matched": True, "rank": 0, "field": "", "field_label": "", "snippet": ""}

    title = str(payload.get("title", "") or "").strip()
    raw_text = str(payload.get("raw_text", "") or "").strip()
    fields: List[Tuple[int, str, str, str]] = [
        (0, "title", "标题", title),
        (1, "normalized_title", "标题", str(payload.get("normalized_title", "") or "").strip()),
        (2, "raw_first_line", "正文", _first_non_empty_line(raw_text)),
        (3, "raw_text", "正文", raw_text),
        (4, "source_name", "来源", str(payload.get("source_name", "") or "").strip()),
        (4, "channel_name", "频道", str(payload.get("channel_name", "") or "").strip()),
        (5, "link_url", "链接", str(payload.get("link_url", "") or "").strip()),
        (5, "message_url", "消息", str(payload.get("message_url", "") or "").strip()),
        (5, "source_post_id", "消息", str(payload.get("source_post_id", "") or extra.get("source_post_id", "")).strip()),
    ]
    seen_values: Set[str] = set()
    for rank, field, label, value in fields:
        normalized_value = str(value or "").strip()
        if not normalized_value:
            continue
        normalized_key = normalized_value.lower()
        if normalized_key in seen_values:
            continue
        seen_values.add(normalized_key)
        if _field_matches_tokens(normalized_value, tokens):
            return {
                "matched": True,
                "rank": rank,
                "field": field,
                "field_label": label,
                "snippet": _build_resource_match_snippet(normalized_value, tokens),
            }
    return {"matched": False, "rank": 99, "field": "", "field_label": "", "snippet": ""}


def get_resource_search_rank(item: Dict[str, Any], keyword: str) -> int:
    match_info = item.get("search_match") if isinstance(item, dict) else {}
    if isinstance(match_info, dict) and match_info.get("matched"):
        return _parse_int(match_info.get("rank"), default=99)
    return _parse_int(build_resource_search_match_info(item, keyword).get("rank"), default=99)


def sort_resource_search_items(items: List[Dict[str, Any]], keyword: str) -> List[Dict[str, Any]]:
    result = list(items or [])
    result.sort(key=get_resource_item_sort_key, reverse=True)
    result.sort(key=lambda item: get_resource_search_rank(item, keyword))
    return result


def resource_item_matches_search(item: Dict[str, Any], keyword: str) -> bool:
    tokens = tokenize_resource_search_keyword(keyword)
    if not tokens:
        return True
    return bool(build_resource_search_match_info(item, keyword).get("matched")) or all(
        token in build_resource_search_text(item) for token in tokens
    )


def get_resource_item_sort_key(item: Dict[str, Any]) -> Tuple[str, int, str]:
    payload = item if isinstance(item, dict) else {}
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    published_at = str(payload.get("published_at", "") or payload.get("created_at", "")).strip()
    cursor = _parse_int(
        extract_telegram_post_cursor(
            str(payload.get("message_url", "")).strip()
            or str(payload.get("source_post_id", "") or extra.get("source_post_id", "")).strip()
        )
    )
    return (published_at, cursor, build_resource_item_identity(payload))


def get_resource_item_post_cursor(item: Dict[str, Any]) -> str:
    payload = item if isinstance(item, dict) else {}
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    return extract_telegram_post_cursor(
        str(payload.get("message_url", "")).strip()
        or str(payload.get("source_post_id", "") or extra.get("source_post_id", "")).strip()
    )


__all__ = [
    "normalize_telegram_channel_id_from_input",
    "normalize_tg_proxy_url_prefix",
    "build_telegram_channel_url",
    "build_telegram_channel_page_url",
    "extract_telegram_post_cursor",
    "resolve_resource_item_published_at",
    "parse_resource_datetime_to_timestamp",
    "build_resource_item_identity",
    "normalize_resource_identity_mode",
    "build_resource_item_identity_by_mode",
    "dedupe_resource_item_dicts",
    "build_resource_search_text",
    "tokenize_resource_search_keyword",
    "build_resource_search_match_info",
    "get_resource_search_rank",
    "sort_resource_search_items",
    "resource_item_matches_search",
    "get_resource_item_sort_key",
    "get_resource_item_post_cursor",
]
