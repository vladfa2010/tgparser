#!/usr/bin/env python3
"""
Web dashboard for tgparser database.
FastAPI + Jinja2 — posts, stats, search, tags analytics.
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

# ─── Models ──────────────────────────────────────────────────
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


app = FastAPI(title="TG Parser Dashboard")

# ─── Jinja2 Setup ────────────────────────────────────────────
from jinja2 import Environment, BaseLoader

jinja_env = Environment(loader=BaseLoader())

BASE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{{ title }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f23; color: #e0e0e0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #00d4aa; margin-bottom: 5px; }
        .subtitle { color: #888; margin-bottom: 15px; }
        .nav { display: flex; gap: 5px; margin-bottom: 25px; background: #1a1a2e; border-radius: 10px; padding: 5px; width: fit-content; }
        .nav a { color: #888; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; }
        .nav a:hover { color: #e0e0e0; background: #2a2a4e; }
        .nav a.active { color: #0f0f23; background: #00d4aa; }
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
        .tag { background: #2a2a4e; color: #00d4aa; padding: 3px 10px; border-radius: 20px; font-size: 12px; text-decoration: none; }
        .tag:hover { background: #00d4aa; color: #0f0f23; }
        .pagination { display: flex; gap: 10px; justify-content: center; margin-top: 20px; align-items: center; }
        .pagination a { color: #00d4aa; text-decoration: none; padding: 8px 16px; background: #1a1a2e; border-radius: 8px; }
        .pagination a:hover { background: #2a2a4e; }
        .search-box { flex: 1; min-width: 200px; }
        .section-title { color: #00d4aa; font-size: 18px; margin: 20px 0 15px; }
        .tag-cloud { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 25px; }
        .tag-item { background: #1a1a2e; border: 1px solid #2a2a4e; border-radius: 20px; padding: 6px 16px; font-size: 14px; color: #e0e0e0; text-decoration: none; display: flex; align-items: center; gap: 8px; }
        .tag-item:hover { background: #00d4aa; color: #0f0f23; border-color: #00d4aa; }
        .tag-count { background: #2a2a4e; color: #00d4aa; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }
        .tag-item:hover .tag-count { background: #0f0f23; color: #00d4aa; }
        .tag-bar-row { display: flex; align-items: center; gap: 15px; margin-bottom: 10px; padding: 12px 15px; background: #1a1a2e; border-radius: 10px; border: 1px solid #2a2a4e; }
        .tag-name { min-width: 150px; font-weight: 600; color: #00d4aa; font-size: 14px; }
        .tag-bar-wrap { flex: 1; height: 28px; background: #0f0f23; border-radius: 6px; overflow: hidden; }
        .tag-bar { height: 100%; border-radius: 6px; transition: width 0.5s ease; display: flex; align-items: center; padding: 0 10px; font-size: 12px; font-weight: 600; color: #fff; }
        .tag-meta { min-width: 80px; text-align: right; color: #888; font-size: 12px; }
        .time-badge { background: #2a2a4e; color: #e0e0e0; padding: 4px 12px; border-radius: 6px; font-size: 13px; display: inline-block; margin-bottom: 15px; }
        .empty { color: #888; text-align: center; padding: 40px; font-style: italic; }
    </style>
</head>
<body>
    <div class="container">
        <h1>TG Parser Dashboard</h1>
        <p class="subtitle">{{ channel_title }} @markettwits | Last update: {{ last_parsed }}</p>
        <div class="nav">
            <a href="/" class="{% if active_tab == 'posts' %}active{% endif %}">Posts</a>
            <a href="/tags" class="{% if active_tab == 'tags' %}active{% endif %}">Tags 24h</a>
        </div>
        {% block content %}{% endblock %}
    </div>
</body>
</html>
"""

