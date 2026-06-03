#!/usr/bin/env python3
"""
TG Parser Dashboard — SPA with async API.
Clean rewrite: proper timezone handling, safe SQL, error recovery.
"""
import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import JSON as JSONCol, BigInteger, Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

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


class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    channel_id = Column(Integer, index=True)
    telegram_message_id = Column(BigInteger)
    text = Column(Text)
    views_count = Column(Integer, default=0)
    forwards_count = Column(Integer, default=0)
    replies_count = Column(Integer, default=0)
    hashtags = Column(JSONCol, default=list)
    forward_from = Column(String(255))
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ParseLog(Base):
    __tablename__ = "parse_logs"
    id = Column(Integer, primary_key=True)
    posts_parsed = Column(Integer, default=0)
    posts_new = Column(Integer, default=0)
    duration_ms = Column(Integer)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))


app = FastAPI()

# ─── SPA HTML ────────────────────────────────────────────────
SPA_HTML = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>TG Parser</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f23; color: #e0e0e0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #00d4aa; font-size: 24px; margin-bottom: 5px; }
        .subtitle { color: #888; font-size: 14px; margin-bottom: 15px; }
        .nav { display: flex; gap: 5px; margin-bottom: 25px; background: #1a1a2e; border-radius: 10px; padding: 5px; width: fit-content; }
        .nav-btn { color: #888; background: none; border: none; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; }
        .nav-btn:hover { color: #e0e0e0; background: #2a2a4e; }
        .nav-btn.active { color: #0f0f23; background: #00d4aa; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 15px; margin-bottom: 25px; }
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
        .pagination span { color: #888; padding: 8px 16px; }
        .section-title { color: #00d4aa; font-size: 18px; margin: 20px 0 15px; }
        .tag-bar-row { display: flex; align-items: center; gap: 15px; margin-bottom: 10px; padding: 12px 15px; background: #1a1a2e; border-radius: 10px; border: 1px solid #2a2a4e; }
        .tag-name { min-width: 150px; font-weight: 600; color: #00d4aa; font-size: 14px; }
        .tag-bar-wrap { flex: 1; height: 28px; background: #0f0f23; border-radius: 6px; overflow: hidden; }
        .tag-bar { height: 100%; border-radius: 6px; display: flex; align-items: center; padding: 0 10px; font-size: 12px; font-weight: 600; color: #fff; transition: width 0.8s ease; }
        .tag-meta { min-width: 80px; text-align: right; color: #888; font-size: 12px; }
        .empty { color: #888; text-align: center; padding: 40px; }
        .error { color: #e17055; text-align: center; padding: 20px; background: #1a1a2e; border-radius: 10px; margin: 10px 0; }
        .retry { color: #00d4aa; cursor: pointer; text-decoration: underline; }

        /* Welcome */
        #welcome { position: fixed; inset: 0; background: #0f0f23; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 1000; transition: opacity 0.5s; }
        #welcome.hidden { opacity: 0; pointer-events: none; }
        #welcome .logo { font-size: 64px; margin-bottom: 20px; }
        #welcome h2 { color: #00d4aa; font-size: 28px; margin-bottom: 10px; }
        #welcome p { color: #888; font-size: 16px; }
        #welcome .loader { width: 200px; height: 4px; background: #1a1a2e; border-radius: 2px; margin-top: 30px; overflow: hidden; }
        #welcome .loader-bar { width: 0%; height: 100%; background: #00d4aa; border-radius: 2px; animation: load 2s ease-in-out infinite; }
        @keyframes load { 0% { width: 0%; } 50% { width: 100%; } 100% { width: 0%; } }
    </style>
</head>
<body>
    <div id="welcome">
        <div class="logo">📊</div>
        <h2>TG Parser Dashboard</h2>
        <p>Loading your data...</p>
        <div class="loader"><div class="loader-bar"></div></div>
    </div>

    <div class="container">
        <h1>TG Parser Dashboard</h1>
        <p class="subtitle" id="subtitle">-</p>

        <div class="nav">
            <button class="nav-btn active" onclick="showTab('posts',this)">Posts</button>
            <button class="nav-btn" onclick="showTab('tags',this)">Tags 24h</button>
        </div>

        <div id="tab-posts">
            <div class="stats" id="post-stats"></div>
            <div class="filters">
                <input type="text" id="searchInput" placeholder="Search..." onkeydown="if(event.key==='Enter')doSearch()">
                <select id="sortSelect">
                    <option value="new">Newest</option>
                    <option value="views">Most views</option>
                </select>
                <button class="btn" onclick="doSearch()">Search</button>
            </div>
            <div class="posts" id="posts-list"></div>
            <div class="pagination" id="pagination"></div>
        </div>

        <div id="tab-tags" style="display:none">
            <div class="stats" id="tag-stats"></div>
            <div id="tags-content"></div>
        </div>
    </div>

    <script>
        let page = 1;
        let loading = {posts: false, tags: false};

        function hideWelcome() {
            document.getElementById('welcome').classList.add('hidden');
        }
        setTimeout(hideWelcome, 8000); // Force hide after 8s

        function showTab(tab, btn) {
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-posts').style.display = tab === 'posts' ? '' : 'none';
            document.getElementById('tab-tags').style.display = tab === 'tags' ? '' : 'none';
            if (tab === 'tags') loadTags();
        }

        async function api(path) {
            const r = await fetch('/api' + path);
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        }

        function fmt(n) { return (n || 0).toLocaleString('en').replace(/,/g, ' '); }
        function esc(t) { const d = document.createElement('div'); d.textContent = t || ''; return d.innerHTML.replace(/\n/g, '<br>'); }

        function setError(id, msg) {
            document.getElementById(id).innerHTML = '<div class="error">' + msg + '<br><span class="retry" onclick="location.reload()">Reload page</span></div>';
            hideWelcome();
        }

        // ─── Posts ──────────────────────────────────────────
        async function loadPosts(p) {
            if (loading.posts) return;
            loading.posts = true;
            if (p !== undefined) page = p;

            const search = document.getElementById('searchInput').value;
            const sort = document.getElementById('sortSelect').value;

            try {
                const [data, stats] = await Promise.all([
                    api('/posts?page=' + page + '&search=' + encodeURIComponent(search) + '&sort=' + sort),
                    api('/stats')
                ]);

                document.getElementById('post-stats').innerHTML =
                    '<div class="stat"><div class="stat-value">' + fmt(stats.total_posts) + '</div><div class="stat-label">Total</div></div>' +
                    '<div class="stat"><div class="stat-value">' + fmt(stats.today_posts) + '</div><div class="stat-label">Today</div></div>' +
                    '<div class="stat"><div class="stat-value">' + fmt(stats.week_posts) + '</div><div class="stat-label">Week</div></div>' +
                    '<div class="stat"><div class="stat-value">' + fmt(stats.avg_views) + '</div><div class="stat-label">Avg reach</div></div>' +
                    '<div class="stat"><div class="stat-value">' + fmt(stats.total_parses) + '</div><div class="stat-label">Parses</div></div>';

                document.getElementById('subtitle').textContent = fmt(stats.total_posts) + ' posts | Last: ' + (stats.last_parsed || '-');

                const list = document.getElementById('posts-list');
                if (!data.posts.length) {
                    list.innerHTML = '<div class="empty">No posts found</div>';
                } else {
                    list.innerHTML = data.posts.map(p => {
                        const tags = (p.hashtags || []).map(t => '<span class="tag">' + esc(t) + '</span>').join('');
                        return '<div class="post"><div class="post-header"><span>ID: ' + p.id + '</span><span>views: ' + fmt(p.views) + '</span><span>' + (p.published ? p.published.slice(0,16).replace('T',' ') : '') + '</span></div><div class="post-text">' + esc(p.text) + '</div>' + (tags ? '<div class="post-tags">' + tags + '</div>' : '') + '</div>';
                    }).join('');
                }

                document.getElementById('pagination').innerHTML =
                    (page > 1 ? '<a onclick="loadPosts(' + (page-1) + ')">&larr; Prev</a>' : '<span></span>') +
                    '<span>Page ' + page + '</span>' +
                    (data.posts.length === 20 ? '<a onclick="loadPosts(' + (page+1) + ')">Next &rarr;</a>' : '<span></span>');

                hideWelcome();
            } catch(e) {
                console.error(e);
                setError('posts-list', 'Failed to load posts. Error: ' + e.message);
            } finally {
                loading.posts = false;
            }
        }

        function doSearch() { page = 1; loadPosts(1); }

        // ─── Tags ───────────────────────────────────────────
        async function loadTags() {
            if (loading.tags) return;
            loading.tags = true;

            try {
                const data = await api('/tags/24h');
                const tags = data.tags || [];

                document.getElementById('tag-stats').innerHTML =
                    '<div class="stat"><div class="stat-value">' + tags.length + '</div><div class="stat-label">Unique tags</div></div>' +
                    '<div class="stat"><div class="stat-value">' + (tags[0] ? tags[0].tag : '-') + '</div><div class="stat-label">Top tag</div></div>' +
                    '<div class="stat"><div class="stat-value">' + fmt(tags.reduce((a,t) => a + t.count, 0)) + '</div><div class="stat-label">Tagged posts</div></div>';

                if (!tags.length) {
                    document.getElementById('tags-content').innerHTML = '<div class="empty">No tags in last 24h</div>';
                    hideWelcome();
                    loading.tags = false;
                    return;
                }

                const maxCount = Math.max(...tags.map(t => t.count));
                const colors = ['#00d4aa','#00b894','#0984e3','#6c5ce7','#fd79a8','#e17055','#fdcb6e','#55efc4'];

                let html = '<div class="section-title">Tag Ranking (last 24h)</div>';
                tags.forEach((t, i) => {
                    const pct = Math.round((t.count / maxCount) * 100);
                    const color = colors[i % colors.length];
                    html += '<div class="tag-bar-row"><div class="tag-name">' + esc(t.tag) + '</div><div class="tag-bar-wrap"><div class="tag-bar" style="width:' + pct + '%;background:' + color + ';">' + t.count + ' posts</div></div><div class="tag-meta">views: ' + fmt(t.total_views) + '<br>~' + fmt(t.avg_views) + '</div></div>';
                });

                document.getElementById('tags-content').innerHTML = html;
                hideWelcome();
            } catch(e) {
                console.error(e);
                setError('tags-content', 'Failed to load tags. Error: ' + e.message);
            } finally {
                loading.tags = false;
            }
        }

        // Init
        loadPosts(1);
    </script>
</body>
</html>'''


# ─── Routes ──────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(content=SPA_HTML)


@app.get("/api/stats")
async def api_stats():
    try:
        async with async_session() as session:
            total = (await session.execute(select(func.count()).select_from(Post))).scalar()
            today = (await session.execute(
                select(func.count()).select_from(Post)
                .where(Post.published_at > datetime.now(timezone.utc) - timedelta(days=1))
            )).scalar()
            week = (await session.execute(
                select(func.count()).select_from(Post)
                .where(Post.published_at > datetime.now(timezone.utc) - timedelta(days=7))
            )).scalar()
            avg_views = int((await session.execute(select(func.avg(Post.views_count)).select_from(Post))).scalar() or 0)
            parses = (await session.execute(select(func.count()).select_from(ParseLog))).scalar()

            # Last parse time
            last = (await session.execute(
                select(ParseLog.started_at).order_by(ParseLog.started_at.desc()).limit(1)
            )).scalar()

            return {
                "total_posts": total,
                "today_posts": today,
                "week_posts": week,
                "avg_views": avg_views,
                "total_parses": parses,
                "last_parsed": last.strftime("%Y-%m-%d %H:%M") if last else None,
            }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/posts")
async def api_posts(page: int = 1, limit: int = 20, search: str = "", sort: str = "new"):
    try:
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

            return {"posts": [{"id": p.telegram_message_id, "text": p.text, "views": p.views_count,
                               "hashtags": p.hashtags or [], "published": p.published_at.isoformat() if p.published_at else None}
                              for p in posts]}
    except Exception as e:
        return {"error": str(e), "posts": []}


@app.get("/api/tags/24h")
async def api_tags_24h():
    try:
        async with async_session() as session:
            now = datetime.now(timezone.utc)
            since = now - timedelta(hours=24)

            # CRITICAL FIX: if DB dates are in future (e.g. 2026), adjust since
            max_pub = (await session.execute(text("SELECT MAX(published_at) FROM posts"))).scalar()
            if max_pub and max_pub.year > now.year:
                since = max_pub - timedelta(hours=24)

            result = await session.execute(text("""
                WITH tagged_posts AS (
                    SELECT * FROM posts
                    WHERE published_at > :since
                      AND hashtags IS NOT NULL
                      AND jsonb_typeof(hashtags) = 'array'
                      AND jsonb_array_length(hashtags) > 0
                )
                SELECT
                    jsonb_array_elements_text(hashtags) as hashtag,
                    COUNT(*) as cnt,
                    SUM(views_count) as total_views,
                    AVG(views_count)::int as avg_views
                FROM tagged_posts
                GROUP BY jsonb_array_elements_text(hashtags)
                ORDER BY cnt DESC
                LIMIT 50
            """), {"since": since})
            rows = result.mappings().all()

            # Fallback: all time
            if not rows:
                result = await session.execute(text("""
                    WITH tagged_posts AS (
                        SELECT * FROM posts
                        WHERE hashtags IS NOT NULL
                          AND jsonb_typeof(hashtags) = 'array'
                          AND jsonb_array_length(hashtags) > 0
                    )
                    SELECT
                        jsonb_array_elements_text(hashtags) as hashtag,
                        COUNT(*) as cnt,
                        SUM(views_count) as total_views,
                        AVG(views_count)::int as avg_views
                    FROM tagged_posts
                    GROUP BY jsonb_array_elements_text(hashtags)
                    ORDER BY cnt DESC
                    LIMIT 50
                """))
                rows = result.mappings().all()

            return {"tags": [{"tag": r["hashtag"], "count": r["cnt"],
                              "total_views": r["total_views"] or 0, "avg_views": r["avg_views"] or 0}
                             for r in rows]}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"tags": [], "error": str(e)}
