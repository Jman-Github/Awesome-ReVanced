#!/usr/bin/env python3
"""Update README repo activity badges based on latest GitHub data."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib import error as urlerror
from urllib import request

STATUS_MAP = {
    "active": {"emoji": "\U0001f7e2", "label": "Active"},
    "partially": {"emoji": "\U0001f7e1", "label": "Partially maintained"},
    "inactive": {"emoji": "\U0001f534", "label": "Inactive"},
    "archived": {"emoji": "\U0001f4e5", "label": "Archived"},
}

GITHUB_RE = re.compile(r"https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)")
LINE_RE = re.compile(r"^(?P<prefix>\s*-\s*)(?:\S+\s+)?\*\*[^*]+\*\*:(?P<ws>\s*)(?P<rest>.+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", default="README.md", help="Path to the markdown file to update")
    parser.add_argument("--dry-run", action="store_true", help="Only print changes without writing the file")
    parser.add_argument("--active-days", type=int, default=60, help="Days since last push to keep repo Active")
    parser.add_argument(
        "--partially-days",
        type=int,
        default=180,
        help="Days since last push to keep repo Partially maintained",
    )
    parser.add_argument(
        "--max-repos",
        type=int,
        help="Optional cap for number of repositories to process (useful for local testing)",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level (default: INFO)")
    return parser.parse_args()


@dataclass
class RepoResult:
    repo: str
    status: str
    pushed_at: Optional[str]
    archived: bool


class GithubClient:
    def __init__(self, token: Optional[str] = None) -> None:
        self._token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self._cache: Dict[str, Optional[dict]] = {}

    def fetch(self, slug: str) -> Optional[dict]:
        if slug in self._cache:
            return self._cache[slug]
        url = f"https://api.github.com/repos/{slug}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "awesome-revanced-activity-script",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = request.Request(url, headers=headers)
        try:
            with request.urlopen(req) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            logging.error("GitHub request failed for %s: %s", slug, exc)
            payload = None
        except urlerror.URLError as exc:
            logging.error("Unable to reach GitHub for %s: %s", slug, exc)
            payload = None
        self._cache[slug] = payload
        return payload


def classify_repo(metadata: dict, now: dt.datetime, active_days: int, partially_days: int) -> RepoResult:
    slug = metadata.get("full_name")
    archived = metadata.get("archived", False)
    pushed_at = metadata.get("pushed_at")
    if archived:
        status = "archived"
    else:
        pushed = None
        if pushed_at:
            try:
                pushed = dt.datetime.strptime(pushed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            except ValueError:
                logging.warning("Unexpected pushed_at for %s: %s", slug, pushed_at)
        if pushed is None:
            status = "inactive"
        else:
            delta = now - pushed
            if delta.days <= active_days:
                status = "active"
            elif delta.days <= partially_days:
                status = "partially"
            else:
                status = "inactive"
    return RepoResult(repo=slug, status=status, pushed_at=pushed_at, archived=archived)


def find_repo_slug(line: str) -> Optional[str]:
    match = GITHUB_RE.search(line)
    if not match:
        return None
    owner, repo = match.group("owner"), match.group("repo")
    return f"{owner}/{repo}" if repo else None


def apply_status_to_line(line: str, status: str) -> str:
    status_info = STATUS_MAP[status]
    match = LINE_RE.match(line)
    if not match:
        return line
    ws = match.group("ws") or " "
    rest = match.group("rest")
    return f"{match.group('prefix')}{status_info['emoji']} **{status_info['label']}:**{ws}{rest}"


def iter_repo_lines(lines: Iterable[str]):
    for idx, line in enumerate(lines):
        slug = find_repo_slug(line)
        if slug:
            yield idx, line, slug


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")
    path = Path(args.readme)
    if not path.is_file():
        logging.error("README not found at %s", path)
        return 1
    client = GithubClient()
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    now = dt.datetime.now(tz=dt.timezone.utc)

    processed = 0
    updates = []

    for idx, line, slug in iter_repo_lines(lines):
        if args.max_repos is not None and processed >= args.max_repos:
            logging.info("Reached max repo limit (%s); stopping early", args.max_repos)
            break
        metadata = client.fetch(slug)
        processed += 1
        if not metadata:
            logging.warning("Skipping %s; could not fetch metadata", slug)
            continue
        result = classify_repo(metadata, now, args.active_days, args.partially_days)
        new_line = apply_status_to_line(line, result.status)
        if new_line != line:
            lines[idx] = new_line
            updates.append((slug, result.status))
            logging.info("Updating %s => %s", slug, result.status)

    if not updates:
        logging.info("No updates needed")
        return 0

    new_contents = "\n".join(lines) + "\n"

    if args.dry_run:
        logging.info("Dry run: %s lines would change", len(updates))
        return 0

    path.write_text(new_contents, encoding="utf-8")
    logging.info("Updated %s lines", len(updates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
