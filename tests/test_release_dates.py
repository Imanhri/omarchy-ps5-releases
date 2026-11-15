import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import games


BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=BERLIN)
REGIONS = {1: "europe", 2: "north america", 3: "australia", 8: "worldwide", 9: "japan"}
STATUSES = {
    1: "alpha",
    2: "beta",
    3: "early access",
    4: "offline",
    5: "cancelled",
    6: "full release",
    34: "advanced access",
    35: "digital compatibility release",
    36: "next-gen optimization patch release",
}
GAME_TYPES = {
    0: "Main Game",
    1: "DLC",
    2: "Expansion",
    3: "Bundle",
    4: "Standalone Expansion",
    5: "Mod",
    6: "Episode",
    7: "Season",
    8: "Remake",
    9: "Remaster",
    10: "Expanded Game",
    11: "Port",
    12: "Fork",
    13: "Pack / Addon",
    14: "Update",
}


def unix_at(day, hour=12, minute=0, tz=BERLIN):
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).timestamp())


def release(
    game_id,
    name,
    day,
    region=1,
    *,
    platform=167,
    platforms=None,
    date_format=0,
    status=6,
    rating=80,
    rating_count=20,
    timestamp=None,
    first_release_date=None,
    game_type=0,
    parent_game=None,
    version_parent=None,
    cover_image_id=None,
    game_url=None,
):
    game = {
        "id": game_id,
        "name": name,
        "platforms": [167] if platforms is None else platforms,
        "total_rating": rating,
        "total_rating_count": rating_count,
        "game_type": game_type,
        "parent_game": parent_game,
        "version_parent": version_parent,
    }
    if cover_image_id is not None:
        game["cover"] = {"image_id": cover_image_id}
    if game_url is not None:
        game["url"] = game_url
    if first_release_date is not None:
        game["first_release_date"] = first_release_date
    row = {
        "id": game_id * 100 + region,
        "date": timestamp if timestamp is not None else unix_at(day),
        "date_format": date_format,
        "platform": platform,
        "release_region": region,
        "status": status,
        "game": game,
    }
    return row


class FakeIGDB:
    def __init__(self, rows):
        self.rows = list(rows)
        self.release_calls = []

    def request(self, endpoint, query, headers):
        if endpoint == "platforms":
            return [{"id": 167, "name": "PlayStation 5", "slug": "ps5"}]
        if endpoint == "release_date_regions":
            return [{"id": key, "region": value} for key, value in REGIONS.items()]
        if endpoint == "release_date_statuses":
            return [{"id": key, "name": value} for key, value in STATUSES.items()]
        if endpoint == "game_types":
            return [{"id": key, "type": value} for key, value in GAME_TYPES.items()]
        if endpoint != "release_dates":
            raise AssertionError("Unexpected IGDB endpoint")

        self.release_calls.append(query)
        limit_match = re.search(r"\blimit\s+(\d+)\s*;", query, re.I)
        offset_match = re.search(r"\boffset\s+(\d+)\s*;", query, re.I)
        limit = int(limit_match.group(1)) if limit_match else 500
        offset = int(offset_match.group(1)) if offset_match else 0

        game_match = re.search(r"\bwhere\s+game\s*=\s*\(([^)]*)\)", query, re.I)
        if game_match:
            game_ids = {int(value) for value in game_match.group(1).split(",") if value.strip()}
            selected = [
                row for row in self.rows
                if row.get("platform") == 167
                and isinstance(row.get("game"), dict)
                and row["game"].get("id") in game_ids
            ]
        else:
            lower = re.search(r"\bdate\s*>=\s*(\d+)", query, re.I)
            upper = re.search(r"\bdate\s*<\s*(\d+)", query, re.I)
            start = int(lower.group(1)) if lower else -10**20
            end = int(upper.group(1)) if upper else 10**20
            selected = [
                row for row in self.rows
                if row.get("platform") == 167
                and isinstance(row.get("date"), (int, float))
                and start <= row["date"] < end
            ]
        selected.sort(key=lambda item: (item.get("date", 0), item.get("id", 0)))
        return selected[offset:offset + limit]


