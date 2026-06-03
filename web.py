#!/usr/bin/env python3
"""
Web dashboard for tgparser database.
FastAPI + Jinja2 — shows posts, stats, search.
"""
import os
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint
)

# ─── Database URL ────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# ─── Models (same as parser) ─────────────────────────────────
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


# ─── FastAPI App ─────────────────────────────────────────────
app = FastAPI(title="TG Parser Dashboard")

# In-memory template (no files needed)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>TG Parser Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f23; color: #e0e0e0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #00d4aa; margin-bottom: 5px; }
        .subtitle { color: #888; margin-bottom: 25px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 25px; }
        .stat { background: #1a1a2e; border-radius: 12px; padding: 20px; border: 1px solid #2a2a4e; }
        .stat-value { font-size: 32px; font-weight: bold; color: #00d4aa; }
        .stat-label { color: #888; font-size: 13px; margin-top: 5px; }
        .filters { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        input, select, button { background: #1a1a2e; color: #e0e0e0; border: 1px solid #2a2a4e; padding: 10px 15px; border-radius: 8px; font-size: 14px; }
        button { background: #00d4aa; color: #0f0f23; border: none; cursor: pointer; font-weight: bold; }
        button:hover { background: #00b894; }
        .posts { display: flex; flex-direction: column; gap: 10px; }
        .post { background: #1a1a2e; border-radius: 12px; padding: 15px; border: 1px solid #2a2a4e; }
        .post-header { display: flex; gap: 15px; margin-bottom: 8px; font-size: 13px; color: #888; flex-wrap: wrap; }
        .post-header span { display: flex; align-items: center; gap: 4px; }
        .post-text { color: #e0e0e0; line-height: 1.6; }
        .post-tags { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
        .tag { background: #2a2a4e; color: #00d4aa; padding: 3px 10px; border-radius: 20px; font-size: 12px; }
        .pagination { display: flex; gap: 10px; justify-content: center; margin-top: 20px; align-items: center; }
        .pagination a { color: #00d4aa; text-decoration: none; padding: 8px 16px; background: #1a1a2e; border-radius: 8px; }
        .pagination a:hover { background: #2a2a4e; }
        .search-box { flex: 1; min-width: 200px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 TG Parser Dashboard</h1>
        <p class="subtitle">{{ channel_title }} @{{ channel_username }} | Последнее обновление: {{ last_parsed }}</p>
        
        <div class="stats">
            <div class="stat">
                <div class="stat-value">{{ total_posts }}</div>
                <div class="stat-label">Всего постов</div>
            </div>
            <div class="stat">
                <div class="stat-value">{{ today_posts }}</div>
                <div class="stat-label">За сегодня</div>
            </div>
            <div class="stat">
                <div class="stat-value">{{ week_posts }}</div>
                <div class="stat-label">За неделю</div>
            </div>
            <div class="stat">
                <div class="stat-value">{{ avg_views }}</div>
                <div class="stat-label">Средний reach</div>
            </div>
            <div class="stat">
                <div class="stat-value">{{ total_parses }}</div>
                <div class="stat-label">Парсингов</div>
            </div>
        </div>
        
        <form class="filters" method="get">
            <input type="text" name="search" placeholder="Поиск по тексту..." value="{{ search }}" class="search-box">
            <select name="sort">
                <option value="new" {% if sort == 'new' %}selected{% endif %}>Новые</option>
                <option value="views" {% if sort == 'views' %}selected{% endif %}>По просмотрам</option>
            </select>
            <button type="submit">🔍 Искать</button>
            {% if search %}<a href="/"><button type="button">✕ Сброс</button></a>{% endif %}
        </form>
        
        <div class="posts">
            {% for post in posts %}
            <div class="post">
                <div class="post-header">
                    <span>🆔 {{ post.telegram_message_id }}</span>
                    <span>👁 {{ post.views_count }}</span>
                    <span>🔄 {{ post.forwards_count }}</span>
                    <span>💬 {{ post.replies_count }}</span>
                    <span>📅 {{ post.published_at }}</span>
                    {% if post.forward_from %}<span>↪️ {{ post.forward_from }}</span>{% endif %}
                </div>
                <div class="post-text">{{ post.text or "(медиа без текста)" }}</div>
                {% if post.hashtags %}
                <div class="post-tags">
                    {% for tag in post.hashtags %}<span class="tag">{{ tag }}</span>{% endfor %}
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        
        <div class="pagination">
            {% if page > 1 %}<a href="?page={{ page - 1 }}&search={{ search }}&sort={{ sort }}">← Назад</a>{% endif %}
            <span>Страница {{ page }} из {{ total_pages }}</span>
            {% if page < total_pages %}<a href="?page={{ page + 1 }}&search={{ search }}&sort={{ sort }}">Вперед →</a>{% endif %}
        </div>
    </div>
</body>
</html>
"""

# Manual template rendering
from jinja2 import Template


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        pass  # Tables already exist from parser


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
    sort: str = Query("new")
):
    async with async_session() as session:
        # Stats
        result = await session.execute(select(func.count()).select_from(Post))
        total_posts = result.scalar()
        
        result = await session.execute(
            select(func.count()).select_from(Post)
            .where(Post.published_at > datetime.utcnow() - timedelta(days=1))
        )
        today_posts = result.scalar()
        
        result = await session.execute(
            select(func.count()).select_from(Post)
            .where(Post.published_at > datetime.utcnow() - timedelta(days=7))
        )
        week_posts = result.scalar()
        
        result = await session.execute(select(func.avg(Post.views_count)).select_from(Post))
        avg_views = int(result.scalar() or 0)
        
        result = await session.execute(select(func.count()).select_from(ParseLog))
        total_parses = result.scalar()
        
        # Channel info
        result = await session.execute(select(Channel).where(Channel.username == "markettwits"))
        channel = result.scalar_one_or_none()
        channel_title = channel.title if channel else "MarketTwits"
        last_parsed = channel.last_parsed_at.strftime("%Y-%m-%d %H:%M") if channel and channel.last_parsed_at else "—"
        
        # Posts query
        per_page = 20
        query = select(Post)
        
        if search:
            query = query.where(Post.text.ilike(f"%{search}%"))
        
        if sort == "views":
            query = query.order_by(Post.views_count.desc())
        else:
            query = query.order_by(Post.published_at.desc())
        
        # Count for pagination
        count_query = select(func.count()).select_from(Post)
        if search:
            count_query = count_query.where(Post.text.ilike(f"%{search}%"))
        result = await session.execute(count_query)
        filtered_total = result.scalar()
        total_pages = max(1, (filtered_total + per_page - 1) // per_page)
        
        # Fetch posts
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(query)
        posts = result.scalars().all()
        
        # Format posts for template
        posts_data = []
        for p in posts:
            posts_data.append({
                "telegram_message_id": p.telegram_message_id,
                "views_count": f"{p.views_count:,}".replace(",", " "),
                "forwards_count": p.forwards_count,
                "replies_count": p.replies_count,
                "published_at": p.published_at.strftime("%d.%m %H:%M") if p.published_at else "—",
                "forward_from": p.forward_from,
                "text": (p.text or "(медиа без текста)").replace("\n", "<br>"),
                "hashtags": p.hashtags or [],
            })
        
        template = Template(HTML_TEMPLATE)
        html = template.render(
            channel_title=channel_title,
            channel_username="markettwits",
            last_parsed=last_parsed,
            total_posts=f"{total_posts:,}".replace(",", " ") if total_posts else "0",
            today_posts=today_posts or 0,
            week_posts=week_posts or 0,
            avg_views=f"{avg_views:,}".replace(",", " ") if avg_views else "0",
            total_parses=total_parses or 0,
            posts=posts_data,
            page=page,
            total_pages=total_pages,
            search=search,
            sort=sort,
        )
        
        return HTMLResponse(content=html)


@app.get("/api/posts")
async def api_posts(
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    search: str = Query("")
):
    """JSON API for posts"""
    async with async_session() as session:
        query = select(Post).order_by(Post.published_at.desc()).offset((page - 1) * limit).limit(limit)
        if search:
            query = query.where(Post.text.ilike(f"%{search}%"))
        
        result = await session.execute(query)
        posts = result.scalars().all()
        
        return {
            "total": len(posts),
            "posts": [
                {
                    "id": p.telegram_message_id,
                    "text": p.text,
                    "views": p.views_count,
                    "hashtags": p.hashtags,
                    "published": p.published_at.isoformat() if p.published_at else None,
                }
                for p in posts
            ]
        }


@app.get("/api/stats")
async def api_stats():
    """JSON API for stats"""
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(Post))
        total = result.scalar()
        
        result = await session.execute(
            select(func.count()).select_from(Post)
            .where(Post.published_at > datetime.utcnow() - timedelta(days=1))
        )
        today = result.scalar()
        
        return {"total_posts": total, "today_posts": today}
