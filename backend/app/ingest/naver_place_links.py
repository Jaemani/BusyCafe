"""Pure, strict matching of canonical cafes to Naver local-search results."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from app.schemas import NaverLocalResponse


_TITLE_BOLD_TAG = re.compile(r"</?b>", re.IGNORECASE)
_NAVER_MAP_DETAIL_PATH = re.compile(r"/p/entry/place/([0-9]+)")
_NAVER_MOBILE_DETAIL_PATH = re.compile(
    r"/(place|restaurant|cafe)/([0-9]+)(?:/(?:home)?)?"
)


@dataclass(frozen=True, slots=True)
class NaverPlaceMatch:
    provider_place_id: str
    detail_url: str


@dataclass(frozen=True, slots=True)
class NaverMatchResult:
    status: str
    match: NaverPlaceMatch | None
    exact_candidate_count: int


def normalize_exact(value: str) -> str:
    """Normalize representation only; no spelling or token substitutions."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def normalize_naver_title(value: str) -> str:
    return html.unescape(_TITLE_BOLD_TAG.sub("", value))


def canonical_naver_place_link(value: str) -> NaverPlaceMatch | None:
    """Accept only direct, numeric Naver Place URLs; reject searches/short URLs."""

    try:
        parsed = urlparse(value)
        has_authority_override = bool(
            parsed.username or parsed.password or parsed.port
        )
    except ValueError:
        return None
    if parsed.scheme != "https" or has_authority_override:
        return None
    place_id: str | None = None
    if parsed.hostname in {"map.naver.com", "m.map.naver.com"}:
        matched = _NAVER_MAP_DETAIL_PATH.fullmatch(parsed.path.rstrip("/"))
        if matched is not None:
            place_id = matched.group(1)
    elif parsed.hostname == "m.place.naver.com":
        matched = _NAVER_MOBILE_DETAIL_PATH.fullmatch(parsed.path.rstrip("/"))
        if matched is not None:
            place_id = matched.group(2)
    if place_id is None:
        return None
    return NaverPlaceMatch(
        provider_place_id=place_id,
        detail_url=f"https://map.naver.com/p/entry/place/{place_id}",
    )


def match_naver_place(
    *,
    cafe_name: str,
    cafe_road_address: str | None,
    response: NaverLocalResponse,
) -> NaverMatchResult:
    """Match one result by direct ID URL plus exact normalized name and road address."""

    if not cafe_road_address:
        return NaverMatchResult("missing_road_address", None, 0)
    expected_name = normalize_exact(cafe_name)
    expected_address = normalize_exact(cafe_road_address)
    if not expected_name or not expected_address:
        return NaverMatchResult("invalid_canonical_fields", None, 0)

    matches: dict[str, NaverPlaceMatch] = {}
    for item in response.items:
        direct = canonical_naver_place_link(item.link)
        if direct is None:
            continue
        if normalize_exact(normalize_naver_title(item.title)) != expected_name:
            continue
        if normalize_exact(item.road_address) != expected_address:
            continue
        matches[direct.provider_place_id] = direct
    if not matches:
        return NaverMatchResult("unmatched", None, 0)
    if len(matches) != 1:
        return NaverMatchResult("ambiguous", None, len(matches))
    return NaverMatchResult("matched", next(iter(matches.values())), 1)


def build_naver_query(name: str, road_address: str) -> str:
    return " ".join((name.strip(), road_address.strip()))
