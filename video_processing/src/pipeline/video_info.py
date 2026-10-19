"""
Per-video metadata from the V3C info JSONs: title, the uploader's description, tags, categories.

Only V3C has these. There are two formats, and they are not the same data:

  * `V3Cmeta/info/00001.json` -- Vimeo scrape. HTML description, tags, categories, channel slug.
  * `V3Cmeta/infoV3C.tgz` (`info/00001.info.json`) -- yt-dlp dump. Plain-text description and the
    uploader's display name, but no tags and no categories in any of its 28,450 files. Where a video
    has no description it holds Vimeo's page filler instead: `This is "<title>" by <uploader> on
    Vimeo, the home for high quality videos and the people who love them.`

Both parse into the same VideoInfo, the filler is dropped, and datasets.json lists the folder first.
Ingest warns when only the archive is there, because the tags are then missing without any error.
"""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import custom_logger
from psycopg2.extras import execute_values

VIMEO_FILLER = re.compile(
    r'^This is ".*" by .* on Vimeo, the home for high quality videos and the people who love them\.?$',
    re.S)
BLOCK_BREAK = re.compile(r"<\s*(br|/p|/div|/li|/h[1-6])\b[^>]*>", re.I)
TAG = re.compile(r"<[^>]+>")
SPACES = re.compile(r"[ \t\r\f\v]+")
BLANK_LINES = re.compile(r"\n\s*\n+")

UPSERT = """
    INSERT INTO video_info (video_id, title, description, channel, tags, categories, upload_date)
    VALUES %s
    ON CONFLICT (video_id) DO UPDATE
        SET title       = EXCLUDED.title,
            description = EXCLUDED.description,
            channel     = EXCLUDED.channel,
            tags        = EXCLUDED.tags,
            categories  = EXCLUDED.categories,
            upload_date = EXCLUDED.upload_date
        WHERE (video_info.title, video_info.description, video_info.tags)
                  IS DISTINCT FROM (EXCLUDED.title, EXCLUDED.description, EXCLUDED.tags)
           OR (video_info.channel, video_info.categories, video_info.upload_date)
                  IS DISTINCT FROM (EXCLUDED.channel, EXCLUDED.categories, EXCLUDED.upload_date)
    RETURNING video_id;
"""

# The embedding was made from the old text, so a changed row needs a new one.
DROP_STALE_EMBEDDINGS = "DELETE FROM video_info_embedding WHERE video_id = ANY(%s);"

SELECT_VIDEOS = "SELECT video_id FROM videos WHERE collection = %s;"


@dataclass
class VideoInfo:
    title: str = ""
    description: str = ""
    channel: str = ""
    tags: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    upload_date: date | None = None

    def passage(self) -> str:
        """
        What gets embedded. Title and tags first, because the text model truncates at
        TEXT_MAX_TOKENS and a long description should lose its tail, not the title.
        """
        parts = [self.title]
        if self.tags:
            parts.append(", ".join(self.tags))
        parts.append(self.description)
        return "\n".join(p for p in parts if p).strip()


def clean_description(text: str) -> str:
    """HTML to plain text, keeping paragraph and line breaks, without Vimeo's filler."""
    text = BLOCK_BREAK.sub("\n", text or "")
    text = html.unescape(TAG.sub("", text))
    lines = [SPACES.sub(" ", line).strip() for line in text.split("\n")]
    text = BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return "" if VIMEO_FILLER.match(text) else text


def _clean_list(values) -> list:
    """Lowercased, trimmed, deduplicated, order kept."""
    seen, out = set(), []
    for value in values or []:
        value = str(value).strip().lower()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _clean_categories(values) -> list:
    """
    `/categories/sports/outdoorsports/videos` -> `sports`, `sports/outdoorsports`.

    Vimeo's categories are nested (74 distinct paths in V3C). Storing every ancestor is what makes
    a filter on `sports` also find the 1,505 videos filed under `sports/outdoorsports`.
    """
    expanded = []
    for value in values or []:
        parts = [p for p in str(value).strip().lower().split("/") if p]
        if parts[:1] == ["categories"]:
            parts = parts[1:]
        if parts[-1:] == ["videos"]:
            parts = parts[:-1]
        expanded += ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return _clean_list(expanded)


def _parse_date(value) -> date | None:
    """`2014-05-31 10:32:38` (Vimeo scrape) or `20140531` (yt-dlp)."""
    value = str(value or "").strip()
    for fmt, length in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(value[:length], fmt).date()
        except ValueError:
            continue
    return None


def parse_info(text: str) -> VideoInfo:
    data = json.loads(text)
    return VideoInfo(
        title=(data.get("title") or "").strip(),
        description=clean_description(data.get("description") or ""),
        channel=(data.get("channel") or data.get("uploader") or "").strip(),
        tags=_clean_list(data.get("tags")),
        categories=_clean_categories(data.get("categories")),
        upload_date=_parse_date(data.get("uploadDate") or data.get("upload_date")),
    )


def ingest_info(conn, dataset, video_ids=None) -> int:
    """
    Loads the info of every video of `dataset` that is already in `videos`. Returns how many rows
    were inserted or changed. Re-running it costs a read of the files and changes nothing.
    """
    logger = custom_logger.get_logger("video_info")
    if dataset.info is None:
        if dataset.info_candidates:
            logger.warning(f"{dataset.name}: no info files found, looked in "
                           f"{', '.join(dataset.info_candidates)}")
        return 0
    if dataset.info.is_archive:
        logger.warning(f"{dataset.name}: reading info from {dataset.info.path}, which has no tags "
                       f"or categories. The extracted info/ folder has them.")

    with conn.cursor() as cur:
        cur.execute(SELECT_VIDEOS, (dataset.name,))
        wanted = {r[0] for r in cur.fetchall()}
    if video_ids is not None:
        wanted &= set(video_ids)
    if not wanted:
        return 0

    rows, failed = [], []
    for video_id, text in dataset.info.items(wanted=wanted):
        try:
            info = parse_info(text)
        except (ValueError, TypeError) as e:
            failed.append(video_id)
            logger.warning(f"{video_id}: unreadable info file ({e})")
            continue
        rows.append((video_id, info.title, info.description, info.channel, info.tags,
                     info.categories, info.upload_date))

    changed = []
    with conn.cursor() as cur:
        if rows:
            changed = [r[0] for r in execute_values(cur, UPSERT, rows, page_size=1000, fetch=True)]
            cur.execute(DROP_STALE_EMBEDDINGS, (changed,))
    conn.commit()

    missing = len(wanted) - len(rows) - len(failed)
    logger.info(f"{dataset.name}: info for {len(rows)}/{len(wanted)} video(s), {len(changed)} new "
                f"or changed" + (f", {missing} without an info file" if missing else ""))
    return len(changed)
