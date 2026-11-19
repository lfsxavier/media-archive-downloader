# rte-download

`rte-download` is a small command-line downloader for videos on RTÉ Archives and RTÉ News.
It resolves the RTÉ page to the underlying HLS playlist and hands that
playlist to `yt-dlp`.

## Requirements

- Python 3.9+
- `yt-dlp`
- `ffmpeg`

On macOS with Homebrew:

```sh
brew install yt-dlp ffmpeg
```

## Usage

```sh
rte-download 'https://www.rte.ie/archives/2019/0914/1075232-lotto-syndicate-celebrate/'
```

By default, downloads go to `~/Downloads`.

Useful options:

```sh
rte-download URL --dry-run
rte-download URL --print-url
rte-download URL --output-dir ~/Movies/Archives
rte-download URL --verbose
```

Archive article URLs and RTÉ Dustin player URLs are both supported:

```sh
rte-download 'https://www.rte.ie/media-embed/dustin/?id=11080866&thumbnail=0012aad5&subheader=off'
```

## RTÉ News clips

For an article with multiple clips, the command lists their titles and IDs.
Choose a clip at the terminal prompt or pass its ID with `--clip-id` when
running non-interactively.

```sh
rte-download 'https://www.rte.ie/news/ireland/2026/0625/1580210-weather-ireland/' --clip-id 22624363 --dry-run
```

## How RTÉ Archive pages are resolved

Older RTÉ Archive pages use the numeric ID in the page URL directly:

```text
https://www.rte.ie/rteavgen/getplaylist/?id=<archive_id>
```

Newer archive pages can have a different player ID. That ID is embedded in the
page HTML, commonly as:

```html
<meta name="av" content="uri:avms:11080866">
```

The downloader reads the page HTML, tries discovered player IDs first, then
falls back to the archive URL ID for legacy pages.

## Development

Run the test suite with:

```sh
python3 -m unittest
```

From a checkout, install the command in editable mode with:

```sh
python3 -m pip install -e .
```
