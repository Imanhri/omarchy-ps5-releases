#!/usr/bin/env python3
"""Fetch upcoming PS5 releases and cache them for six hours.

Credentials are read only in this process. The UI receives game data over
stdout; no credential values or request details are ever written to output.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import sys
import tempfile
import time as time_module
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests


CONFIG = Path.home() / ".config/ps5-releases/credentials"
CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
CACHE_DIR = CACHE_ROOT / "ps5-releases"
CACHE_FILE = CACHE_DIR / "releases.json"
LOCK_FILE = CACHE_DIR / "refresh.lock"
COVER_CACHE_DIR = Path.home() / ".cache/ps5-releases/covers"
CACHE_VERSION = 6
CACHE_TTL_SECONDS = 6 * 60 * 60
MAX_RESPONSE_BYTES = 1_000_000
MAX_GAMES = 10
MAX_API_LIMIT = 500
PS5_PLATFORM_ID = 167
MAX_MONTH_RELEASE_RECORDS = 5_000
MAX_UPCOMING_RELEASE_RECORDS = 20_000
MAX_GAME_HISTORY_RECORDS = 50_000
GAME_ID_BATCH_SIZE = 80
MIN_MONTH_RATING_COUNT = 5
MAX_NAME_LENGTH = 160
MAX_COVER_IMAGE_BYTES = 512_000
MAX_COVER_CACHE_BYTES = 32 * 1024 * 1024
MAX_CACHED_COVERS = 300
MAX_COVER_IMAGE_ID_LENGTH = 128
RELEASE_WINDOW_DAYS = 180
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_KEY_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
COVER_IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
GAME_DETAIL_PATH_PATTERN = re.compile(r"^/games/[a-z0-9][a-z0-9-]{0,199}/?$", re.IGNORECASE)
GAME_URL_HOSTS = {"igdb.com", "www.igdb.com"}
JPEG_SIGNATURE = b"\xff\xd8\xff"
BERLIN_TZ = ZoneInfo("Europe/Berlin")
AVAILABLE_RELEASE_STATUSES = {"full release", "early access", "advanced access"}
ALLOWED_GAME_TYPE_NAMES = {
    "main game",
    "standalone expansion",
    "remake",
    "remaster",
    "port",
}
INVALID_DATE_REASONS = {
    "missing_date_precision",
    "approximate_date_precision",
    "missing_release_date",
    "invalid_release_date",
}
INVALID_PLATFORM_REASONS = {
    "missing_release_platform",
    "release_platform_mismatch",
    "missing_game_platforms",
    "incomplete_game_platforms",
    "game_platforms_missing_ps5",
}
_last_igdb_request_at = None


class FetchError(Exception):
    """A deliberately detail-free error safe to reduce to a UI error state."""


class CredentialsError(FetchError):
    """Missing or unreadable IGDB credentials, safe to identify to the UI."""


def _json_response(response: requests.Response):
    if not response.ok:
        raise FetchError

    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                raise FetchError
        except ValueError:
            raise FetchError from None

    body = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise FetchError

    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise FetchError from None


def _post_json(url, *, headers=None, data=None):
    with requests.post(
        url,
        headers=headers,
        data=data,
        timeout=(5, 20),
        stream=True,
    ) as response:
        return _json_response(response)


def load_credentials():
    values = {}
    try:
        lines = CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise CredentialsError from None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")

    client_id = values.get("IGDB_CLIENT_ID", "")
    client_secret = values.get("IGDB_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise CredentialsError
    return client_id, client_secret


def get_token(client_id, client_secret):
    payload = _post_json(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise FetchError
    return token


def igdb_request(endpoint, query, headers):
    global _last_igdb_request_at
    now = time_module.monotonic()
    if _last_igdb_request_at is not None:
        pause = 0.26 - (now - _last_igdb_request_at)
        if pause > 0:
            time_module.sleep(pause)
    _last_igdb_request_at = time_module.monotonic()
    return _post_json(
        f"https://api.igdb.com/v4/{endpoint}",
        headers=headers,
        data=query,
    )


def _safe_name(value):
    if not isinstance(value, str):
        return ""
    name = "".join(character for character in value.strip() if character.isprintable())
    return name[:MAX_NAME_LENGTH].strip()


def _cover_image_id(value):
    """Extract a conservative IGDB image id from a cover reference."""
    if isinstance(value, dict):
        value = value.get("image_id")
    if not isinstance(value, str) or len(value) > MAX_COVER_IMAGE_ID_LENGTH:
        return ""
    return value if COVER_IMAGE_ID_PATTERN.fullmatch(value) else ""


def _cover_url(value):
    image_id = _cover_image_id(value)
    if not image_id:
        return ""
    return f"https://images.igdb.com/igdb/image/upload/t_cover_small/{image_id}.jpg"


def _validated_game_url(value):
    """Accept only canonical IGDB game-detail URLs from the games.url field."""
    if (not isinstance(value, str) or not value or len(value) > 2048 or not value.isascii()
            or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if (parsed.scheme.casefold() != "https"
            or parsed.netloc.casefold() not in GAME_URL_HOSTS
            or parsed.query or parsed.fragment
            or not GAME_DETAIL_PATH_PATTERN.fullmatch(parsed.path)):
        return ""
    return value


def _cover_id_from_url(value):
    if not isinstance(value, str):
        return ""
    match = re.fullmatch(
        r"https://images\.igdb\.com/igdb/image/upload/t_cover_small/([A-Za-z0-9_-]{1,128})\.jpg",
        value,
    )
    return match.group(1) if match else ""


def _cached_cover_uri(value):
    image_id = _cover_id_from_url(value)
    if not image_id:
        return ""
    path = COVER_CACHE_DIR / f"{image_id}.jpg"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_COVER_IMAGE_BYTES:
            return ""
        with path.open("rb") as handle:
            if handle.read(len(JPEG_SIGNATURE)) != JPEG_SIGNATURE:
                return ""
        return path.as_uri()
    except OSError:
        return ""


def _download_cover(image_id):
    """Download one small cover from IGDB's fixed image host, without redirects."""
    if not COVER_IMAGE_ID_PATTERN.fullmatch(image_id):
        return None
    path = COVER_CACHE_DIR / f"{image_id}.jpg"
    try:
        COVER_CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(COVER_CACHE_DIR, 0o700)
        with requests.get(
            f"https://images.igdb.com/igdb/image/upload/t_cover_small/{image_id}.jpg",
            timeout=(2, 3),
            stream=True,
            allow_redirects=False,
        ) as response:
            if not response.ok or response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "image/jpeg":
                return None
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_COVER_IMAGE_BYTES:
                        return None
                except ValueError:
                    return None
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16_384):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > MAX_COVER_IMAGE_BYTES:
                    return None
        if not body.startswith(JPEG_SIGNATURE):
            return None
        descriptor, temporary_path = tempfile.mkstemp(prefix=".cover-", dir=COVER_CACHE_DIR)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
        return path
    except (OSError, requests.RequestException):
        return None


