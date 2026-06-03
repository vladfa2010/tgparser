#!/usr/bin/env python3
"""
Parser for @markettwits Telegram channel
Saves posts to PostgreSQL database
Docker-ready: supports env vars, scheduled mode, persistent sessions
"""
import asyncio
import hashlib
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, select
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from telethon import TelegramClient
from telethon.tl.types import Message

load_dotenv()

# ─── Config ──────────────────────────────────────────────────
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION = os.getenv("TG_SESSION", "/app/sessions/markettwits_session")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://tgparser:tgparser_secret@postgres/tgparser")
CHANNEL_USERNAME = "markettwits"
SCHEDULE_MODE = os.getenv("SCHEDULE_MODE", "0") == "1"
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "300"))
HISTORY = os.getenv("HISTORY", "0") == "1"
LIMIT = os.getenv("LIMIT")
LIMIT = int(LIMIT) if LIMIT else None

# ─── Validation ──────────────────────────────────────────────
if not TG_API_ID or TG_API_ID == 0:
    print("ERROR: Set TG_API_ID env var (get it from https://my.telegram.org/apps)")
    sys.exit(1)
if not TG_API_HASH:
    print("ERROR: Set TG_API_HASH env var")
    sys.exit(1)

# ─── Database ────────────────────────────────────────────────
Base = declarative_base()
engine = create_async_engine(DATABASE_URL)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Channel(Base):
    __tablename__ = "channels"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True)
    username = Column(String(255), unique=True, index=True)
    title = Column(String(500))
    description = Column(Text)
    subscriber_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    last_parsed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    channel_id = Column(Integer, index=True)
    telegram_message_id = Column(BigInteger)
    text = Column(Text)
    text_hash = Column(String(64), index=True)
    views_count = Column(Integer, default=0)
    forwards_count = Column(Integer, default=0)
    replies_count = Column(Integer, default=0)
    hashtags = Column(JSON, default=list)
    mentions = Column(JSON, default=list)
    urls = Column(JSON, default=list)
    forward_from = Column(String(255))
    has_media = Column(Boolean, default=False)
    media_type = Column(String(50))
    published_at = Column(DateTime(timezone=True))
    edited_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("channel_id", "telegram_message_id"),)


class ParseLog(Base):
    __tablename__ = "parse_logs"
    id = Column(Integer, primary_key=True)
    channel_id = Column(Integer)
    posts_parsed = Column(Integer, default=0)
    posts_new = Column(Integer, default=0)
    duration_ms = Column(Integer)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))


# ─── Helpers ─────────────────────────────────────────────────
def extract_hashtags(text: str) -> list:
    return re.findall(r'#\w+', text) if text else []


def extract_mentions(text: str) -> list:
    return re.findall(r'@\w+', text) if text else []


def extract_urls(text: str) -> list:
    return re.findall(r'https?://[^\s]+', text) if text else []


def make_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest() if text else ""


def get_media_type(message: Message) -> Optional[str]:
    if not message.media:
        return None
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.document:
        return "document"
    return "other"


