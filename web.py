#!/usr/bin/env python3
"""
Web dashboard for tgparser database.
SPA: static HTML with loader + async API data loading.
Eliminates cold start delay — page renders instantly, data loads via fetch.
"""
import os
import json
from datetime import datetime, timedelta

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import (
    JSON as JSONCol, BigInteger, Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint
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
    hashtags = Column(JSONCol, default=list)
    mentions = Column(JSONCol, default=list)
    urls = Column(JSONCol, default=list)
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

# ─── Static SPA HTML — loads instantly ───────────────────────
# Data is fetched asynchronously via /api/* endpoints

SPA_HTML = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>TG Parser Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f23; color: #e0e0e0; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        h1 { color: #00d4aa; margin-bottom: 5px; font-size: 24px; }
        .subtitle { color: #888; margin-bottom: 15px; font-size: 14px; }
        .nav { display: flex; gap: 5px; margin-bottom: 25px; background: #1a1a2e; border-radius: 10px; padding: 5px; width: fit-content; }
        .nav-btn { color: #888; background: none; border: none; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s; }
        .nav-btn:hover { color: #e0e0e0; background: #2a2a4e; }
        .nav-btn.active { color: #0f0f23; background: #00d4aa; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 15px; margin-bottom: 25px; }
        .stat { background: #1a1a2e; border-radius: 12px; padding: 20px; border: 1px solid #2a2a4e; }
        .stat-value { font-size: 28px; font-weight: bold; color: #00d4aa; }
        .stat-label { color: #888; font-size: 13px; margin-top: 5px; }
        .filters { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        input, select { background: #1a1a2e; color: #e0e0e0; border: 1px solid #2a2a4e; padding: 10px 15px; border-radius: 8px; font-size: 14px; }
        .btn { background: #00d4aa; color: #0f0f23; border: none; padding: 10px 20px; border-radius: 8px; font-weight: bold; cursor: pointer; }
        .btn:hover { background: #00b894; }
        .posts { display: flex; flex-direction: column; gap: 10px; }
        .post { background: #1a1a2e; border-radius: 12px; padding: 15px; border: 1px solid #2a2a4e; }
        .post-header { display: flex; gap: 15px; margin-bottom: 8px; font-size: 13px; color: #888; flex-wrap: wrap; }
        .post-text { color: #e0e0e0; line-height: 1.6; white-space: pre-wrap; }
        .post-tags { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
        .tag { background: #2a2a4e; color: #00d4aa; padding: 3px 10px; border-radius: 20px; font-size: 12px; text-decoration: none; cursor: pointer; }
        .tag:hover { background: #00d4aa; color: #0f0f23; }
        .pagination { display: flex; gap: 10px; justify-content: center; margin-top: 20px; align-items: center; }
        .pagination a { color: #00d4aa; text-decoration: none; padding: 8px 16px; background: #1a1a2e; border-radius: 8px; cursor: pointer; }
        .pagination a:hover { background: #2a2a4e; }
        .section-title { color: #00d4aa; font-size: 18px; margin: 20px 0 15px; }
        .tag-cloud { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 25px; }
        .tag-item { background: #1a1a2e; border: 1px solid #2a2a4e; border-radius: 20px; padding: 6px 16px; font-size: 14px; color: #e0e0e0; text-decoration: none; display: flex; align-items: center; gap: 8px; cursor: pointer; }
        .tag-item:hover { background: #00d4aa; color: #0f0f23; border-color: #00d4aa; }
        .tag-count { background: #2a2a4e; color: #00d4aa; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }
        .tag-item:hover .tag-count { background: #0f0f23; color: #00d4aa; }
        .tag-bar-row { display: flex; align-items: center; gap: 15px; margin-bottom: 10px; padding: 12px 15px; background: #1a1a2e; border-radius: 10px; border: 1px solid #2a2a4e; }
        .tag-name { min-width: 150px; font-weight: 600; color: #00d4aa; font-size: 14px; }
        .tag-bar-wrap { flex: 1; height: 28px; background: #0f0f23; border-radius: 6px; overflow: hidden; }
        .tag-bar { height: 100%; border-radius: 6px; display: flex; align-items: center; padding: 0 10px; font-size: 12px; font-weight: 600; color: #fff; transition: width 0.8s ease; }
        .tag-meta { min-width: 90px; text-align: right; color: #888; font-size: 12px; }
        .time-badge { background: #2a2a4e; color: #e0e0e0; padding: 4px 12px; border-radius: 6px; font-size: 13px; display: inline-block; margin-bottom: 15px; }
        .empty { color: #888; text-align: center; padding: 40px; }

        /* Skeleton loader */
        .skeleton { background: linear-gradient(90deg, #1a1a2e 25%, #2a2a4e 50%, #1a1a2e 75%); background-size: 200% 100%; animation: shimmer 1.5s infinite; border-radius: 8px; }
        @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
        .sk-stat { height: 60px; }
        .sk-post { height: 100px; margin-bottom: 10px; }

        /* Welcome overlay */
        #welcome { position: fixed; inset: 0; background: #0f0f23; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 1000; transition: opacity 0.5s; }
        #welcome.hidden { opacity: 0; pointer-events: none; }
        #welcome .logo { font-size: 64px; margin-bottom: 20px; animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.1); } }
        #welcome h2 { color: #00d4aa; font-size: 28px; margin-bottom: 10px; }
        #welcome p { color: #888; font-size: 16px; }
        #welcome .loader { width: 200px; height: 4px; background: #1a1a2e; border-radius: 2px; margin-top: 30px; overflow: hidden; }
        #welcome .loader-bar { width: 0%; height: 100%; background: #00d4aa; border-radius: 2px; animation: load 2s ease-in-out infinite; }
        @keyframes load { 0% { width: 0%; } 50% { width: 100%; } 100% { width: 0%; } }
    </style>
</head>
<body>
    <!-- Welcome / Loader Overlay -->
    <div id="welcome">
        <div class="logo">📊</div>
        <h2>TG Parser Dashboard</h2>
        <p>Loading your data...</p>
        <div class="loader"><div class="loader-bar"></div></div>
    </div>

    <div class="container">
        <h1>TG Parser Dashboard</h1>
        <p class="subtitle" id="subtitle">Loading channel info...</p>

        <div class="nav">
            <button class="nav-btn active" onclick="showTab('posts', this)">Posts</button>
            <button class="nav-btn" onclick="showTab('tags', this)">Tags 24h</button>
        </div>

        <!-- Posts Tab -->
        <div id="tab-posts">
            <div class="stats" id="post-stats">
                <div class="stat skeleton sk-stat"></div>
                <div class="stat skeleton sk-stat"></div>
                <div class="stat skeleton sk-stat"></div>
                <div class="stat skeleton sk-stat"></div>
                <div class="stat skeleton sk-stat"></div>
            </div>
            <div class="filters">
                <input type="text" id="search" placeholder="Search text..." onkeydown="if(event.key==='Enter')loadPosts()">
                <select id="sort">
                    <option value="new">Newest</option>
                    <option value="views">Most views</option>
                </select>
                <button class="btn" onclick="loadPosts()">Search</button>
            </div>
            <div class="posts" id="posts-list">
                <div class="skeleton sk-post"></div>
                <div class="skeleton sk-post"></div>
                <div class="skeleton sk-post"></div>
            </div>
            <div class="pagination" id="pagination"></div>
        </div>

        <!-- Tags Tab -->
        <div id="tab-tags" style="display:none">
            <div class="stats" id="tag-stats">
                <div class="stat skeleton sk-stat"></div>
                <div class="stat skeleton sk-stat"></div>
                <div class="stat skeleton sk-stat"></div>
            </div>
            <div id="tags-content">
                <div class="skeleton sk-post"></div>
                <div class="skeleton sk-post"></div>
            </div>
        </div>
    </div>

    <script>
        let currentPage = 1;
        let currentTag = '';

        function hideWelcome() {
            document.getElementById('welcome').classList.add('hidden');
        }

        function showTab(tab, btn) {
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-posts').style.display = tab === 'posts' ? '' : 'none';
            document.getElementById('tab-tags').style.display = tab === 'tags' ? '' : 'none';
            if (tab === 'tags') loadTags();
        }

        async function api(path) {
            const r = await fetch('/api' + path);
            if (!r.ok) throw new Error(r.status);
            return r.json();
        }

        function fmtNum(n) {
            return (n || 0).toLocaleString('en').replace(/,/g, ' ');
        }

        async function loadStats() {
            try {
                const s = await api('/stats');
                document.getElementById('subtitle').textContent =
                    fmtNum(s.total_posts) + ' posts | ' + fmtNum(s.today_posts) + ' today';
                hideWelcome();
            } catch(e) { console.error(e); }
        }

        async function loadPosts(page) {
            // If called without page (from Search button), reset to page 1
            if (page === undefined) currentPage = 1;
            else if (page) currentPage = page;

            const search = document.getElementById('search').value;
            const sort = document.getElementById('sort').value;
            const url = '/posts?page=' + currentPage + '&limit=20&search=' + encodeURIComponent(search) + '&sort=' + sort;
            console.log('API:', url);

            try {
                const data = await api(url);
                console.log('Got', data.posts.length, 'posts');

                // Stats
                const stats = await api('/stats');
                document.getElementById('post-stats').innerHTML = `
                    <div class="stat"><div class="stat-value">${fmtNum(stats.total_posts)}</div><div class="stat-label">Total posts</div></div>
                    <div class="stat"><div class="stat-value">${fmtNum(stats.today_posts)}</div><div class="stat-label">Today</div></div>
                    <div class="stat"><div class="stat-value">${fmtNum(stats.week_posts)}</div><div class="stat-label">This week</div></div>
                    <div class="stat"><div class="stat-value">${fmtNum(stats.avg_views)}</div><div class="stat-label">Avg reach</div></div>
                    <div class="stat"><div class="stat-value">${fmtNum(stats.total_parses)}</div><div class="stat-label">Parses</div></div>
                `;

                // Posts
                const list = document.getElementById('posts-list');
                if (!data.posts.length) {
                    list.innerHTML = '<div class="empty">No posts found</div>';
                } else {
                    list.innerHTML = data.posts.map(p => {
                        const tags = (p.hashtags || []).map(t =>
                            `<span class="tag" onclick="searchTag('${t.replace('#','')}')">${t}</span>`
                        ).join('');
                        return `<div class="post">
                            <div class="post-header">
                                <span>ID: ${p.id}</span>
                                <span>views: ${fmtNum(p.views)}</span>
                                <span>${p.published ? p.published.slice(0,16).replace('T',' ') : ''}</span>
                            </div>
                            <div class="post-text">${escapeHtml(p.text || '(no text)')}</div>
                            ${tags ? '<div class="post-tags">' + tags + '</div>' : ''}
                        </div>`;
                    }).join('');
                }

                // Pagination
                const hasNext = data.posts.length === 20;
                const hasPrev = currentPage > 1;
                document.getElementById('pagination').innerHTML =
                    (hasPrev ? `<a href="#" onclick="event.preventDefault(); loadPosts(${currentPage-1}); return false;">&larr; Prev</a>` : '<span></span>') +
                    `<span>Page ${currentPage}</span>` +
                    (hasNext ? `<a href="#" onclick="event.preventDefault(); loadPosts(${currentPage+1}); return false;">Next &rarr;</a>` : '<span></span>');

                hideWelcome();
            } catch(e) {
                console.error('loadPosts error:', e);
                document.getElementById('posts-list').innerHTML = '<div class="empty">Error loading posts. <button class="btn" onclick="loadPosts()">Retry</button></div>';
            }
        }

        async function loadTags() {
            if (window.tagsLoading) return;
            window.tagsLoading = true;
            try {
                const data = await api('/tags/24h');
                const tags = data.tags;
                window.tagsLoaded = true;

                const isFallback = data.fallback;
                document.getElementById('tag-stats').innerHTML = `
                    <div class="stat"><div class="stat-value">${tags.length}</div><div class="stat-label">Unique tags</div></div>
                    <div class="stat"><div class="stat-value">${tags[0] ? tags[0].tag : '-'}</div><div class="stat-label">Top tag</div></div>
                    <div class="stat"><div class="stat-value">${fmtNum(tags.reduce((a,t)=>a+t.count,0))}</div><div class="stat-label">Tagged posts</div></div>
                `;

                if (!tags.length) {
                    document.getElementById('tags-content').innerHTML = '<div class="empty">No tags found</div>';
                    return;
                }

                const maxCount = Math.max(...tags.map(t => t.count));
                const colors = ['#00d4aa','#00b894','#0984e3','#6c5ce7','#fd79a8','#e17055','#fdcb6e','#55efc4'];

                let html = `<div class="time-badge">${isFallback ? 'Last 24 hours (no data — showing all-time)' : 'Last 24 hours'}</div>`;
                html += '<div class="section-title">Tag Cloud</div>';
                html += '<div class="tag-cloud">';
                tags.slice(0, 20).forEach(t => {
                    html += `<span class="tag-item" onclick="showTagPosts('${t.tag.replace('#','')}')">${t.tag} <span class="tag-count">${t.count}</span></span>`;
                });
                html += '</div>';

                html += '<div class="section-title">Tag Ranking</div>';
                tags.forEach((t, i) => {
                    const pct = Math.round((t.count / maxCount) * 100);
                    const color = colors[i % colors.length];
                    html += `<div class="tag-bar-row">
                        <div class="tag-name">${t.tag}</div>
                        <div class="tag-bar-wrap"><div class="tag-bar" style="width:${pct}%;background:${color};">${t.count} posts</div></div>
                        <div class="tag-meta">views: ${fmtNum(t.total_views)}<br>~${fmtNum(t.avg_views)} avg</div>
                    </div>`;
                });

                document.getElementById('tags-content').innerHTML = html;
            } catch(e) {
                console.error('loadTags error:', e);
                document.getElementById('tags-content').innerHTML =
                    '<div class="empty">Error loading tags. <button class="btn" onclick="loadTags()">Retry</button></div>';
            } finally {
                window.tagsLoading = false;
            }
        }

        function searchTag(tag) {
            document.getElementById('search').value = '#' + tag;
            loadPosts(1);
            document.querySelectorAll('.nav-btn')[0].click();
        }

        function showTagPosts(tag) {
            currentTag = tag;
            alert('Drill-down for #' + tag + ' — implement with /api/posts?search=%23' + tag);
        }

        function escapeHtml(t) {
            const d = document.createElement('div');
            d.textContent = t;
            return d.innerHTML.replace(/\\n/g, '<br>');
        }

        // Init
        loadStats();
        loadPosts(1);
        setTimeout(hideWelcome, 5000); // Force hide after 5s max
    </script>
</body>
</html>'''


# ─── / — Static SPA ──────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=SPA_HTML)


# ─── API Endpoints (fast, no template rendering) ─────────────
@app.get("/api/stats")
async def api_stats():
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(Post))
        total = result.scalar()

        result = await session.execute(
            select(func.count()).select_from(Post)
            .where(Post.published_at > datetime.utcnow() - timedelta(days=1))
        )
        today = result.scalar()

        result = await session.execute(
            select(func.count()).select_from(Post)
            .where(Post.published_at > datetime.utcnow() - timedelta(days=7))
        )
        week = result.scalar()

        result = await session.execute(select(func.avg(Post.views_count)).select_from(Post))
        avg_views = int(result.scalar() or 0)

        result = await session.execute(select(func.count()).select_from(ParseLog))
        parses = result.scalar()

        return {
            "total_posts": total,
            "today_posts": today,
            "week_posts": week,
            "avg_views": avg_views,
            "total_parses": parses,
        }


@app.get("/api/posts")
async def api_posts(
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    search: str = Query(""),
    sort: str = Query("new")
):
    async with async_session() as session:
        query = select(Post)
        if search:
            query = query.where(Post.text.ilike(f"%{search}%"))
        if sort == "views":
            query = query.order_by(Post.views_count.desc())
        else:
            query = query.order_by(Post.published_at.desc())

        query = query.offset((page - 1) * limit).limit(limit)
        result = await session.execute(query)
        posts = result.scalars().all()

        return {
            "posts": [
                {
                    "id": p.telegram_message_id,
                    "text": p.text,
                    "views": p.views_count,
                    "forwards": p.forwards_count,
                    "replies": p.replies_count,
                    "hashtags": p.hashtags or [],
                    "forward_from": p.forward_from,
                    "published": p.published_at.isoformat() if p.published_at else None,
                }
                for p in posts
            ]
        }


@app.get("/api/tags/24h")
async def api_tags_24h():
    import traceback
    async with async_session() as session:
        since = datetime.utcnow() - timedelta(hours=24)
        try:
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
            rows = result.mappings().all()

            # Fallback: if no tags in 24h, show all-time top tags
            if not rows:
                result = await session.execute(text("""
                    SELECT
                        jsonb_array_elements_text(hashtags) as hashtag,
                        COUNT(*) as cnt,
                        SUM(views_count) as total_views,
                        MAX(views_count) as max_views,
                        AVG(views_count)::int as avg_views
                    FROM posts
                    WHERE hashtags IS NOT NULL
                      AND jsonb_typeof(hashtags) = 'array'
                      AND jsonb_array_length(hashtags) > 0
                    GROUP BY jsonb_array_elements_text(hashtags)
                    ORDER BY cnt DESC
                    LIMIT 50
                """))
                rows = result.mappings().all()

            return {
                "tags": [
                    {
                        "tag": r["hashtag"],
                        "count": r["cnt"],
                        "total_views": r["total_views"] or 0,
                        "max_views": r["max_views"] or 0,
                        "avg_views": r["avg_views"] or 0,
                    }
                    for r in rows
                ],
                "fallback": rows and since is not None,
            }
        except Exception as e:
            print(f"ERROR in /api/tags/24h: {e}")
            traceback.print_exc()
            return {"tags": [], "error": str(e)}