def _trim_cover_cache():
    """Keep the local cover cache bounded by count and total size."""
    try:
        files = [
            path for path in COVER_CACHE_DIR.glob("*.jpg")
            if not path.is_symlink() and path.is_file()
        ]
        files.sort(key=lambda path: path.stat().st_mtime)
        total = sum(path.stat().st_size for path in files)
        while files and (len(files) > MAX_CACHED_COVERS or total > MAX_COVER_CACHE_BYTES):
            oldest = files.pop(0)
            size = oldest.stat().st_size
            oldest.unlink()
            total -= size
    except OSError:
        return


def _cache_cover_images(games):
    """Ensure covers for the visible rows exist locally; misses remain CDN-backed."""
    if not isinstance(games, list):
        return games
    ids = sorted({
        image_id for game in games if isinstance(game, dict)
        for image_id in [_cover_id_from_url(game.get("cover_url"))]
        if image_id
    })
    missing = []
    for image_id in ids:
        path = COVER_CACHE_DIR / f"{image_id}.jpg"
        try:
            if not _cached_cover_uri(_cover_url(image_id)):
                missing.append(image_id)
            else:
                path.touch()
        except OSError:
            missing.append(image_id)
    if missing:
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(_download_cover, missing))
        except Exception:
            # Image caching is optional; the UI can still load the official URL.
            pass
    _trim_cover_cache()
    for game in games:
        if isinstance(game, dict):
            game["cover_cache_url"] = _cached_cover_uri(game.get("cover_url"))
    return games


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_game_type(value):
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _platform_debug(debug_platforms, row, reason, normalized_date=None):
    if not debug_platforms:
        return

    game = row.get("game") if isinstance(row, dict) else None
    game = game if isinstance(game, dict) else {}
    game_id = game.get("id")
    if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id <= 0:
        game_id = None
    name = _safe_name(game.get("name")) or "<unknown>"
    game_platforms = game.get("platforms")
    if isinstance(game_platforms, list):
        game_platform_ids = [
            value for value in game_platforms
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        ]
    else:
        game_platform_ids = None
    release_platform_id = row.get("platform") if isinstance(row, dict) else None
    if (isinstance(release_platform_id, bool) or not isinstance(release_platform_id, int)
            or release_platform_id <= 0):
        release_platform_id = None
    date_format = row.get("date_format") if isinstance(row, dict) else None
    if isinstance(date_format, bool) or not isinstance(date_format, int):
        date_format = None
    release_region_id = row.get("release_region") if isinstance(row, dict) else None
    if isinstance(release_region_id, bool) or not isinstance(release_region_id, int):
        release_region_id = None
    status_id = row.get("status") if isinstance(row, dict) else None
    if isinstance(status_id, bool) or not isinstance(status_id, int):
        status_id = None
    record_id = row.get("id") if isinstance(row, dict) else None
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id <= 0:
        record_id = None
    raw_timestamp = _finite_number(row.get("date")) if isinstance(row, dict) else None
    raw_date_utc = None
    if raw_timestamp is not None:
        try:
            raw_date_utc = datetime.fromtimestamp(raw_timestamp, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    diagnostic = {
        "event": "ps5_release_diagnostic",
        "release_date_id": record_id,
        "game_id": game_id,
        "name": name,
        "reason": reason,
        "release_platform_id": release_platform_id,
        "game_platform_ids": game_platform_ids,
        "release_date_utc": raw_date_utc,
        "date_format": date_format,
        "release_region_id": release_region_id,
        "status_id": status_id,
        "released_berlin": normalized_date.isoformat() if normalized_date else None,
    }
    print(json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)


def _selection_debug(debug_platforms, game_id, records, reason):
    if not debug_platforms:
        return
    safe_records = [
        {
            "release_date_id": record.get("release_record_id"),
            "release_platform_id": record["release_platform_id"],
            "game_platform_ids": record["game_platforms"],
            "date_format": record["date_format"],
            "release_region_id": record["release_region_id"],
            "release_region": record["release_region"],
            "status_id": record["release_status_id"],
            "status": record["release_status"],
            "released_berlin": record["released"].isoformat(),
        }
        for record in sorted(
            records,
            key=lambda item: (item["released"], item["release_region"], item["id"]),
        )
    ]
    name = records[0]["name"] if records else "<unknown>"
    print(json.dumps({
        "event": "ps5_game_release_selection",
        "game_id": game_id,
        "name": name,
        "decision": reason,
        "release_records": safe_records,
    }, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)


def _normalize_ps5_release(row, ps5_id, regions, statuses, debug_platforms=False):
    """Validate one PS5 release record and normalize its date to Berlin."""
    game = row.get("game") if isinstance(row, dict) else None
    if not isinstance(game, dict):
        _platform_debug(debug_platforms, row, "missing_game")
        return None, "missing_game"

    game_id = game.get("id")
    name = _safe_name(game.get("name"))
    if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id <= 0 or not name:
        _platform_debug(debug_platforms, row, "missing_game_identity")
        return None, "missing_game_identity"

    release_platform_id = row.get("platform")
    if isinstance(release_platform_id, bool) or not isinstance(release_platform_id, int):
        _platform_debug(debug_platforms, row, "missing_release_platform")
        return None, "missing_release_platform"
    if release_platform_id != ps5_id:
        _platform_debug(debug_platforms, row, "release_platform_mismatch")
        return None, "release_platform_mismatch"

    game_platforms = game.get("platforms")
    if not isinstance(game_platforms, list) or not game_platforms:
        _platform_debug(debug_platforms, row, "missing_game_platforms")
        return None, "missing_game_platforms"
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in game_platforms):
        _platform_debug(debug_platforms, row, "incomplete_game_platforms")
        return None, "incomplete_game_platforms"
    if ps5_id not in game_platforms:
        _platform_debug(debug_platforms, row, "game_platforms_missing_ps5")
        return None, "game_platforms_missing_ps5"

    date_format = row.get("date_format")
    if isinstance(date_format, bool) or not isinstance(date_format, int):
        _platform_debug(debug_platforms, row, "missing_date_precision")
        return None, "missing_date_precision"
    if date_format != 0:  # IGDB's YYYYMMDD precision.
        _platform_debug(debug_platforms, row, "approximate_date_precision")
        return None, "approximate_date_precision"

    timestamp = _finite_number(row.get("date"))
    if timestamp is None:
        _platform_debug(debug_platforms, row, "missing_release_date")
        return None, "missing_release_date"
    try:
        released = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(BERLIN_TZ).date()
    except (OverflowError, OSError, ValueError):
        _platform_debug(debug_platforms, row, "invalid_release_date")
        return None, "invalid_release_date"

    region_id = row.get("release_region")
    if isinstance(region_id, bool) or not isinstance(region_id, int):
        _platform_debug(debug_platforms, row, "missing_release_region", released)
        return None, "missing_release_region"
    region_name = regions.get(region_id)
    if not isinstance(region_name, str) or not region_name.strip():
        _platform_debug(debug_platforms, row, "unknown_release_region", released)
        return None, "unknown_release_region"
    region_name = region_name.strip().casefold()

    status_id = row.get("status")
    if status_id is None:
        status_name = None
    elif isinstance(status_id, bool) or not isinstance(status_id, int):
        _platform_debug(debug_platforms, row, "invalid_release_status", released)
        return None, "invalid_release_status"
    else:
        status_name = statuses.get(status_id)
        if not isinstance(status_name, str):
            _platform_debug(debug_platforms, row, "unknown_release_status", released)
            return None, "unknown_release_status"
        status_name = status_name.strip().casefold()
        if status_name not in AVAILABLE_RELEASE_STATUSES:
            reason = "non_release_status:" + (status_name or "unknown")
            _platform_debug(debug_platforms, row, reason, released)
            return None, reason

    rating = _finite_number(game.get("total_rating"))
    if rating is not None and (rating < 0 or rating > 100):
        rating = None
    rating_count = game.get("total_rating_count")
    if isinstance(rating_count, bool) or not isinstance(rating_count, int):
        rating_count = 0

    return {
        "id": game_id,
        "release_record_id": row.get("id") if isinstance(row.get("id"), int) and not isinstance(row.get("id"), bool) else None,
        "name": name,
        "game_type_id": game.get("game_type"),
        "parent_game_id": game.get("parent_game"),
        "version_parent_id": game.get("version_parent"),
        "released": released,
        "platform_id": ps5_id,
        "release_platform_id": release_platform_id,
        "game_platforms": list(game_platforms),
        "date_format": date_format,
        "release_region_id": region_id,
        "release_region": region_name,
        "release_status_id": status_id,
        "release_status": status_name,
        "rating": rating,
        "rating_count": rating_count,
        "cover_url": _cover_url(game.get("cover")),
        "game_url": _validated_game_url(game.get("url")),
    }, None


def _valid_games(value):
    if not isinstance(value, list) or len(value) > MAX_GAMES:
        raise FetchError

    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _safe_name(item.get("name"))
        released = item.get("released")
        days_left = item.get("days_left")
        game_id = item.get("id")
        platform_id = item.get("platform_id")
        release_platform_id = item.get("release_platform_id")
        game_platforms = item.get("game_platforms")
        date_format = item.get("date_format")
        release_region_id = item.get("release_region_id")
        release_region = item.get("release_region")
        release_status_id = item.get("release_status_id")
        release_status = item.get("release_status")
        raw_cover_url = item.get("cover_url")
        cover_url = raw_cover_url if _cover_id_from_url(raw_cover_url) else ""
        game_url = _validated_game_url(item.get("game_url"))
        if not name or not isinstance(released, str) or not DATE_PATTERN.fullmatch(released):
            continue
        try:
            date.fromisoformat(released)
        except ValueError:
            continue
        if isinstance(days_left, bool) or not isinstance(days_left, int):
            continue
        if days_left < 0 or days_left > RELEASE_WINDOW_DAYS:
            continue
        if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id <= 0:
            continue
        if (isinstance(platform_id, bool) or not isinstance(platform_id, int)
                or platform_id <= 0 or isinstance(release_platform_id, bool)
                or release_platform_id != platform_id):
            continue
        if (not isinstance(release_platform_id, int) or not isinstance(game_platforms, list)
                or not game_platforms):
            continue
        if any(isinstance(platform, bool) or not isinstance(platform, int) or platform <= 0
                for platform in game_platforms):
            continue
        if platform_id not in game_platforms:
            continue
        if isinstance(date_format, bool) or date_format != 0:
            continue
        if (isinstance(release_region_id, bool) or not isinstance(release_region_id, int)
                or release_region_id <= 0 or not isinstance(release_region, str) or not release_region):
            continue
        if (release_status_id is not None
                and (isinstance(release_status_id, bool) or not isinstance(release_status_id, int))):
            continue
        if release_status is not None and not isinstance(release_status, str):
            continue
        result.append(
            {
                "id": game_id,
                "name": name,
                "released": released,
                "days_left": days_left,
                "platform_id": platform_id,
                "release_platform_id": release_platform_id,
                "game_platforms": game_platforms,
                "date_format": date_format,
                "release_region_id": release_region_id,
                "release_region": release_region,
                "release_status_id": release_status_id,
                "release_status": release_status,
                "cover_url": cover_url,
                "cover_cache_url": _cached_cover_uri(cover_url),
                "game_url": game_url,
            }
        )

    result.sort(key=lambda game: (game["released"], game["name"].casefold(), game["id"]))
    return result


def _ps5_platform_id(headers):
    platforms = igdb_request(
        "platforms",
        'fields id,name,slug; where slug = "ps5"; limit 2;',
        headers,
    )
    if not isinstance(platforms, list) or len(platforms) != 1:
        raise FetchError
    platform = platforms[0]
    if not isinstance(platform, dict) or platform.get("slug") != "ps5":
        raise FetchError
    if _safe_name(platform.get("name")).casefold() != "playstation 5":
        raise FetchError
    platform_id = platform.get("id")
    if isinstance(platform_id, bool) or not isinstance(platform_id, int) or platform_id <= 0:
        raise FetchError
    if platform_id != PS5_PLATFORM_ID:
        raise FetchError
    return platform_id


def _release_metadata(headers):
    region_rows = igdb_request("release_date_regions", "fields id,region; limit 500;", headers)
    status_rows = igdb_request("release_date_statuses", "fields id,name; limit 500;", headers)
    if (not isinstance(region_rows, list) or len(region_rows) > MAX_API_LIMIT
            or not isinstance(status_rows, list) or len(status_rows) > MAX_API_LIMIT):
        raise FetchError

    regions = {}
    for item in region_rows:
        if (not isinstance(item, dict) or isinstance(item.get("id"), bool)
                or not isinstance(item.get("id"), int) or item["id"] <= 0
                or not isinstance(item.get("region"), str)):
            continue
        regions[item["id"]] = item["region"].strip().casefold()
    if "europe" not in regions.values() or "worldwide" not in regions.values():
        raise FetchError

    statuses = {}
    for item in status_rows:
        if (not isinstance(item, dict) or isinstance(item.get("id"), bool)
                or not isinstance(item.get("id"), int) or item["id"] <= 0
                or not isinstance(item.get("name"), str)):
            continue
        statuses[item["id"]] = item["name"].strip().casefold()
    if not AVAILABLE_RELEASE_STATUSES.issubset(set(statuses.values())):
        raise FetchError
    return regions, statuses


def _game_type_metadata(headers):
    """Load the live game_type reference table instead of relying on legacy enum IDs."""
    rows = igdb_request("game_types", "fields id,type; limit 500;", headers)
    if not isinstance(rows, list) or len(rows) > MAX_API_LIMIT:
        raise FetchError

    game_types = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        type_id = item.get("id")
        label = item.get("type")
        if (isinstance(type_id, bool) or not isinstance(type_id, int) or type_id < 0
                or not isinstance(label, str) or not label.strip()):
            continue
        canonical = _canonical_game_type(label)
        if not canonical:
            continue
        if type_id in game_types and game_types[type_id]["canonical"] != canonical:
            raise FetchError
        game_types[type_id] = {"label": label.strip(), "canonical": canonical}

    if not ALLOWED_GAME_TYPE_NAMES.issubset(
        {item["canonical"] for item in game_types.values()}
    ):
        raise FetchError
    return game_types


def _game_content_decisions(candidate_ids, rows, game_types, debug_content=False):
    """Classify each candidate once using IGDB type and version relationships."""
    infos = {}
    for row in rows:
        game = row.get("game") if isinstance(row, dict) else None
        if not isinstance(game, dict):
            continue
        game_id = game.get("id")
        if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id <= 0:
            continue
        info = infos.setdefault(game_id, {
            "names": set(),
            "game_type_values": set(),
            "game_type_missing": False,
            "game_type_invalid": False,
            "parent_values": set(),
            "parent_missing": False,
            "parent_invalid": False,
            "version_values": set(),
            "version_missing": False,
            "version_invalid": False,
        })
        name = _safe_name(game.get("name"))
        if name:
            info["names"].add(name)

        for field, values_key, missing_key, invalid_key, allow_zero in (
            ("game_type", "game_type_values", "game_type_missing", "game_type_invalid", True),
            ("parent_game", "parent_values", "parent_missing", "parent_invalid", False),
            ("version_parent", "version_values", "version_missing", "version_invalid", False),
        ):
            value = game.get(field)
            if value is None:
                info[missing_key] = True
            elif (isinstance(value, bool) or not isinstance(value, int)
                  or value < 0 or (value == 0 and not allow_zero)):
                info[invalid_key] = True
            else:
                info[values_key].add(value)

    eligible_ids = set()
    excluded_by_type = {}
    decisions = {}
    for game_id in candidate_ids:
        info = infos.get(game_id)
        if info is None:
            info = {
                "names": set(), "game_type_values": set(), "game_type_missing": True,
                "game_type_invalid": False, "parent_values": set(), "parent_missing": True,
                "parent_invalid": False, "version_values": set(), "version_missing": True,
                "version_invalid": False,
            }

        type_values = info["game_type_values"]
        type_id = next(iter(type_values)) if len(type_values) == 1 else None
        type_info = game_types.get(type_id) if type_id is not None else None
        type_label = type_info["label"] if type_info else None
        canonical_type = type_info["canonical"] if type_info else None
        parent_values = info["parent_values"]
        version_values = info["version_values"]

        if info["game_type_invalid"]:
            reason = "invalid_game_type_metadata"
        elif not type_values:
            reason = "missing_game_type"
        elif len(type_values) > 1 or info["game_type_missing"]:
            reason = "contradictory_game_type_metadata"
        elif type_info is None:
            reason = "unknown_game_type_id"
        elif (info["parent_invalid"] or info["version_invalid"]
              or len(parent_values) > 1 or len(version_values) > 1
              or (parent_values and info["parent_missing"])
              or (version_values and info["version_missing"])):
            reason = "contradictory_relationship_metadata"
        elif canonical_type not in ALLOWED_GAME_TYPE_NAMES:
            reason = "non_standalone_game_type"
        elif canonical_type == "main game" and version_values:
            # IGDB documents version_parent as the main game a version belongs to.
            reason = "main_game_is_version_of_another_game"
        elif canonical_type == "main game" and parent_values:
            reason = "main_game_has_parent_content_relationship"
        else:
            reason = "allowed_" + canonical_type.replace(" ", "_")
            eligible_ids.add(game_id)

        if game_id not in eligible_ids:
            if len(type_values) > 1:
                key = "<contradictory game type metadata>"
            elif type_info is None:
                key = "<missing or unknown game type>"
            else:
                key = type_label
            excluded_by_type[key] = excluded_by_type.get(key, 0) + 1
        decisions[game_id] = {
            "game_type_id": type_id,
            "game_type": type_label,
            "observed_game_type_ids": sorted(type_values),
            "observed_game_types": sorted({
                game_types[value]["label"] for value in type_values if value in game_types
            }),
            "decision": "included" if game_id in eligible_ids else "excluded",
            "reason": reason,
            "parent_game_id": next(iter(parent_values)) if len(parent_values) == 1 else None,
            "version_parent_id": next(iter(version_values)) if len(version_values) == 1 else None,
            "name": min(info["names"], key=str.casefold) if info["names"] else "<unknown>",
        }
        if debug_content:
            print(json.dumps({
                "event": "content_filter",
                "game_id": game_id,
                **decisions[game_id],
            }, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)

    return eligible_ids, excluded_by_type, decisions


def _fetch_release_pages(base_query, headers, max_records):
    if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 0:
        raise FetchError
    rows = []
    offset = 0
    while True:
        remaining = max_records - offset
        probe = remaining <= 0
        limit = 1 if probe else min(MAX_API_LIMIT, remaining)
        query = f"{base_query.rstrip()}\nlimit {limit};\noffset {offset};"
        page = igdb_request("release_dates", query, headers)
        if not isinstance(page, list) or len(page) > limit:
            raise FetchError
        if probe:
            if page:
                raise FetchError
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return rows


def _date_window_timestamps(start_day, end_day_exclusive):
    start = datetime.combine(start_day, time.min, tzinfo=BERLIN_TZ).astimezone(timezone.utc)
    end = datetime.combine(end_day_exclusive, time.min, tzinfo=BERLIN_TZ).astimezone(timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def _candidate_game_ids(rows):
    ids = set()
    for row in rows:
        game = row.get("game") if isinstance(row, dict) else None
        game_id = game.get("id") if isinstance(game, dict) else None
        if isinstance(game_id, int) and not isinstance(game_id, bool) and game_id > 0:
            ids.add(game_id)
    return sorted(ids)


def _fetch_game_history(game_ids, platform_id, headers, include_ratings=False):
    rows = []
    for start in range(0, len(game_ids), GAME_ID_BATCH_SIZE):
        batch = game_ids[start:start + GAME_ID_BATCH_SIZE]
        rating_fields = ",game.total_rating,game.total_rating_count" if include_ratings else ""
        query = f"""
            fields id,date,date_format,platform,release_region,status,
                game.id,game.name,game.url,game.cover.image_id,game.platforms,game.game_type,
                game.parent_game,game.version_parent{rating_fields};
            where game = ({','.join(str(game_id) for game_id in batch)})
              & platform = {platform_id};
            sort date asc;
        """
        remaining = MAX_GAME_HISTORY_RECORDS - len(rows)
        batch_rows = _fetch_release_pages(query, headers, remaining)
        rows.extend(batch_rows)
        if len(rows) > MAX_GAME_HISTORY_RECORDS:
            raise FetchError
    return rows


def _select_game_release(records, today):
    europe = [record for record in records if record["release_region"] == "europe"]
    worldwide = [record for record in records if record["release_region"] == "worldwide"]
    authoritative = europe + worldwide
    if any(record["released"] < today for record in authoritative):
        return None, "already_released_europe"

    date_key = lambda record: (record["released"], record["name"].casefold(), record["id"])
    if europe:
        return min(europe, key=date_key), "selected_europe"
    if worldwide:
        return min(worldwide, key=date_key), "selected_worldwide"

    other_regions = [record for record in records if record["release_region"] not in {"europe", "worldwide"}]
    if any(record["released"] < today for record in other_regions):
        return None, "already_released_elsewhere"
    future_other = [record for record in other_regions if record["released"] >= today]
    if future_other:
        return min(future_other, key=lambda record: (
            record["released"], record["release_region"], record["name"].casefold(), record["id"]
        )), "selected_fallback_region"
    return None, "no_usable_release"


def _validated_history(history_rows, platform_id, regions, statuses, debug_platforms=False):
    records_by_game = {}
    reason_counts = {}
    for row in history_rows:
        record, reason = _normalize_ps5_release(row, platform_id, regions, statuses, debug_platforms)
        if record is None:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue
        records_by_game.setdefault(record["id"], []).append(record)
    return records_by_game, reason_counts


def get_games(now=None, debug_platforms=False, diagnostics=None, debug_content=False):
    client_id, client_secret = load_credentials()
    token = get_token(client_id, client_secret)
    headers = {"Client-ID": client_id, "Authorization": f"Bearer {token}"}
    platform_id = _ps5_platform_id(headers)
    regions, statuses = _release_metadata(headers)
    game_types = _game_type_metadata(headers)

    now = now or datetime.now(BERLIN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BERLIN_TZ)
    today = now.astimezone(BERLIN_TZ).date()
    last_included_day = today + timedelta(days=RELEASE_WINDOW_DAYS)
    start_ts, end_ts = _date_window_timestamps(today, last_included_day + timedelta(days=1))
    query = f"""
        fields id,date,date_format,platform,release_region,status,game.id,game.name,
            game.url,game.cover.image_id,game.platforms,game.game_type,game.parent_game,game.version_parent;
        where platform = {platform_id}
          & date >= {start_ts}
          & date < {end_ts};
        sort date asc;
    """
    candidate_rows = _fetch_release_pages(query, headers, MAX_UPCOMING_RELEASE_RECORDS)
    candidate_ids = _candidate_game_ids(candidate_rows)
    history_rows = _fetch_game_history(candidate_ids, platform_id, headers)
    content_ids, content_excluded_by_type, _ = _game_content_decisions(
        candidate_ids, candidate_rows + history_rows, game_types, debug_content
    )
    records_by_game, reason_counts = _validated_history(
        history_rows, platform_id, regions, statuses, debug_platforms
    )

    candidates = []
    released_europe = 0
    released_elsewhere = 0
    no_usable_release = 0
    outside_window = 0
    for game_id in candidate_ids:
        if game_id not in content_ids:
            continue
        selected, reason = _select_game_release(records_by_game.get(game_id, []), today)
        if selected is None:
            if reason == "already_released_europe":
                released_europe += 1
            elif reason == "already_released_elsewhere":
                released_elsewhere += 1
            else:
                no_usable_release += 1
            _selection_debug(debug_platforms, game_id, records_by_game.get(game_id, []), reason)
            continue
        if selected["released"] < today or selected["released"] > last_included_day:
            outside_window += 1
            _selection_debug(debug_platforms, game_id, records_by_game.get(game_id, []), "outside_upcoming_window")
            continue
        candidates.append({
            "id": game_id,
            "name": selected["name"],
            "released": selected["released"].isoformat(),
            "days_left": (selected["released"] - today).days,
            "platform_id": selected["platform_id"],
            "release_platform_id": selected["release_platform_id"],
            "game_platforms": selected["game_platforms"],
            "date_format": selected["date_format"],
            "release_region_id": selected["release_region_id"],
            "release_region": selected["release_region"],
            "release_status_id": selected["release_status_id"],
            "release_status": selected["release_status"],
            "cover_url": selected["cover_url"],
            "game_url": selected["game_url"],
        })

    candidates.sort(key=lambda game: (game["released"], game["name"].casefold(), game["id"]))
    games = _valid_games(candidates[:MAX_GAMES])
    audit = {
        "candidate_release_rows": len(candidate_rows),
        "candidate_games": len(candidate_ids),
        "content_eligible_games": len(content_ids),
        "excluded_content_games": len(candidate_ids) - len(content_ids),
        "excluded_content_by_type": content_excluded_by_type,
        "release_history_rows": len(history_rows),
        "excluded_already_released_europe_or_worldwide": released_europe,
        "excluded_already_released_elsewhere": released_elsewhere,
        "excluded_invalid_date_records": sum(
            count for reason, count in reason_counts.items() if reason in INVALID_DATE_REASONS
        ),
        "excluded_invalid_platform_records": sum(
            count for reason, count in reason_counts.items() if reason in INVALID_PLATFORM_REASONS
        ),
        "excluded_other_invalid_records": sum(
            count for reason, count in reason_counts.items()
            if reason not in INVALID_DATE_REASONS and reason not in INVALID_PLATFORM_REASONS
        ),
        "excluded_no_usable_release": no_usable_release,
        "excluded_outside_window": outside_window,
        "returned": len(games),
    }
    if diagnostics is not None:
        diagnostics.update(audit)
    if debug_platforms:
        print(json.dumps({"event": "upcoming_audit_summary", **audit}, separators=(",", ":")), file=sys.stderr)
    if debug_content:
        print(json.dumps({"event": "upcoming_content_summary", **audit}, separators=(",", ":")), file=sys.stderr)
    return games


def _valid_month_games(value, year, month):
    if not isinstance(value, list) or len(value) > MAX_GAMES:
        raise FetchError

    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        game_id = item.get("id")
        name = _safe_name(item.get("name"))
        released = item.get("released")
        rating = item.get("rating")
        rating_count = item.get("rating_count")
        platform_id = item.get("platform_id")
        release_platform_id = item.get("release_platform_id")
        game_platforms = item.get("game_platforms")
        date_format = item.get("date_format")
        release_region_id = item.get("release_region_id")
        release_region = item.get("release_region")
        release_status_id = item.get("release_status_id")
        release_status = item.get("release_status")
        raw_cover_url = item.get("cover_url")
        cover_url = raw_cover_url if _cover_id_from_url(raw_cover_url) else ""
        game_url = _validated_game_url(item.get("game_url"))
        if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id <= 0:
            continue
        if not name or not isinstance(released, str) or not DATE_PATTERN.fullmatch(released):
            continue
        try:
            released_date = date.fromisoformat(released)
        except ValueError:
            continue
        if released_date.year != year or released_date.month != month:
            continue
        rating = _finite_number(rating)
        if rating is None or rating < 0 or rating > 100:
            continue
        if isinstance(rating_count, bool) or not isinstance(rating_count, int):
            continue
        if rating_count < MIN_MONTH_RATING_COUNT:
            continue
        if (isinstance(platform_id, bool) or not isinstance(platform_id, int)
                or platform_id <= 0 or isinstance(release_platform_id, bool)
                or release_platform_id != platform_id):
            continue
        if (not isinstance(release_platform_id, int) or not isinstance(game_platforms, list)
                or not game_platforms):
            continue
        if any(isinstance(platform, bool) or not isinstance(platform, int) or platform <= 0
                for platform in game_platforms):
            continue
        if platform_id not in game_platforms:
            continue
        if isinstance(date_format, bool) or date_format != 0:
            continue
        if (isinstance(release_region_id, bool) or not isinstance(release_region_id, int)
                or release_region_id <= 0 or not isinstance(release_region, str) or not release_region):
            continue
        if (release_status_id is not None
                and (isinstance(release_status_id, bool) or not isinstance(release_status_id, int))):
            continue
        if release_status is not None and not isinstance(release_status, str):
            continue
        result.append(
            {
                "id": game_id,
                "name": name,
                "rating": float(rating),
                "rating_count": rating_count,
                "released": released,
                "platform_id": platform_id,
                "release_platform_id": release_platform_id,
                "game_platforms": game_platforms,
                "date_format": date_format,
                "release_region_id": release_region_id,
                "release_region": release_region,
                "release_status_id": release_status_id,
                "release_status": release_status,
                "cover_url": cover_url,
                "cover_cache_url": _cached_cover_uri(cover_url),
                "game_url": game_url,
            }
        )

    result.sort(key=lambda game: (-game["rating"], game["released"], game["name"].casefold(), game["id"]))
    return result


def _month_cache_path(year, month):
    if isinstance(year, bool) or not isinstance(year, int) or year < 1 or year > 9999:
        raise FetchError
    if isinstance(month, bool) or not isinstance(month, int) or month < 1 or month > 12:
        raise FetchError
    return CACHE_DIR / f"top-games-{year:04d}-{month:02d}.json"


def get_top_month_games(year, month, debug_platforms=False, diagnostics=None, debug_content=False):
    """Return monthly rankings after selecting the same regional PS5 date as Upcoming."""
    _month_cache_path(year, month)
    client_id, client_secret = load_credentials()
    token = get_token(client_id, client_secret)
    headers = {"Client-ID": client_id, "Authorization": f"Bearer {token}"}
    platform_id = _ps5_platform_id(headers)
    regions, statuses = _release_metadata(headers)
    game_types = _game_type_metadata(headers)

    first_day = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1) if year < 9999 else None
    else:
        next_month = date(year, month + 1, 1)
    if next_month is None:
        raise FetchError
    start_ts, end_ts = _date_window_timestamps(first_day, next_month)
    candidate_query = f"""
        fields id,date,date_format,platform,release_region,status,game.id,game.name,
            game.url,game.cover.image_id,game.platforms,game.game_type,game.parent_game,game.version_parent;
        where platform = {platform_id}
          & date >= {start_ts}
          & date < {end_ts};
        sort date asc;
    """
    candidate_rows = _fetch_release_pages(candidate_query, headers, MAX_MONTH_RELEASE_RECORDS)
    candidate_ids = _candidate_game_ids(candidate_rows)
    history_rows = _fetch_game_history(candidate_ids, platform_id, headers, include_ratings=True)
    content_ids, content_excluded_by_type, _ = _game_content_decisions(
        candidate_ids, candidate_rows + history_rows, game_types, debug_content
    )
    records_by_game, reason_counts = _validated_history(
        history_rows, platform_id, regions, statuses, debug_platforms
    )

    eligible = {}
    released_europe = 0
    for game_id in candidate_ids:
        if game_id not in content_ids:
            continue
        selected, reason = _select_game_release(records_by_game.get(game_id, []), first_day)
        if selected is None:
            if reason == "already_released_europe":
                released_europe += 1
            continue
        if selected["released"].year != year or selected["released"].month != month:
            continue
        eligible[game_id] = selected

    ranked = []
    unranked_count = 0
    for game_id, release in eligible.items():
        game_records = records_by_game.get(game_id, [])
        rating_records = [record for record in game_records if record["rating"] is not None]
        rating_record = max(rating_records, key=lambda record: record["rating_count"]) if rating_records else None
        rating = rating_record["rating"] if rating_record else None
        rating_count = rating_record["rating_count"] if rating_record else 0
        if rating is None or rating_count < MIN_MONTH_RATING_COUNT:
            unranked_count += 1
            continue
        ranked.append({
            "id": game_id,
            "name": release["name"],
            "rating": rating,
            "rating_count": rating_count,
            "released": release["released"].isoformat(),
            "platform_id": release["platform_id"],
            "release_platform_id": release["release_platform_id"],
            "game_platforms": release["game_platforms"],
            "date_format": release["date_format"],
            "release_region_id": release["release_region_id"],
            "release_region": release["release_region"],
            "release_status_id": release["release_status_id"],
            "release_status": release["release_status"],
            "cover_url": release["cover_url"],
            "game_url": release["game_url"],
        })

    ranked.sort(key=lambda game: (-game["rating"], game["released"], game["name"].casefold(), game["id"]))
    valid_ranked = _valid_month_games(ranked[:MAX_GAMES], year, month)
    audit = {
        "candidate_release_rows": len(candidate_rows),
        "candidate_games": len(candidate_ids),
        "content_eligible_games": len(content_ids),
        "excluded_content_games": len(candidate_ids) - len(content_ids),
        "excluded_content_by_type": content_excluded_by_type,
        "release_history_rows": len(history_rows),
        "excluded_already_released_europe_or_worldwide": released_europe,
        "excluded_invalid_date_records": sum(
            count for reason, count in reason_counts.items() if reason in INVALID_DATE_REASONS
        ),
        "monthly_eligible_ranked": len(valid_ranked),
        "unranked_count": unranked_count,
    }
    if diagnostics is not None:
        diagnostics.update(audit)
    if debug_platforms:
        print(json.dumps({"event": "monthly_audit_summary", **audit}, separators=(",", ":")), file=sys.stderr)
    if debug_content:
        print(json.dumps({"event": "monthly_content_summary", **audit}, separators=(",", ":")), file=sys.stderr)
    return {
        "games": valid_ranked,
        "unranked_count": unranked_count,
        "month": f"{year:04d}-{month:02d}",
    }


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def read_fresh_cache(now, cache_file=CACHE_FILE, validator=_valid_games, metadata_reader=None):
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if (not isinstance(payload, dict)
            or isinstance(payload.get("version"), bool)
            or payload.get("version") != CACHE_VERSION):
        return None

    updated_at = _parse_timestamp(payload.get("updated_at"))
    if updated_at is None:
        return None
    age = (now - updated_at).total_seconds()
    if age < 0 or age >= CACHE_TTL_SECONDS:
        return None
    try:
        games = validator(payload.get("games"))
    except FetchError:
        return None
    if validator is _valid_games:
        today = now.astimezone(BERLIN_TZ).date()
        games = [game for game in games if date.fromisoformat(game["released"]) >= today]
        for game in games:
            game["days_left"] = (date.fromisoformat(game["released"]) - today).days
    metadata = metadata_reader(payload) if metadata_reader else {}
    if metadata is None:
        return None
    result = {
        "ok": True,
        "games": games,
        "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
        "cached": True,
    }
    result.update(metadata)
    return result


def write_cache(games, updated_at, cache_file=CACHE_FILE, metadata=None):
    payload = {
        "version": CACHE_VERSION,
        "updated_at": updated_at,
        "games": games,
    }
    if metadata:
        payload.update(metadata)
    temporary_path = None
    try:
        cache_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(prefix=".releases-", dir=cache_file.parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, cache_file)
        temporary_path = None
    except OSError:
        # The current response is still useful even if the optional cache write fails.
        return
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _cached_fetch(cache_file, fetcher, validator, now=None, force=False, metadata_reader=None):
    CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK_FILE.open("a", encoding="utf-8") as lock:
        os.chmod(LOCK_FILE, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        now = now or datetime.now(timezone.utc)
        cached = None if force else read_fresh_cache(now, cache_file, validator, metadata_reader)
        if cached is not None:
            cached["games"] = _cache_cover_images(cached["games"])
            return cached

        fetched = fetcher(now)
        fetched_games = fetched.get("games")
        if not isinstance(fetched_games, list):
            raise FetchError
        games = validator(_cache_cover_images(fetched_games))
        metadata = metadata_reader(fetched) if metadata_reader else {}
        if metadata is None:
            raise FetchError
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        write_cache(games, updated_at, cache_file, metadata)
        result = {
            "ok": True,
            "games": games,
            "updated_at": updated_at,
            "cached": False,
        }
        result.update(metadata)
        return result


def get_cached_or_fetch(now=None, debug_platforms=False, debug_content=False):
    return _cached_fetch(
        CACHE_FILE,
        lambda current: {
            "games": get_games(
                current,
                debug_platforms=debug_platforms,
                debug_content=debug_content,
            )
        },
        _valid_games,
        now=now,
        force=debug_platforms or debug_content,
    )


def _month_metadata_reader(expected_month):
    def read_metadata(payload):
        if not isinstance(payload, dict) or payload.get("month") != expected_month:
            return None
        unranked_count = payload.get("unranked_count")
        if (isinstance(unranked_count, bool) or not isinstance(unranked_count, int)
                or unranked_count < 0 or unranked_count > MAX_MONTH_RELEASE_RECORDS):
            return None
        return {"month": expected_month, "unranked_count": unranked_count}

    return read_metadata


def get_cached_or_fetch_month(year, month, force=False, now=None,
                              debug_platforms=False, debug_content=False):
    cache_file = _month_cache_path(year, month)
    month_key = f"{year:04d}-{month:02d}"
    validator = lambda games: _valid_month_games(games, year, month)
    return _cached_fetch(
        cache_file,
        lambda _current: get_top_month_games(
            year,
            month,
            debug_platforms=debug_platforms,
            debug_content=debug_content,
        ),
        validator,
        now=now,
        force=force or debug_platforms or debug_content,
        metadata_reader=_month_metadata_reader(month_key),
    )


def main(argv=None):
    try:
        parser = argparse.ArgumentParser(description="Fetch PS5 release data from IGDB.")
        parser.add_argument("--top-month", metavar="YYYY-MM")
        parser.add_argument("--force", action="store_true", help="refresh a monthly result, bypassing its cache")
        parser.add_argument(
            "--debug-platforms",
            action="store_true",
            help="bypass cache and report excluded release rows to stderr",
        )
        parser.add_argument(
            "--debug-content",
            action="store_true",
            help="bypass cache and report IGDB game type inclusion decisions to stderr",
        )
        args = parser.parse_args(argv)

        if args.top_month is None:
            if args.force:
                raise FetchError
            result = get_cached_or_fetch(
                debug_platforms=args.debug_platforms,
                debug_content=args.debug_content,
            )
        else:
            match = MONTH_KEY_PATTERN.fullmatch(args.top_month)
            if not match:
                raise FetchError
            year, month = int(match.group(1)), int(match.group(2))
            _month_cache_path(year, month)
            result = get_cached_or_fetch_month(
                year,
                month,
                force=args.force,
                debug_platforms=args.debug_platforms,
                debug_content=args.debug_content,
            )
        exit_code = 0
    except CredentialsError:
        result = {"ok": False, "error": "credentials_missing"}
        exit_code = 1
    except Exception:
        # Never echo exception/request details: they can contain sensitive data.
        result = {"ok": False}
        exit_code = 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
