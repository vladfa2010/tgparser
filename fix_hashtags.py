#!/usr/bin/env python3
"""
Retroactively extract hashtags from existing posts.

Problem: Before commit 6981b61, hashtag regex was #\w+ which only matched
Latin characters. Cyrillic hashtags (#россия, #сбер, #пмэф) were saved as [].

This script:
  1. Finds posts with empty/null hashtags
  2. Extracts hashtags from post text using #\S+ (supports Cyrillic)
  3. Updates the hashtags column

Usage:
    # Local (with .env or exported vars)
    python fix_hashtags.py

    # Docker
    docker compose run --rm -e DATABASE_URL=... parser python fix_hashtags.py

    # Render (one-time job)
    # Set TG_STRING_SESSION not needed — only DB access required
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
# Remove +asyncpg for sync connection
SYNC_URL = DATABASE_URL.replace("+asyncpg", "")

HASHTAG_RE = re.compile(r"#\S+")
BATCH_SIZE = 500


def extract_hashtags(text: str) -> list:
    """Extract hashtags from text. Supports Cyrillic, Latin, symbols."""
    if not text:
        return []
    return HASHTAG_RE.findall(text)


def get_stats(conn):
    """Show current hashtag statistics."""
    total = conn.execute(text("SELECT COUNT(*) FROM posts")).scalar()

    empty = conn.execute(
        text(
            """
        SELECT COUNT(*) FROM posts
        WHERE hashtags IS NULL
           OR hashtags = '[]'
           OR json_typeof(hashtags) != 'array'
    """
        )
    ).scalar()

    tagged = conn.execute(
        text(
            """
        SELECT COUNT(*) FROM posts
        WHERE json_typeof(hashtags) = 'array'
          AND json_array_length(hashtags) > 0
    """
        )
    ).scalar()

    # Sample of Cyrillic posts that should have tags
    sample = conn.execute(
        text(
            """
        SELECT telegram_message_id, LEFT(text, 60), hashtags
        FROM posts
        WHERE hashtags = '[]'
          AND text LIKE '%#%'
        LIMIT 5
    """
        )
    ).fetchall()

    return {"total": total, "empty": empty, "tagged": tagged, "sample": sample}


def process_batch(conn, rows):
    """Update a batch of posts with extracted hashtags."""
    updated = 0
    skipped = 0

    for row in rows:
        post_id = row[0]
        text_content = row[1] or ""
        current_tags = row[2]

        # Extract fresh hashtags from text
        new_tags = extract_hashtags(text_content)

        if not new_tags:
            skipped += 1
            continue

        # Update database
        conn.execute(
            text("UPDATE posts SET hashtags = :tags WHERE id = :id"),
            {"tags": json.dumps(new_tags), "id": post_id},
        )
        updated += 1

    return updated, skipped


def main():
    if not SYNC_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    print("=" * 60)
    print("HASHTAG RETROACTIVE FIX")
    print("=" * 60)
    print(f"DB: {SYNC_URL.split('@')[-1] if '@' in SYNC_URL else 'local'}")
    print(f"Regex: {HASHTAG_RE.pattern}")
    print(f"Batch size: {BATCH_SIZE}")
    print()

    engine = create_engine(SYNC_URL)

    with engine.connect() as conn:
        # --- BEFORE stats ---
        print("--- BEFORE ---")
        before = get_stats(conn)
        print(f"  Total posts:     {before['total']}")
        print(f"  Empty hashtags:  {before['empty']}")
        print(f"  Tagged posts:    {before['tagged']}")
        print()

        if before["sample"]:
            print("  Sample posts with #text but [] hashtags:")
            for row in before["sample"]:
                print(f"    ID {row[0]}: {row[1]}... | tags={row[2]}")
            print()

        if before["empty"] == 0:
            print("No posts to fix. Exiting.")
            return

        confirm = input(f"Fix {before['empty']} posts? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        print()
        print("Processing...")

        # --- Main loop ---
        total_updated = 0
        total_skipped = 0
        offset = 0

        while True:
            # Fetch batch of posts with empty/null hashtags
            rows = conn.execute(
                text(
                    """
                    SELECT id, text, hashtags
                    FROM posts
                    WHERE hashtags IS NULL
                       OR hashtags = '[]'
                       OR json_typeof(hashtags) != 'array'
                    ORDER BY id
                    LIMIT :limit OFFSET :offset
                """
                ),
                {"limit": BATCH_SIZE, "offset": offset},
            ).fetchall()

            if not rows:
                break

            updated, skipped = process_batch(conn, rows)
            conn.commit()

            total_updated += updated
            total_skipped += skipped
            offset += len(rows)

            print(
                f"  Batch {offset // BATCH_SIZE}: "
                f"+{updated} fixed, {skipped} no-tags, "
                f"total processed: {offset}"
            )

        # --- AFTER stats ---
        print()
        print("--- AFTER ---")
        after = get_stats(conn)
        print(f"  Total posts:     {after['total']}")
        print(f"  Empty hashtags:  {after['empty']}")
        print(f"  Tagged posts:    {after['tagged']}")
        print()
        print(f"Posts fixed: {total_updated}")
        print(f"Posts still empty (no # in text): {total_skipped}")
        print()
        print("Done!")


if __name__ == "__main__":
    start = datetime.now(timezone.utc)
    main()
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    print(f"Time: {elapsed:.1f}s")
