from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .core import RteDownloadError, download_with_ytdlp, resolve_stream


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rte-download",
        description="Download videos from RTÉ Archives and RTÉ News.",
    )
    parser.add_argument("url", help="RTÉ Archives or RTÉ News page URL")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path.home() / "Downloads",
        help="Directory for downloaded files (default: ~/Downloads)",
    )
    parser.add_argument(
        "--filename-template",
        default="{date} - {title} [{id}]",
        help="Filename stem template using {date}, {title}, and {id}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the stream and print what would be downloaded.",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help="Print only the resolved HLS playlist URL.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity.",
    )
    parser.add_argument("--clip-id", help="Select a clip by ID on an RTÉ News article.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def choose_clip(clips: tuple[tuple[str, str], ...]) -> str:
    print("This article contains multiple clips.", file=sys.stderr)
    for number, (clip_id, title) in enumerate(clips, start=1):
        label = f"{title} [{clip_id}]" if title else clip_id
        print(f"  {number}. {label}", file=sys.stderr)

    if not sys.stdin.isatty():
        raise RteDownloadError("Run again with --clip-id and one of the IDs above.")

    while True:
        print(f"Select a clip (1–{len(clips)}): ", end="", file=sys.stderr, flush=True)
        try:
            selection = int(input())
        except (EOFError, KeyboardInterrupt) as exc:
            raise RteDownloadError("Clip selection cancelled.") from exc
        except ValueError:
            continue
        if 1 <= selection <= len(clips):
            return clips[selection - 1][0]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    try:
        stream = resolve_stream(
            args.url,
            filename_template=args.filename_template,
            clip_id=args.clip_id,
            select_clip=choose_clip,
        )
    except RteDownloadError as exc:
        print(f"rte-download: {exc}", file=sys.stderr)
        return 1

    if args.print_url:
        print(stream.stream_url)
        return 0

    if args.dry_run:
        print(f"Page title: {stream.page.title}")
        print(f"Playlist ID: {stream.playlist_id}")
        print(f"Playlist API: {stream.playlist_url}")
        print(f"HLS stream: {stream.stream_url}")
        print(f"Output: {args.output_dir.expanduser() / (stream.filename_stem + '.%(ext)s')}")
        return 0

    return download_with_ytdlp(stream, args.output_dir)
