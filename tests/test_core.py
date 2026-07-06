import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rte_download.core import (
    GETPLAYLIST_URL,
    build_filename_stem,
    extract_archive_id,
    extract_getplaylist_ids,
    extract_media_urls,
    find_hls_stream_url,
    parse_page,
    resolve_stream,
)


NEW_PAGE_HTML = """
<html>
  <head>
    <meta name="date" content="2019-09-14">
    <meta name="av" content="uri:avms:11080866">
    <meta name="DC.title" content="Lotto Syndicate Celebrate">
    <meta name="DC.identifier" content="urn:epic:1075232">
  </head>
  <body>
    <iframe src="https://www.rte.ie/media-embed/dustin/?id=11080866&amp;thumbnail=0012aad5"></iframe>
  </body>
</html>
"""

OLD_PAGE_HTML = """
<html>
  <head>
    <meta name="date" content="2017-08-30">
    <meta name="DC.title" content="National Lottery Details Announced">
    <meta name="DC.identifier" content="urn:clipper:21226157">
  </head>
  <body>
    <iframe src="https://www.rte.ie/media-embed/dustin/?id=21226157&amp;thumbnail=000fcd4e"></iframe>
  </body>
</html>
"""

NEW_PLAYLIST = {
    "shows": [
        {
            "itemid": 11080866,
            "title": "RT&#201; News : Syndicate From Ballyfermot Wins Lotto Jackpot ",
            "published": "2019-08-21T12:04:00",
            "media:group": [
                {
                    "hls_server": "https://cdn.rasset.ie/hls-vod",
                    "hls_url": "/2019/0821/example/manifest.m3u8",
                }
            ],
        }
    ]
}

OLD_PLAYLIST = {
    "shows": [
        {
            "itemid": 21226157,
            "title": "National Lottery Details Announced",
            "published": "2017-08-30T11:24:00",
            "media:group": [
                {
                    "hls_server": "https://cdn.rasset.ie/hls-vod",
                    "hls_url": "/1986/1007/example/manifest.m3u8",
                }
            ],
        }
    ]
}