# ─── Parser ──────────────────────────────────────────────────
class MarketTwitsParser:
    def __init__(self):
        os.makedirs(os.path.dirname(TG_SESSION) or ".", exist_ok=True)
        self.client = TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)

    async def init_db(self):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def sync_channel(self, session: AsyncSession) -> int:
        entity = await self.client.get_entity(CHANNEL_USERNAME)

        result = await session.execute(
            select(Channel).where(Channel.username == CHANNEL_USERNAME)
        )
        channel = result.scalar_one_or_none()

        if not channel:
            channel = Channel(
                telegram_id=entity.id,
                username=CHANNEL_USERNAME,
                title=entity.title,
                description=getattr(entity, "about", None),
                subscriber_count=getattr(entity, "participants_count", 0),
            )
            session.add(channel)
            await session.flush()
        else:
            channel.title = entity.title
            channel.subscriber_count = getattr(entity, "participants_count", 0)

        return channel.id

    async def parse(self, limit: Optional[int] = None, history: bool = False):
        start_time = time.time()
        log = ParseLog(started_at=datetime.utcnow())

        async with async_session() as session:
            try:
                channel_db_id = await self.sync_channel(session)
                log.channel_id = channel_db_id

                # Incremental: resume from last parsed message
                last_id = 0
                if not history:
                    result = await session.execute(
                        select(Post)
                        .where(Post.channel_id == channel_db_id)
                        .order_by(Post.telegram_message_id.desc())
                        .limit(1)
                    )
                    last_post = result.scalar_one_or_none()
                    if last_post:
                        last_id = last_post.telegram_message_id
                        print(f"  Resuming from message_id {last_id}")

                entity = await self.client.get_entity(CHANNEL_USERNAME)
                parsed_count = 0
                new_count = 0

                async for message in self.client.iter_messages(
                    entity, limit=limit, min_id=last_id if not history else 0
                ):
                    if not message.text and not message.media:
                        continue

                    parsed_count += 1

                    # Skip duplicates
                    result = await session.execute(
                        select(Post).where(
                            (Post.channel_id == channel_db_id)
                            & (Post.telegram_message_id == message.id)
                        )
                    )
                    if result.scalar_one_or_none():
                        continue

                    text = message.text or ""
                    forward_from = None
                    if message.forward and message.forward.chat:
                        forward_from = (
                            message.forward.chat.username
                            or message.forward.chat.title
                        )

                    post = Post(
                        channel_id=channel_db_id,
                        telegram_message_id=message.id,
                        text=text,
                        text_hash=make_text_hash(text),
                        views_count=message.views or 0,
                        forwards_count=message.forwards or 0,
                        replies_count=message.replies.replies
                        if message.replies
                        else 0,
                        hashtags=extract_hashtags(text),
                        mentions=extract_mentions(text),
                        urls=extract_urls(text),
                        forward_from=forward_from,
                        has_media=message.media is not None,
                        media_type=get_media_type(message),
                        published_at=message.date,
                        edited_at=message.edit_date,
                    )
                    session.add(post)
                    new_count += 1

                    if new_count % 100 == 0:
                        await session.commit()
                        print(f"  Saved {new_count} new posts...")

                await session.commit()

                # Update channel
                result = await session.execute(
                    select(Channel).where(Channel.id == channel_db_id)
                )
                channel = result.scalar_one()
                channel.last_parsed_at = datetime.utcnow()
                await session.commit()

                # Save log
                log.posts_parsed = parsed_count
                log.posts_new = new_count
                log.duration_ms = int((time.time() - start_time) * 1000)
                log.finished_at = datetime.utcnow()
                session.add(log)
                await session.commit()

                print(
                    f"Done! Parsed: {parsed_count}, New: {new_count}, "
                    f"Time: {log.duration_ms}ms"
                )

            except Exception as e:
                log.error_message = str(e)
                log.finished_at = datetime.utcnow()
                log.duration_ms = int((time.time() - start_time) * 1000)
                session.add(log)
                await session.commit()
                raise

    async def run_once(self):
        await self.init_db()
        await self.client.start()
        me = await self.client.get_me()
        print(f"Connected as {me.first_name} (@{me.username})")
        try:
            await self.parse(limit=LIMIT, history=HISTORY)
        finally:
            await self.client.disconnect()

    async def run_scheduled(self):
        await self.init_db()
        await self.client.start()
        me = await self.client.get_me()
        print(f"Scheduler connected as {me.first_name}")
        print(f"Parsing every {INTERVAL_SEC} seconds")

        try:
            while True:
                print(f"\n[{datetime.utcnow().isoformat()}] Starting parse...")
                try:
                    await self.parse(limit=None, history=False)
                except Exception as e:
                    print(f"Parse error: {e}")
                print(f"Sleeping {INTERVAL_SEC}s...")
                await asyncio.sleep(INTERVAL_SEC)
        finally:
            await self.client.disconnect()


async def main():
    parser = MarketTwitsParser()
    if SCHEDULE_MODE:
        await parser.run_scheduled()
    else:
        await parser.run_once()


if __name__ == "__main__":
    asyncio.run(main())