@contextlib.contextmanager
def mocked_igdb(rows):
    fake = FakeIGDB(rows)
    with (
        mock.patch.object(games, "load_credentials", return_value=("test-client", "test-secret")),
        mock.patch.object(games, "get_token", return_value="test-access-token"),
        mock.patch.object(games, "igdb_request", side_effect=fake.request),
        mock.patch.object(games.time_module, "sleep", return_value=None),
    ):
        yield fake


class ReleaseDateTests(unittest.TestCase):
    def test_credentials_errors_are_specific_but_do_not_include_file_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials_file = Path(directory) / "credentials"
            with mock.patch.object(games, "CONFIG", credentials_file):
                with self.assertRaises(games.CredentialsError):
                    games.load_credentials()

                credentials_file.write_text(
                    "IGDB_CLIENT_ID=private-client-value\n",
                    encoding="utf-8",
                )
                with self.assertRaises(games.CredentialsError):
                    games.load_credentials()

    def test_main_returns_safe_missing_credentials_code_for_both_tabs(self):
        for arguments, fetcher in (
            ([], "get_cached_or_fetch"),
            (["--top-month", "2026-11"], "get_cached_or_fetch_month"),
        ):
            with self.subTest(arguments=arguments):
                output = io.StringIO()
                with (
                    mock.patch.object(games, fetcher, side_effect=games.CredentialsError()),
                    contextlib.redirect_stdout(output),
                ):
                    exit_code = games.main(arguments)
                self.assertEqual(exit_code, 1)
                self.assertEqual(
                    json.loads(output.getvalue()),
                    {"ok": False, "error": "credentials_missing"},
                )
                self.assertNotIn("private-client-value", output.getvalue())

    def test_manifest_qml_module_names_and_credential_messages_are_included(self):
        project = Path(__file__).parents[1]
        manifest = json.loads(project.joinpath("manifest.json").read_text(encoding="utf-8"))
        plugin_id = manifest["id"]
        for filename in ("BarWidget.qml", "Panel.qml"):
            qml = project.joinpath(filename).read_text(encoding="utf-8")
            self.assertIn(f'moduleName: "{plugin_id}"', qml)
        panel = project.joinpath("Panel.qml").read_text(encoding="utf-8")
        translations = json.loads(project.joinpath("i18n/en.json").read_text(encoding="utf-8"))
        self.assertIn(f'ipcTarget: "{plugin_id}"', panel)
        self.assertIn('root.credentialsMissing ? "panel.credentialsMissing"', panel)
        self.assertIn('root.monthlyCredentialsMissing ? "monthly.credentialsMissing"', panel)
        self.assertIn("IGDB_CLIENT_SECRET", translations["panel.credentialsMissing"])
        self.assertIn("IGDB_CLIENT_SECRET", translations["monthly.credentialsMissing"])

    def test_igdb_game_url_validation_accepts_only_safe_detail_paths(self):
        for url in (
            "https://www.igdb.com/games/clair-obscur-expedition-33",
            "https://igdb.com/games/game-123/",
        ):
            with self.subTest(url=url):
                self.assertEqual(games._validated_game_url(url), url)

        for url in (
            None,
            "",
            "https://www.igdb.com/",
            "http://www.igdb.com/games/game-slug",
            "https://attacker.example/games/game-slug",
            "https://www.igdb.com.attacker.example/games/game-slug",
            "https://user@www.igdb.com/games/game-slug",
            "https://www.igdb.com:443/games/game-slug",
            "https://www.igdb.com/games/game-slug?next=https://attacker.example",
            "https://www.igdb.com/games/game-slug#fragment",
            "https://www.igdb.com/games/../admin",
            "https://www.igdb.com/games/%2e%2e",
            "https://www.igdb.com/games/game-slug/reviews",
            "https://www.igdb.com/games/",
            "https://www.igdb.com/games/game-slug\n",
        ):
            with self.subTest(url=url):
                self.assertEqual(games._validated_game_url(url), "")

    def test_game_urls_stay_associated_with_their_game_ids_in_both_tabs(self):
        rows = [
            release(
                701, "Alpha", datetime(2026, 11, 18).date(), rating=92, rating_count=20,
                game_url="https://www.igdb.com/games/alpha-detail",
            ),
            release(
                702, "Beta", datetime(2026, 11, 20).date(), rating=88, rating_count=12,
                game_url="https://igdb.com/games/beta-detail",
            ),
            release(
                703, "Malformed URL", datetime(2026, 11, 22).date(), rating=80,
                rating_count=10, game_url="https://evil.example/games/not-igdb",
            ),
            release(704, "Missing URL", datetime(2026, 11, 23).date(), rating=70, rating_count=10),
        ]
        with mocked_igdb(rows) as fake:
            upcoming = games.get_games(NOW)
            monthly = games.get_top_month_games(2026, 11)

        for result_rows in (upcoming, monthly["games"]):
            by_id = {item["id"]: item["game_url"] for item in result_rows}
            self.assertEqual(by_id[701], "https://www.igdb.com/games/alpha-detail")
            self.assertEqual(by_id[702], "https://igdb.com/games/beta-detail")
            self.assertEqual(by_id[703], "")
            self.assertEqual(by_id[704], "")
        self.assertTrue(fake.release_calls)
        self.assertTrue(all("game.url" in query for query in fake.release_calls))

    def test_qml_rows_open_internal_details_and_browser_uses_argument_array(self):
        panel = Path(__file__).parents[1].joinpath("Panel.qml").read_text(encoding="utf-8")
        self.assertGreaterEqual(panel.count("TapHandler {"), 2)
        self.assertGreaterEqual(panel.count("Qt.PointingHandCursor"), 2)
        self.assertGreaterEqual(panel.count('strings.t("panel.gameDetailsTooltip")'), 2)
        self.assertEqual(panel.count("root.showDetails(modelData)"), 2)
        self.assertIn('gameOpenProcess.command = ["xdg-open", safeUrl]', panel)
        self.assertIn("function safeGameUrl(value)", panel)
        self.assertIn("game_url: root.safeGameUrl(game.game_url)", panel)
        self.assertIn('enabled: destination !== ""', panel)
        self.assertIn("function backToList()", panel)
        self.assertIn("property alias listScroll: panelScroll", panel)
        self.assertIn("property alias detailScroll: detailsScroll", panel)
        self.assertIn("root.listScrollPosition = content.listScroll.contentY", panel)
        self.assertIn("content.listScroll.contentY = root.listScrollPosition", panel)
        self.assertNotIn("root.listScrollPosition = panelScroll.contentY", panel)
        self.assertIn('detailProcess.command = ["python3", root.scriptPath, "--details", String(root.detailProcessGameId)]', panel)
        self.assertIn("Number(payload.game.id) !== root.selectedGame.id", panel)
        self.assertNotRegex(panel, r"xdg-open[^\n]*\+|command:\s*\".*xdg-open")

    def test_details_flickable_is_visible_sibling_of_list_flickable(self):
        panel = Path(__file__).parents[1].joinpath("Panel.qml").read_text(encoding="utf-8")
        list_position = panel.index("id: panelScroll")
        details_position = panel.index("id: detailsScroll")
        def depth_at(position):
            prefix = panel[:position]
            return prefix.count("{") - prefix.count("}")
        self.assertEqual(depth_at(list_position), depth_at(details_position))
        self.assertIn("visible: !root.detailsVisible", panel)
        self.assertIn("visible: root.detailsVisible", panel)
        self.assertNotIn("detailHeroSource(detailHeroImage.status", panel)

    def test_cover_url_generation_and_missing_cover(self):
        self.assertEqual(
            games._cover_url({"image_id": "co8yw2"}),
            "https://images.igdb.com/igdb/image/upload/t_cover_small/co8yw2.jpg",
        )
        self.assertEqual(games._cover_url(None), "")
        self.assertEqual(games._cover_url({"image_id": "../secrets"}), "")
        self.assertEqual(games._cover_url("x" * 129), "")

    def test_cover_cache_downloads_once_and_uses_a_trusted_local_name(self):
        image_bytes = b"\xff\xd8\xffmock-jpeg"
        response = mock.MagicMock()
        response.ok = True
        response.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(image_bytes))}
        response.iter_content.return_value = [image_bytes]
        response_context = mock.MagicMock()
        response_context.__enter__.return_value = response
        response_context.__exit__.return_value = False
        game_row = {"id": 1001, "cover_url": games._cover_url("cover-id_1")}
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(games, "COVER_CACHE_DIR", Path(directory) / "covers"),
                mock.patch.object(games.requests, "get", return_value=response_context) as get_image,
            ):
                first = games._cache_cover_images([dict(game_row)])[0]
                second = games._cache_cover_images([dict(game_row)])[0]
                self.assertTrue(first["cover_cache_url"].endswith("/covers/cover-id_1.jpg"))
                self.assertEqual(first["cover_cache_url"], second["cover_cache_url"])
                self.assertEqual(get_image.call_count, 1)
                self.assertEqual(
                    get_image.call_args.args[0],
                    "https://images.igdb.com/igdb/image/upload/t_cover_small/cover-id_1.jpg",
                )

    def test_cover_failure_keeps_official_cdn_url_for_ui_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(games, "COVER_CACHE_DIR", Path(directory) / "covers"),
                mock.patch.object(games.requests, "get", side_effect=games.requests.ConnectionError),
            ):
                rows = games._cache_cover_images([{
                    "id": 1002,
                    "cover_url": games._cover_url("safe-id"),
                }])
        self.assertEqual(rows[0]["cover_url"], games._cover_url("safe-id"))
        self.assertEqual(rows[0]["cover_cache_url"], "")

    def test_cover_cache_trims_oldest_images_to_its_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory) / "covers"
            cache_dir.mkdir()
            for index in range(3):
                path = cache_dir / f"cover-{index}.jpg"
                path.write_bytes(b"\xff\xd8\xffx")
                path.touch()
                os.utime(path, (index + 1, index + 1))
            with (
                mock.patch.object(games, "COVER_CACHE_DIR", cache_dir),
                mock.patch.object(games, "MAX_CACHED_COVERS", 2),
            ):
                games._trim_cover_cache()
            self.assertEqual(sorted(path.name for path in cache_dir.glob("*.jpg")), [
                "cover-1.jpg", "cover-2.jpg",
            ])

    def test_qml_rows_use_async_cover_images_plain_titles_and_placeholders(self):
        project = Path(__file__).parents[1]
        panel = project.joinpath("Panel.qml").read_text(encoding="utf-8")
        self.assertEqual(panel.count("sourceSize: Qt.size(90, 128)"), 2)
        self.assertGreaterEqual(panel.count("asynchronous: true"), 4)
        self.assertEqual(panel.count("elide: Text.ElideRight"), 2)
        self.assertGreaterEqual(panel.count("textFormat: Text.PlainText"), 10)
        self.assertIn("upcomingCover.status !== Image.Ready", panel)
        self.assertIn("monthlyCover.status !== Image.Ready", panel)
        self.assertIn("cover_url: root.safeCoverUrl(game.cover_url)", panel)
        self.assertEqual(panel.count('Qt.resolvedUrl("ps5-logo.svg")'), 3)
        self.assertTrue(project.joinpath("ps5-logo.svg").is_file())

    def test_europe_release_suppresses_later_other_region_record(self):
        rows = [
            release(101, "Already Out", datetime(2026, 11, 2).date(), region=1),
            release(101, "Already Out", datetime(2026, 11, 20).date(), region=2),
        ]
        audit = {}
        with mocked_igdb(rows):
            found = games.get_games(NOW, diagnostics=audit)
        self.assertEqual(found, [])
        self.assertEqual(audit["candidate_games"], 1)
        self.assertEqual(audit["excluded_already_released_europe_or_worldwide"], 1)

    def test_prior_ps5_release_suppresses_another_future_record(self):
        rows = [
            release(102, "Older PS5 Launch", datetime(2026, 11, 10).date(), region=8),
            release(102, "Older PS5 Launch", datetime(2026, 11, 28).date(), region=1),
        ]
        with mocked_igdb(rows):
            self.assertEqual(games.get_games(NOW), [])

    def test_old_general_first_release_does_not_replace_future_ps5_date(self):
        rows = [release(
            103,
            "PC First, PS5 Later",
            datetime(2026, 12, 2).date(),
            region=1,
            first_release_date=unix_at(datetime(2024, 5, 1).date()),
        )]
        with mocked_igdb(rows):
            found = games.get_games(NOW)
        self.assertEqual([game["name"] for game in found], ["PC First, PS5 Later"])
        self.assertEqual(found[0]["released"], "2026-12-02")

    def test_genuine_upcoming_and_region_duplicates_return_once_with_europe_date(self):
        rows = [
            release(104, "A New Game", datetime(2026, 12, 1).date(), region=2),
            release(104, "A New Game", datetime(2026, 11, 29).date(), region=8),
            release(104, "A New Game", datetime(2026, 11, 29).date(), region=1),
        ]
        with mocked_igdb(rows):
            found = games.get_games(NOW)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["release_region"], "europe")
        self.assertEqual(found[0]["released"], "2026-11-29")

    def test_utc_timestamp_is_compared_as_berlin_calendar_date(self):
        rows = [release(
            105,
            "After Berlin Midnight",
            datetime(2026, 11, 15).date(),
            timestamp=int(datetime(2026, 11, 14, 23, 30, tzinfo=timezone.utc).timestamp()),
        )]
        with mocked_igdb(rows):
            found = games.get_games(NOW)
        self.assertEqual(found[0]["released"], "2026-11-15")
        self.assertEqual(found[0]["days_left"], 2)

    def test_fallback_region_is_only_used_when_it_has_no_prior_release(self):
        future_only = [release(106, "No EU Metadata", datetime(2026, 11, 24).date(), region=9)]
        with mocked_igdb(future_only):
            found = games.get_games(NOW)
        self.assertEqual(found[0]["release_region"], "japan")

        mixed = [
            release(107, "Already Elsewhere", datetime(2026, 11, 5).date(), region=9),
            release(107, "Already Elsewhere", datetime(2026, 11, 24).date(), region=2),
        ]
        with mocked_igdb(mixed):
            self.assertEqual(games.get_games(NOW), [])

    def test_invalid_and_contradictory_release_records_are_rejected(self):
        regions, statuses = REGIONS, STATUSES
        base = release(108, "Invalid", datetime(2026, 11, 20).date())
        cases = [
            ({**base, "date_format": 1}, "approximate_date_precision"),
            ({**base, "date_format": None}, "missing_date_precision"),
            ({**base, "date": None}, "missing_release_date"),
            ({**base, "date": "not-a-timestamp"}, "missing_release_date"),
            ({**base, "platform": 48}, "release_platform_mismatch"),
            ({**base, "game": {**base["game"], "platforms": [48]}}, "game_platforms_missing_ps5"),
            ({**base, "game": {key: value for key, value in base["game"].items() if key != "platforms"}}, "missing_game_platforms"),
            ({**base, "status": 4}, "non_release_status:offline"),
        ]
        for row, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                record, reason = games._normalize_ps5_release(row, 167, regions, statuses)
                self.assertIsNone(record)
                self.assertEqual(reason, expected_reason)

    def test_iso_date_sort_is_stable_and_independent_of_localized_display(self):
        def cached_game(game_id, name, released):
            return {
                "id": game_id,
                "name": name,
                "released": released,
                "days_left": 10,
                "platform_id": 167,
                "release_platform_id": 167,
                "game_platforms": [167],
                "date_format": 0,
                "release_region_id": 1,
                "release_region": "europe",
                "release_status_id": 6,
                "release_status": "full release",
            }

        games_list = games._valid_games([
            cached_game(109, "Later", "2026-11-30"),
            cached_game(110, "Earlier", "2026-11-08"),
            cached_game(111, "Same Date B", "2026-11-12"),
            cached_game(112, "Same Date A", "2026-11-12"),
        ])
        self.assertEqual(
            [(game["released"], game["name"]) for game in games_list],
            [
                ("2026-11-08", "Earlier"),
                ("2026-11-12", "Same Date A"),
                ("2026-11-12", "Same Date B"),
                ("2026-11-30", "Later"),
            ],
        )
        panel = Path(__file__).parents[1].joinpath("Panel.qml").read_text(encoding="utf-8")
        self.assertIn("model: root.games", panel)
        self.assertNotRegex(panel, r"root\.games\s*=\s*[^;]*sort\s*\(")
        self.assertIn('text: root.formattedDate(modelData.released)', panel)

    def test_month_results_use_selected_region_and_keep_rating_ranking(self):
        rows = [
            release(201, "Highest", datetime(2026, 11, 18).date(), region=1, rating=94.5, rating_count=50),
            release(202, "Second", datetime(2026, 11, 20).date(), region=8, rating=88, rating_count=9),
            release(202, "Second", datetime(2026, 11, 22).date(), region=2, rating=88, rating_count=9),
            release(203, "Not Enough Ratings", datetime(2026, 11, 21).date(), region=1, rating=99, rating_count=4),
            release(204, "Past Before Month", datetime(2026, 10, 31).date(), region=1, rating=99, rating_count=20),
        ]
        audit = {}
        with mocked_igdb(rows):
            result = games.get_top_month_games(2026, 11, diagnostics=audit)
        self.assertEqual([game["name"] for game in result["games"]], ["Highest", "Second"])
        self.assertEqual(result["games"][1]["release_region"], "worldwide")
        self.assertEqual(result["unranked_count"], 1)
        self.assertEqual(audit["candidate_games"], 3)
        self.assertEqual(games._month_cache_path(2026, 11).name, "top-games-2026-11.json")

    def test_month_navigation_updates_year_month_and_request_key(self):
        panel = Path(__file__).parents[1].joinpath("Panel.qml").read_text(encoding="utf-8")
        self.assertIn("function moveMonth(delta)", panel)
        self.assertIn("root.selectedYear = next.getFullYear()", panel)
        self.assertIn("root.selectedMonth = next.getMonth() + 1", panel)
        self.assertIn("root.monthlyRequestKey = root.selectedMonthKey()", panel)
        self.assertIn("selectedMonthKey()", panel)

    def test_release_page_fetch_includes_records_after_first_api_batch(self):
        rows = [{"id": value} for value in range(600)]
        offsets = []

        def page(endpoint, query, headers):
            offset = int(re.search(r"\boffset\s+(\d+)", query).group(1))
            limit = int(re.search(r"\blimit\s+(\d+)", query).group(1))
            offsets.append(offset)
            return rows[offset:offset + limit]

        with mock.patch.object(games, "igdb_request", side_effect=page):
            found = games._fetch_release_pages("fields id;", {}, 1_000)
        self.assertEqual(len(found), 600)
        self.assertEqual(found[-1]["id"], 599)
        self.assertEqual(offsets, [0, 500])

    def test_cache_schema_v5_is_rejected(self):
        payload = {
            "version": 5,
            "updated_at": NOW.astimezone(timezone.utc).isoformat(),
            "games": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / "releases.json"
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNone(games.read_fresh_cache(NOW.astimezone(timezone.utc), cache_file))

    def test_standalone_content_policy_uses_dynamic_game_type_names(self):
        day = datetime(2026, 11, 22).date()
        rows = [
            release(401, "Main Game", day, game_type=0),
            release(402, "DLC Add-on", day, game_type=1),
            release(403, "Cosmetic Outfit Pack", day, game_type=13),
            release(404, "Original Soundtrack", day, game_type=13),
            release(405, "Season Pass", day, game_type=7),
            release(406, "Premium Edition", day, game_type=0, version_parent=400),
            release(407, "Game Collection Bundle", day, game_type=3),
            release(408, "Standalone Expansion", day, game_type=4, parent_game=401),
            release(409, "Remake", day, game_type=8),
            release(410, "Remaster", day, game_type=9),
            release(411, "Port", day, game_type=11),
            release(412, "Missing Type", day, game_type=None),
            release(413, "Multi-platform PS5 Game", day, game_type=0, platforms=[48, 167]),
            release(414, "Contradictory Type", day, game_type=0),
            release(414, "Contradictory Type", day, region=8, game_type=13),
            release(415, "Non-standalone Expansion", day, game_type=2),
            release(416, "Standalone Deluxe Edition", day, game_type=0),
            release(417, "Unknown Type", day, game_type=99),
        ]
        with mocked_igdb(rows):
            audit = {}
            found = games.get_games(NOW, diagnostics=audit)
        self.assertEqual(
            {item["id"] for item in found},
            {401, 408, 409, 410, 411, 413, 416},
        )
        self.assertEqual(len(found), len({item["id"] for item in found}))
        self.assertEqual(audit["excluded_content_games"], 10)
        self.assertEqual(audit["excluded_content_by_type"]["Pack / Addon"], 2)
        self.assertEqual(audit["excluded_content_by_type"]["<contradictory game type metadata>"], 1)
        self.assertEqual(audit["excluded_content_by_type"]["<missing or unknown game type>"], 2)

    def test_game_type_ids_are_resolved_from_the_live_reference_table(self):
        remapped = [{"id": type_id + 100, "type": label} for type_id, label in GAME_TYPES.items()]
        with mock.patch.object(games, "igdb_request", return_value=remapped):
            type_table = games._game_type_metadata({})
        eligible, excluded, decisions = games._game_content_decisions(
            [990],
            [{"game": {"id": 990, "name": "Dynamic ID Game", "game_type": 100}}],
            type_table,
        )
        self.assertEqual(eligible, {990})
        self.assertEqual(excluded, {})
        self.assertEqual(decisions[990]["game_type"], "Main Game")

    def test_content_filter_applies_before_upcoming_limit_and_sort(self):
        rows = []
        for index in range(15):
            day = datetime(2026, 11, 14 + index % 15).date()
            rows.append(release(500 + index, f"Pack {index:03d}", day, game_type=13))
        for index in range(15):
            day = datetime(2026, 12, 1 + index % 20).date()
            rows.append(release(700 + index, f"Game {index:03d}", day, game_type=0))
        with mocked_igdb(rows):
            found = games.get_games(NOW)
        self.assertEqual(len(found), 10)
        self.assertTrue(all(item["id"] >= 700 for item in found))
        self.assertEqual(len({item["id"] for item in found}), 10)
        keys = [(item["released"], item["name"].casefold(), item["id"]) for item in found]
        self.assertEqual(keys, sorted(keys))

    def test_upcoming_results_always_have_safe_cover_fields(self):
        rows = [release(
            720,
            "Cover Game",
            datetime(2026, 11, 19).date(),
            cover_image_id="cover123",
        )]
        with mocked_igdb(rows):
            found = games.get_games(NOW)
        self.assertEqual(len(found), 1)
        self.assertEqual(
            found[0]["cover_url"],
            "https://images.igdb.com/igdb/image/upload/t_cover_small/cover123.jpg",
        )
        self.assertEqual(found[0]["cover_cache_url"], "")

    def test_monthly_content_filter_preserves_rating_order_and_removes_dlc(self):
        day = datetime(2026, 11, 22).date()
        rows = [
            release(801, "High Rated DLC", day, game_type=1, rating=99, rating_count=100),
            release(802, "Main Game", day, game_type=0, rating=93, rating_count=20),
            release(803, "Standalone Expansion", day, game_type=4, parent_game=802, rating=90, rating_count=10),
            release(804, "Special Edition", day, game_type=0, version_parent=802, rating=98, rating_count=40),
            release(805, "Remaster", day, game_type=9, rating=95, rating_count=15,
                    cover_image_id="monthcover"),
        ]
        with mocked_igdb(rows):
            audit = {}
            result = games.get_top_month_games(2026, 11, diagnostics=audit)
        self.assertEqual([item["id"] for item in result["games"]], [805, 802, 803])
        ratings = [item["rating"] for item in result["games"]]
        self.assertEqual(ratings, sorted(ratings, reverse=True))
        self.assertEqual(
            result["games"][0]["cover_url"],
            "https://images.igdb.com/igdb/image/upload/t_cover_small/monthcover.jpg",
        )
        self.assertEqual(audit["excluded_content_by_type"]["DLC"], 1)
        self.assertEqual(audit["excluded_content_by_type"]["Main Game"], 1)

    def test_monthly_results_are_limited_to_ten_after_rating_sort(self):
        day = datetime(2026, 11, 22).date()
        rows = [
            release(
                1000 + index,
                f"Month Game {index:02d}",
                day,
                rating=60 + index,
                rating_count=10 + index,
            )
            for index in range(12)
        ]
        with mocked_igdb(rows):
            result = games.get_top_month_games(2026, 11)
        self.assertEqual(len(result["games"]), 10)
        self.assertEqual([game["id"] for game in result["games"]], list(range(1011, 1001, -1)))
        ratings = [game["rating"] for game in result["games"]]
        self.assertEqual(ratings, sorted(ratings, reverse=True))

    def test_debug_content_reports_ids_types_and_reasons_without_secrets(self):
        rows = [
            release(901, "Allowed Game", datetime(2026, 11, 22).date(), game_type=0),
            release(902, "Excluded Pack", datetime(2026, 11, 22).date(), game_type=13),
        ]
        output = io.StringIO()
        with mocked_igdb(rows), contextlib.redirect_stderr(output):
            found = games.get_games(NOW, debug_content=True)
        self.assertEqual([item["id"] for item in found], [901])
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        decisions = {event["game_id"]: event for event in events if event.get("event") == "content_filter"}
        self.assertEqual(decisions[901]["decision"], "included")
        self.assertEqual(decisions[901]["game_type"], "Main Game")
        self.assertEqual(decisions[902]["decision"], "excluded")
        self.assertEqual(decisions[902]["game_type"], "Pack / Addon")
        self.assertEqual(decisions[902]["reason"], "non_standalone_game_type")
        self.assertNotIn("test-access-token", output.getvalue())
        self.assertNotIn("test-secret", output.getvalue())

    def test_debug_diagnostics_describe_exclusion_without_secret_values(self):
        row = release(301, "Safe Diagnostic Title", datetime(2026, 11, 18).date(), date_format=1)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            record, reason = games._normalize_ps5_release(row, 167, REGIONS, STATUSES, True)
        self.assertIsNone(record)
        self.assertEqual(reason, "approximate_date_precision")
        event = json.loads(output.getvalue())
        self.assertEqual(event["game_id"], 301)
        self.assertEqual(event["name"], "Safe Diagnostic Title")
        self.assertEqual(event["release_date_id"], 30101)
        self.assertNotIn("test-access-token", output.getvalue())
        self.assertNotIn("test-secret", output.getvalue())

        valid_row = release(302, "Previously Released", datetime(2026, 11, 5).date(), region=1)
        valid_record, reason = games._normalize_ps5_release(valid_row, 167, REGIONS, STATUSES)
        self.assertIsNone(reason)
        selection_output = io.StringIO()
        with contextlib.redirect_stderr(selection_output):
            games._selection_debug(True, 302, [valid_record], "already_released_europe")
        selection = json.loads(selection_output.getvalue())
        self.assertEqual(selection["release_records"][0]["release_date_id"], 30201)
        self.assertEqual(selection["release_records"][0]["release_region"], "europe")
        self.assertEqual(selection["release_records"][0]["released_berlin"], "2026-11-05")
        self.assertNotIn("test-secret", selection_output.getvalue())

    def test_platform_endpoint_must_resolve_to_igdb_id_167(self):
        with mock.patch.object(
            games,
            "igdb_request",
            return_value=[{"id": 48, "name": "PlayStation 5", "slug": "ps5"}],
        ):
            with self.assertRaises(games.FetchError):
                games._ps5_platform_id({})


if __name__ == "__main__":
    unittest.main()