class CoreTests(unittest.TestCase):
    def test_parse_new_page_prefers_avms_player_id_before_archive_id(self):
        page = parse_page(
            "https://www.rte.ie/archives/2019/0914/1075232-lotto-syndicate-celebrate/",
            NEW_PAGE_HTML,
        )

        self.assertEqual(page.archive_id, "1075232")
        self.assertEqual(page.player_ids, ("11080866", "1075232"))
        self.assertEqual(page.title, "Lotto Syndicate Celebrate")
        self.assertEqual(page.date, "2019-09-14")

    def test_parse_old_page_uses_legacy_archive_id(self):
        page = parse_page(
            "https://www.rte.ie/archives/collections/news/21226157-national-lottery-details-announced/",
            OLD_PAGE_HTML,
        )

        self.assertEqual(page.archive_id, "21226157")
        self.assertEqual(page.player_ids, ("21226157",))

    def test_extract_archive_id_from_url_when_meta_missing(self):
        self.assertEqual(
            extract_archive_id(
                "https://www.rte.ie/archives/collections/news/21226157-national-lottery-details-announced/"
            ),
            "21226157",
        )

    def test_parse_page_supports_direct_dustin_url(self):
        page = parse_page(
            "https://www.rte.ie/media-embed/dustin/?id=11080866&thumbnail=0012aad5&subheader=off",
            "<html><head><title>Dustin</title></head></html>",
        )

        self.assertEqual(page.player_ids, ("11080866",))
        self.assertEqual(page.title, "Dustin")

    def test_extracts_raw_media_and_playlist_urls_from_html(self):
        text = """
        window.player = "https://www.rte.ie/media-embed/dustin/?id=11080866&thumbnail=0012aad5";
        window.playlist = "https://www.rte.ie/rteavgen/getplaylist/?id=21226157";
        """

        self.assertEqual(
            extract_media_urls(text),
            ["https://www.rte.ie/media-embed/dustin/?id=11080866&thumbnail=0012aad5"],
        )
        self.assertEqual(extract_getplaylist_ids(text), ["21226157"])

    def test_find_hls_stream_url_uses_structured_hls_fields(self):
        self.assertEqual(
            find_hls_stream_url(NEW_PLAYLIST),
            "https://cdn.rasset.ie/hls-vod/2019/0821/example/manifest.m3u8",
        )

    def test_resolve_stream_tries_new_player_id_then_legacy_id(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if url.startswith("https://www.rte.ie/archives/"):
                return NEW_PAGE_HTML
            if url == GETPLAYLIST_URL.format(id="11080866"):
                return json.dumps(NEW_PLAYLIST)
            raise AssertionError(f"unexpected URL: {url}")

        stream = resolve_stream(
            "https://www.rte.ie/archives/2019/0914/1075232-lotto-syndicate-celebrate/",
            fetch=fetch,
        )

        self.assertEqual(stream.playlist_id, "11080866")
        self.assertEqual(stream.stream_url, "https://cdn.rasset.ie/hls-vod/2019/0821/example/manifest.m3u8")
        self.assertIn("11080866", stream.filename_stem)
        self.assertEqual(calls[1], GETPLAYLIST_URL.format(id="11080866"))

    def test_resolve_stream_falls_back_to_archive_id_if_embedded_id_fails(self):
        page_html = NEW_PAGE_HTML.replace("11080866", "99999999")

        def fetch(url):
            if url.startswith("https://www.rte.ie/archives/"):
                return page_html
            if url == GETPLAYLIST_URL.format(id="99999999"):
                return json.dumps({"shows": []})
            if url == GETPLAYLIST_URL.format(id="1075232"):
                return json.dumps(NEW_PLAYLIST)
            raise AssertionError(f"unexpected URL: {url}")

        stream = resolve_stream(
            "https://www.rte.ie/archives/2019/0914/1075232-lotto-syndicate-celebrate/",
            fetch=fetch,
        )

        self.assertEqual(stream.playlist_id, "1075232")

    def test_resolve_stream_supports_direct_dustin_url(self):
        def fetch(url):
            if url.startswith("https://www.rte.ie/media-embed/dustin/"):
                return "<html><head><title>Dustin</title></head></html>"
            if url == GETPLAYLIST_URL.format(id="11080866"):
                return json.dumps(NEW_PLAYLIST)
            raise AssertionError(f"unexpected URL: {url}")

        stream = resolve_stream(
            "https://www.rte.ie/media-embed/dustin/?id=11080866&thumbnail=0012aad5&subheader=off",
            fetch=fetch,
        )

        self.assertEqual(stream.playlist_id, "11080866")
        self.assertEqual(stream.title, "RTÉ News : Syndicate From Ballyfermot Wins Lotto Jackpot")
        self.assertIn("Syndicate From Ballyfermot", stream.filename_stem)

    def test_resolve_stream_supports_old_page(self):
        def fetch(url):
            if url.startswith("https://www.rte.ie/archives/"):
                return OLD_PAGE_HTML
            if url == GETPLAYLIST_URL.format(id="21226157"):
                return json.dumps(OLD_PLAYLIST)
            raise AssertionError(f"unexpected URL: {url}")

        stream = resolve_stream(
            "https://www.rte.ie/archives/collections/news/21226157-national-lottery-details-announced/",
            fetch=fetch,
        )

        self.assertEqual(stream.playlist_id, "21226157")
        self.assertEqual(stream.stream_url, "https://cdn.rasset.ie/hls-vod/1986/1007/example/manifest.m3u8")

    def test_build_filename_stem_sanitizes_path_separators(self):
        self.assertEqual(
            build_filename_stem("{date} - {title} [{id}]", date="2019-09-14", title="A/B: C?", id="11080866"),
            "2019-09-14 - A-B - C [11080866]",
        )


if __name__ == "__main__":
    unittest.main()
