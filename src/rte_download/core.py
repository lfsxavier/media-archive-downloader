from __future__ import annotations

import html
import json
import logging
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable

LOG = logging.getLogger(__name__)

GETPLAYLIST_URL = "https://www.rte.ie/rteavgen/getplaylist/?id={id}"
USER_AGENT = "Mozilla/5.0 (compatible; rte-download/0.1)"


class RteDownloadError(RuntimeError):
    """Raised when an RTÉ archive video cannot be resolved or downloaded."""


@dataclass(frozen=True)
class PageInfo:
    url: str
    archive_id: str | None
    player_ids: tuple[str, ...]
    title: str
    date: str | None


@dataclass(frozen=True)
class StreamInfo:
    page: PageInfo
    playlist_id: str
    playlist_url: str
    stream_url: str
    title: str
    date: str | None
    filename_stem: str


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.iframes: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = attrs_dict.get("name") or attrs_dict.get("property")
            content = attrs_dict.get("content")
            if key and content is not None:
                self.meta[key] = html.unescape(content).strip()
        elif tag.lower() == "iframe":
            src = attrs_dict.get("src")
            if src:
                self.iframes.append(html.unescape(src).strip())
        elif tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def fetch_text(url: str, timeout: int = 30) -> str:
    LOG.debug("Fetching %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_page(url: str, html_text: str) -> PageInfo:
    parser = MetadataParser()
    parser.feed(html_text)

    archive_id = extract_archive_id(url, parser.meta)
    player_ids = unique_ids(
        [
            *extract_url_ids(url),
            *extract_avms_ids(parser.meta),
            *extract_embed_ids(parser.iframes),
            *extract_embed_ids(extract_media_urls(html_text)),
            *extract_getplaylist_ids(html_text),
            archive_id,
        ]
    )

    title = first_non_empty(
        parser.meta.get("DC.title"),
        parser.meta.get("og:title"),
        " ".join(parser.title_parts).strip(),
        title_from_url(url),
        "rte-video",
    )
    date = first_non_empty(parser.meta.get("date"), parser.meta.get("contextdate"))

    return PageInfo(
        url=url,
        archive_id=archive_id,
        player_ids=tuple(player_ids),
        title=clean_text(title),
        date=date,
    )


def extract_archive_id(url: str, meta: dict[str, str] | None = None) -> str | None:
    if meta:
        identifier = meta.get("DC.identifier", "")
        match = re.search(r"urn:(?:epic|clipper):(\d+)", identifier)
        if match:
            return match.group(1)

    parsed = urllib.parse.urlparse(url)
    matches = re.findall(r"(?:^|/)(\d{6,})(?=-|/|$)", parsed.path)
    return matches[-1] if matches else None


def extract_avms_ids(meta: dict[str, str]) -> list[str]:
    ids: list[str] = []
    for key, value in meta.items():
        if key.lower() == "av":
            ids.extend(re.findall(r"(?:avms:|^)(\d{6,})", value))
    return ids


def extract_url_ids(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url.strip())
    query = urllib.parse.parse_qs(parsed.query)
    return [id_ for id_ in query.get("id", []) if re.fullmatch(r"\d{6,}", id_)]


def extract_embed_ids(iframe_sources: Iterable[str]) -> list[str]:
    ids: list[str] = []
    for src in iframe_sources:
        parsed = urllib.parse.urlparse(src.strip())
        if "media-embed/dustin" not in parsed.path:
            continue
        query = urllib.parse.parse_qs(parsed.query)
        ids.extend(query.get("id", []))
    return [id_ for id_ in ids if re.fullmatch(r"\d{6,}", id_)]


def extract_media_urls(text: str) -> list[str]:
    text = html.unescape(text)
    return re.findall(r"https?://[^\s\"'<>]+/media-embed/dustin/\?[^\s\"'<>]+", text)


def extract_getplaylist_ids(text: str) -> list[str]:
    text = html.unescape(text)
    ids = re.findall(r"/rteavgen/getplaylist/\?[^\"'<>\s]*\bid=(\d{6,})", text)
    ids.extend(re.findall(r"https?://[^\s\"'<>]+/rteavgen/getplaylist/\?[^\"'<>\s]*\bid=(\d{6,})", text))
    return unique_ids(ids)


def fetch_playlist(playlist_id: str, fetch: Callable[[str], str] = fetch_text) -> dict[str, Any]:
    url = GETPLAYLIST_URL.format(id=playlist_id)
    LOG.info("Fetching RTÉ playlist %s", url)
    return json.loads(fetch(url))


def resolve_stream(
    url: str,
    fetch: Callable[[str], str] = fetch_text,
    filename_template: str = "{date} - {title} [{id}]",
) -> StreamInfo:
    page_html = fetch(url)
    page = parse_page(url, page_html)

    if not page.player_ids:
        raise RteDownloadError("Could not find an RTÉ archive or player ID on the page.")

    LOG.debug("Candidate player IDs: %s", ", ".join(page.player_ids))
    last_error: Exception | None = None

    for playlist_id in page.player_ids:
        try:
            playlist = fetch_playlist(playlist_id, fetch=fetch)
            stream_url = find_hls_stream_url(playlist)
        except Exception as exc:
            LOG.debug("Playlist ID %s did not resolve: %s", playlist_id, exc)
            last_error = exc
            continue

        if not stream_url:
            LOG.debug("Playlist ID %s had no HLS stream", playlist_id)
            continue

        title = clean_text(best_title(page.title, playlist_title(playlist)))
        date = first_non_empty(page.date, playlist_date(playlist))
        stem = build_filename_stem(filename_template, title=title, date=date, id=playlist_id)

        return StreamInfo(
            page=page,
            playlist_id=playlist_id,
            playlist_url=GETPLAYLIST_URL.format(id=playlist_id),
            stream_url=stream_url,
            title=title,
            date=date,
            filename_stem=stem,
        )

    if last_error:
        raise RteDownloadError(f"Could not resolve a playable HLS stream: {last_error}") from last_error
    raise RteDownloadError("Could not find a playable HLS stream in any RTÉ playlist.")


def find_hls_stream_url(data: Any) -> str | None:
    structured = find_structured_hls(data)
    if structured:
        return structured

    found = find_m3u8(data)
    if isinstance(found, str):
        return normalize_stream_url(found)
    return None


def find_structured_hls(data: Any) -> str | None:
    if isinstance(data, dict):
        hls_server = data.get("hls_server")
        hls_url = data.get("hls_url")
        if isinstance(hls_server, str) and isinstance(hls_url, str):
            return urllib.parse.urljoin(hls_server.rstrip("/") + "/", hls_url.lstrip("/"))
        for value in data.values():
            found = find_structured_hls(value)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = find_structured_hls(value)
            if found:
                return found
    return None


def find_m3u8(data: Any) -> str | None:
    if isinstance(data, str):
        return html.unescape(data) if ".m3u8" in data else None
    if isinstance(data, dict):
        for value in data.values():
            found = find_m3u8(value)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = find_m3u8(value)
            if found:
                return found
    return None


def normalize_stream_url(value: str) -> str:
    value = html.unescape(value).strip()
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("hls-vod/"):
        return "https://cdn.rasset.ie/" + value
    if value.startswith("/"):
        return "https://cdn.rasset.ie/hls-vod" + value
    return value


def playlist_title(data: Any) -> str | None:
    show = first_show(data)
    if isinstance(show, dict):
        return first_non_empty(show.get("title"), show.get("ptitle"))
    return None


def best_title(page_title: str | None, media_title: str | None) -> str:
    if page_title and page_title.lower() not in {"dustin", "rte player"}:
        return page_title
    return media_title or page_title or "rte-video"


def playlist_date(data: Any) -> str | None:
    show = first_show(data)
    if isinstance(show, dict):
        published = show.get("published")
        if isinstance(published, str) and len(published) >= 10:
            return published[:10]
    return None


def first_show(data: Any) -> Any:
    if isinstance(data, dict):
        shows = data.get("shows")
        if isinstance(shows, list) and shows:
            return shows[0]
    return None


def build_filename_stem(template: str, **values: str | None) -> str:
    safe_values = {
        key: sanitize_filename_component(value or "").strip()
        for key, value in values.items()
    }
    if not safe_values.get("date"):
        safe_values["date"] = "undated"

    try:
        stem = template.format(**safe_values)
    except KeyError as exc:
        raise RteDownloadError(f"Unknown filename template field: {exc.args[0]}") from exc

    return sanitize_filename_component(stem).strip(" .") or "rte-video"


def download_with_ytdlp(stream: StreamInfo, output_dir: Path) -> int:
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "-P",
        str(output_dir),
        "-o",
        f"{stream.filename_stem}.%(ext)s",
        stream.stream_url,
    ]
    LOG.info("Running: %s", " ".join(cmd))
    return subprocess.run(cmd).returncode


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sanitize_filename_component(value: str) -> str:
    value = clean_text(value)
    value = value.replace("/", "-").replace(":", " -")
    value = re.sub(r'[<>:"\\|?*\x00-\x1f]', "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def title_from_url(url: str) -> str | None:
    slug = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    if not slug:
        return None
    return slug.replace("-", " ").strip()


def unique_ids(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
