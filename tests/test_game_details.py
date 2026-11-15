import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import games


NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def detail_row():
    return {
        "id": 321,
        "name": "The Game",
        "summary": "<script>plain text</script>",
        "storyline": "An official storyline.",
        "genres": [{"name": "Adventure"}, {"name": "RPG"}],
        "involved_companies": [
            {"company": {"name": "Studio"}, "developer": True, "publisher": False},
            {"company": {"name": "Label"}, "developer": False, "publisher": True},
            {"company": {"name": "Both"}, "developer": True, "publisher": True},
            {"company": {"name": "Support"}, "developer": False, "publisher": False},
        ],
        "platforms": [{"name": "PlayStation 5"}, {"name": "PC (Microsoft Windows)"}],
        "cover": {"image_id": "abc123"},
        "artworks": [{"image_id": "art456"}],
        "total_rating": 84.5,
        "url": "https://www.igdb.com/games/the-game",
        "first_release_date": 1000,
    }


class GameDetailTests(unittest.TestCase):
    def test_game_id_rejects_injection_and_invalid_values(self):
        self.assertEqual(games._game_id("321"), 321)
        for value in ("0", "-1", "+1", "1;touch /tmp/x", "1 2", "001", "2147483648", "abc"):
            with self.subTest(value=value), self.assertRaises(games.FetchError):
                games._game_id(value)

    def test_metadata_and_roles_are_extracted_without_general_release_date(self):
        data = games._normalise_details(detail_row(), 321)
        self.assertEqual(data["developers"], ["Studio", "Both"])
        self.assertEqual(data["publishers"], ["Label", "Both"])
        self.assertEqual(data["genres"], ["Adventure", "RPG"])
        self.assertEqual(data["platforms"], ["PlayStation 5", "PC (Microsoft Windows)"])
        self.assertEqual(data["summary"], "<script>plain text</script>")
        self.assertEqual(data["artwork_url"], "https://images.igdb.com/igdb/image/upload/t_screenshot_med/art456.jpg")
        self.assertEqual(data["rating"], 84.5)
        self.assertNotIn("released", data)
        self.assertNotIn("first_release_date", data)

    def test_missing_and_malformed_fields_remain_missing(self):
        data = games._normalise_details({"id": 321, "name": "Sparse", "total_rating": 150,
                                         "url": "https://evil.example/games/sparse",
                                         "involved_companies": [{"company": {"name": "Unknown"}}]}, 321)
        self.assertEqual(data["summary"], "")
        self.assertEqual(data["storyline"], "")
        self.assertEqual(data["developers"], [])
        self.assertEqual(data["publishers"], [])
        self.assertEqual(data["genres"], [])
        self.assertEqual(data["platforms"], [])
        self.assertIsNone(data["rating"])
        self.assertEqual(data["game_url"], "")
        with self.assertRaises(games.FetchError):
            games._normalise_details({"id": 999, "name": "Wrong"}, 321)

    def test_detail_cache_is_separate_six_hours_and_avoids_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(games, "DETAIL_CACHE_DIR", Path(directory)), \
                 mock.patch.object(games, "_cached_cover_uri", return_value=""), \
                 mock.patch.object(games, "load_credentials", return_value=("id", "secret")), \
                 mock.patch.object(games, "get_token", return_value="token"), \
                 mock.patch.object(games, "igdb_request", return_value=[detail_row()]) as request:
                first = games.get_game_details(321, NOW)
                second = games.get_game_details(321, NOW + timedelta(hours=5))
                self.assertFalse(first["cached"])
                self.assertTrue(second["cached"])
                self.assertEqual(request.call_count, 1)
                self.assertIn("where id = 321;", request.call_args.args[1])
                self.assertEqual(Path(directory, "321.json").stat().st_mode & 0o777, 0o600)
                games.get_game_details(321, NOW + timedelta(hours=7))
                self.assertEqual(request.call_count, 2)

    def test_errors_are_safe_and_do_not_cache_failed_results(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(games, "DETAIL_CACHE_DIR", Path(directory)), \
                 mock.patch.object(games, "load_credentials", return_value=("id", "secret")), \
                 mock.patch.object(games, "get_token", return_value="token"), \
                 mock.patch.object(games, "igdb_request", return_value=[]):
                with self.assertRaises(games.FetchError):
                    games.get_game_details(321, NOW)
                self.assertFalse(Path(directory, "321.json").exists())
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(games, "get_game_details", side_effect=games.CredentialsError):
            self.assertEqual(games.main(["--details", "321"]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"ok": False, "error": "credentials_missing", "game_id": 321})
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(games, "get_game_details") as fetch:
            self.assertEqual(games.main(["--details", "1;bad"]), 1)
            fetch.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {"ok": False})


if __name__ == "__main__":
    unittest.main()