POSTS_TEMPLATE = """
<div class="stats">
    <div class="stat"><div class="stat-value">{{ total_posts }}</div><div class="stat-label">Total posts</div></div>
    <div class="stat"><div class="stat-value">{{ today_posts }}</div><div class="stat-label">Today</div></div>
    <div class="stat"><div class="stat-value">{{ week_posts }}</div><div class="stat-label">This week</div></div>
    <div class="stat"><div class="stat-value">{{ avg_views }}</div><div class="stat-label">Avg reach</div></div>
    <div class="stat"><div class="stat-value">{{ total_parses }}</div><div class="stat-label">Parses</div></div>
</div>
<form class="filters" method="get">
    <input type="text" name="search" placeholder="Search..." value="{{ search }}" class="search-box">
    <select name="sort">
        <option value="new" {% if sort == 'new' %}selected{% endif %}>New</option>
        <option value="views" {% if sort == 'views' %}selected{% endif %}>Views</option>
    </select>
    <button type="submit">Search</button>
    {% if search %}<a href="/"><button type="button">Clear</button></a>{% endif %}
</form>
<div class="posts">
    {% for post in posts %}
    <div class="post">
        <div class="post-header">
            <span>ID: {{ post.telegram_message_id }}</span>
            <span>views: {{ post.views_count }}</span>
            <span>fwd: {{ post.forwards_count }}</span>
            <span>replies: {{ post.replies_count }}</span>
            <span>date: {{ post.published_at }}</span>
            {% if post.forward_from %}<span>from: {{ post.forward_from }}</span>{% endif %}
        </div>
        <div class="post-text">{{ post.text | safe }}</div>
        {% if post.hashtags %}
        <div class="post-tags">
            {% for tag in post.hashtags %}<a href="/tags?tag={{ tag[1:] }}" class="tag">{{ tag }}</a>{% endfor %}
        </div>
        {% endif %}
    </div>
    {% endfor %}
</div>
<div class="pagination">
    {% if page > 1 %}<a href="?page={{ page - 1 }}&search={{ search }}&sort={{ sort }}">Prev</a>{% endif %}
    <span>Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}<a href="?page={{ page + 1 }}&search={{ search }}&sort={{ sort }}">Next</a>{% endif %}
</div>
"""

TAGS_TEMPLATE = """
<div class="stats">
    <div class="stat"><div class="stat-value">{{ tag_count }}</div><div class="stat-label">Unique tags</div></div>
    <div class="stat"><div class="stat-value">{{ total_tagged }}</div><div class="stat-label">Tagged posts</div></div>
    <div class="stat"><div class="stat-value">{{ top_tag }}</div><div class="stat-label">Top tag</div></div>
</div>
<div class="time-badge">Last 24h: {{ since }} - {{ now }}</div>
<div class="section-title">Tag Cloud</div>
<div class="tag-cloud">
    {% for t in cloud_tags %}
    <a href="/tags?tag={{ t.tag[1:] }}" class="tag-item">{{ t.tag }} <span class="tag-count">{{ t.count }}</span></a>
    {% endfor %}
</div>
<div class="section-title">Tag Ranking</div>
{% for t in tag_bars %}
<div class="tag-bar-row">
    <div class="tag-name">{{ t.tag }}</div>
    <div class="tag-bar-wrap">
        <div class="tag-bar" style="width:{{ t.pct }}%;background:{{ t.color }};">{{ t.count }} posts</div>
    </div>
    <div class="tag-meta">views: {{ t.total_views }}<br>avg: {{ t.avg_views }}</div>
</div>
{% endfor %}
"""

TAG_POSTS_TEMPLATE = """
<div class="section-title">Posts with #{{ tag }} ({{ post_count }})</div>
<a href="/tags">Back to all tags</a><br><br>
<div class="posts">
    {% for post in posts %}
    <div class="post">
        <div class="post-header">
            <span>ID: {{ post.telegram_message_id }}</span>
            <span>views: {{ post.views_count }}</span>
            <span>date: {{ post.published_at }}</span>
        </div>
        <div class="post-text">{{ post.text | safe }}</div>
        {% if post.hashtags %}
        <div class="post-tags">
            {% for t in post.hashtags %}<a href="/tags?tag={{ t[1:] }}" class="tag">{{ t }}</a>{% endfor %}
        </div>
        {% endif %}
    </div>
    {% endfor %}
</div>
"""

EMPTY_TEMPLATE = '<div class="empty">No data in last 24 hours</div>'

# ─── Helpers ─────────────────────────────────────────────────
async def get_channel_info(session):
    result = await session.execute(select(Channel).where(Channel.username == "markettwits"))
    channel = result.scalar_one_or_none()
    return (
        channel.title if channel else "MarketTwits",
        channel.last_parsed_at.strftime("%Y-%m-%d %H:%M") if channel and channel.last_parsed_at else "-"
    )


def render_base(title, channel_title, last_parsed, active_tab, content):
    base_tmpl = jinja_env.from_string(BASE_TEMPLATE)
    return base_tmpl.render(
        title=title,
        channel_title=channel_title,
        last_parsed=last_parsed,
        active_tab=active_tab
    ).replace('{% block content %}{% endblock %}', content)


# ─── / — Posts ───────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
    sort: str = Query("new")
):
    async with async_session() as session:
        channel_title, last_parsed = await get_channel_info(session)
        
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
        
        per_page = 20
        query = select(Post)
        if search:
            query = query.where(Post.text.ilike(f"%{search}%"))
        if sort == "views":
            query = query.order_by(Post.views_count.desc())
        else:
            query = query.order_by(Post.published_at.desc())
        
        count_query = select(func.count()).select_from(Post)
        if search:
            count_query = count_query.where(Post.text.ilike(f"%{search}%"))
        result = await session.execute(count_query)
        filtered_total = result.scalar()
        total_pages = max(1, (filtered_total + per_page - 1) // per_page)
        
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(query)
        posts = result.scalars().all()
        
        posts_data = []
        for p in posts:
            posts_data.append({
                "telegram_message_id": p.telegram_message_id,
                "views_count": f"{p.views_count:,}".replace(",", " "),
                "forwards_count": p.forwards_count,
                "replies_count": p.replies_count,
                "published_at": p.published_at.strftime("%d.%m %H:%M") if p.published_at else "-",
                "forward_from": p.forward_from,
                "text": (p.text or "(media, no text)").replace("\n", "<br>"),
                "hashtags": p.hashtags or [],
            })
        
        tmpl = jinja_env.from_string(POSTS_TEMPLATE)
        content = tmpl.render(
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
        
        html = render_base("TG Parser - Posts", channel_title, last_parsed, "posts", content)
        return HTMLResponse(content=html)


# ─── /tags — Tags Analytics (24h) ────────────────────────────
@app.get("/tags", response_class=HTMLResponse)
async def tags_page(
    request: Request,
    tag: str = Query("")
):
    async with async_session() as session:
        channel_title, last_parsed = await get_channel_info(session)
        since = datetime.utcnow() - timedelta(hours=24)
        
        if tag:
            search_tag = f"#{tag}"
            result = await session.execute(
                select(Post)
                .where(Post.hashtags.op("@>")([search_tag]))
                .where(Post.published_at > since)
                .order_by(Post.published_at.desc())
            )
            posts = result.scalars().all()
            
            posts_data = []
            for p in posts:
                posts_data.append({
                    "telegram_message_id": p.telegram_message_id,
                    "views_count": f"{p.views_count:,}".replace(",", " "),
                    "published_at": p.published_at.strftime("%d.%m %H:%M") if p.published_at else "-",
                    "text": (p.text or "(media)").replace("\n", "<br>"),
                    "hashtags": p.hashtags or [],
                })
            
            tmpl = jinja_env.from_string(TAG_POSTS_TEMPLATE)
            content = tmpl.render(tag=tag, post_count=len(posts), posts=posts_data)
            html = render_base(f"TG Parser - #{tag}", channel_title, last_parsed, "tags", content)
            return HTMLResponse(content=html)
        
        # Tag analytics
        result = await session.execute(text("""
            SELECT 
                jsonb_array_elements_text(hashtags) as hashtag,
                COUNT(*) as cnt,
                SUM(views_count) as total_views,
                MAX(views_count) as max_views,
                AVG(views_count)::int as avg_views
            FROM posts
            WHERE published_at > :since
              AND hashtags IS NOT NULL
              AND jsonb_typeof(hashtags) = 'array'
              AND jsonb_array_length(hashtags) > 0
            GROUP BY jsonb_array_elements_text(hashtags)
            ORDER BY cnt DESC
            LIMIT 50
        """), {"since": since})
        
        tags = result.mappings().all()
        
        if not tags:
            content = EMPTY_TEMPLATE
        else:
            max_count = max(t["cnt"] for t in tags)
            total_tagged = sum(t["cnt"] for t in tags)
            
            colors = ["#00d4aa", "#00b894", "#0984e3", "#6c5ce7", "#fd79a8", "#e17055", "#fdcb6e", "#55efc4"]
            
            cloud_tags = []
            tag_bars = []
            for i, t in enumerate(tags):
                pct = round((t["cnt"] / max_count) * 100)
                color = colors[i % len(colors)]
                tag_data = {
                    "tag": t["hashtag"],
                    "count": t["cnt"],
                    "total_views": f"{t['total_views']:,}".replace(",", " ") if t["total_views"] else "0",
                    "avg_views": f"{t['avg_views']:,}".replace(",", " ") if t["avg_views"] else "0",
                    "pct": pct,
                    "color": color,
                }
                if i < 15:
                    cloud_tags.append(tag_data)
                tag_bars.append(tag_data)
            
            tmpl = jinja_env.from_string(TAGS_TEMPLATE)
            content = tmpl.render(
                tag_count=len(tags),
                total_tagged=f"{total_tagged:,}".replace(",", " "),
                top_tag=tags[0]["hashtag"] if tags else "-",
                since=since.strftime("%d.%m %H:%M"),
                now=datetime.utcnow().strftime("%d.%m %H:%M"),
                cloud_tags=cloud_tags,
                tag_bars=tag_bars,
            )
        
        html = render_base("TG Parser - Tags 24h", channel_title, last_parsed, "tags", content)
        return HTMLResponse(content=html)


# ─── API Endpoints ───────────────────────────────────────────
@app.get("/api/posts")
async def api_posts(page: int = Query(1, ge=1), limit: int = Query(20, le=100), search: str = Query("")):
    async with async_session() as session:
        query = select(Post).order_by(Post.published_at.desc()).offset((page - 1) * limit).limit(limit)
        if search:
            query = query.where(Post.text.ilike(f"%{search}%"))
        result = await session.execute(query)
        posts = result.scalars().all()
        return {
            "total": len(posts),
            "posts": [{"id": p.telegram_message_id, "text": p.text, "views": p.views_count,
                       "hashtags": p.hashtags, "published": p.published_at.isoformat() if p.published_at else None} for p in posts]
        }


@app.get("/api/stats")
async def api_stats():
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(Post))
        total = result.scalar()
        result = await session.execute(
            select(func.count()).select_from(Post).where(Post.published_at > datetime.utcnow() - timedelta(days=1))
        )
        today = result.scalar()
        return {"total_posts": total, "today_posts": today}


@app.get("/api/tags/24h")
async def api_tags_24h():
    async with async_session() as session:
        since = datetime.utcnow() - timedelta(hours=24)
        result = await session.execute(text("""
            SELECT 
                jsonb_array_elements_text(hashtags) as hashtag,
                COUNT(*) as cnt,
                SUM(views_count) as total_views,
                AVG(views_count)::int as avg_views
            FROM posts
            WHERE published_at > :since
              AND hashtags IS NOT NULL
              AND jsonb_typeof(hashtags) = 'array'
              AND jsonb_array_length(hashtags) > 0
            GROUP BY jsonb_array_elements_text(hashtags)
            ORDER BY cnt DESC
            LIMIT 50
        """), {"since": since})
        return {"tags": [{"tag": r["hashtag"], "count": r["cnt"], "total_views": r["total_views"], "avg_views": r["avg_views"]} for r in result.mappings().all()]}
