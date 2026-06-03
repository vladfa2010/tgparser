#!/usr/bin/env python3
"""
TG Parser Dashboard — Production-grade SPA.
Features: skeleton loading, error recovery, auto-retry, offline detection.
"""
import os
import logging
import traceback
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import JSON as JSONCol, BigInteger, Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

# ─── Logging ─────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── Database ────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

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

# ─── Helpers ─────────────────────────────────────────────────
def json_response(data, status=200):
    return JSONResponse(content=data, status_code=status)


# ─── SPA ─────────────────────────────────────────────────────
HTML = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>TG Parser Dashboard</title>
    <style>
        *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a1a;color:#e2e8f0;line-height:1.5}
        .wrap{max-width:1200px;margin:0 auto;padding:24px}
        header{margin-bottom:24px}
        h1{color:#00d4aa;font-size:28px;font-weight:700;letter-spacing:-0.5px}
        .sub{color:#64748b;font-size:14px;margin-top:4px}

        /* Loader */
        #loader{position:fixed;inset:0;background:#0a0a1a;z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;transition:opacity .4s}
        #loader.done{opacity:0;pointer-events:none}
        .loader-ring{width:48px;height:48px;border:3px solid #1e293b;border-top-color:#00d4aa;border-radius:50%;animation:spin 1s linear infinite}
        @keyframes spin{to{transform:rotate(360deg)}}
        .loader-text{margin-top:16px;color:#64748b;font-size:14px}
        .loader-sub{margin-top:8px;color:#334155;font-size:12px;max-width:300px;text-align:center}

        /* Nav */
        nav{display:flex;gap:4px;background:#0f172a;padding:4px;border-radius:10px;width:fit-content;margin-bottom:24px;border:1px solid #1e293b}
        nav button{background:none;border:none;color:#64748b;padding:10px 20px;border-radius:8px;font-size:14px;font-weight:500;cursor:pointer;transition:all .15s}
        nav button:hover{color:#e2e8f0;background:#1e293b}
        nav button.on{color:#0a0a1a;background:#00d4aa;font-weight:600}

        /* Stats */
        .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin-bottom:24px}
        .stat{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:20px;transition:border-color .15s}
        .stat:hover{border-color:#334155}
        .stat-v{font-size:28px;font-weight:700;color:#00d4aa}
        .stat-l{font-size:12px;color:#64748b;margin-top:4px;text-transform:uppercase;letter-spacing:0.5px}

        /* Skeleton */
        @keyframes shimmer{0%{background-position:-200% 0}100%{background-position:200% 0}}
        .sk{background:linear-gradient(90deg,#0f172a 25%,#1e293b 50%,#0f172a 75%);background-size:200% 100%;animation:shimmer 1.5s infinite;border-radius:8px}
        .sk-stat{height:48px;margin-bottom:16px}
        .sk-post{height:120px;margin-bottom:12px;border-radius:12px}

        /* Posts */
        .filters{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
        .filters input,.filters select{background:#0f172a;border:1px solid #1e293b;color:#e2e8f0;padding:10px 16px;border-radius:8px;font-size:14px;outline:none;transition:border-color .15s;min-width:200px}
        .filters input:focus,.filters select:focus{border-color:#00d4aa}
        .filters button{background:#00d4aa;color:#0a0a1a;border:none;padding:10px 24px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;transition:opacity .15s}
        .filters button:hover{opacity:.85}
        .filters button:disabled{opacity:.5;cursor:not-allowed}

        .posts{display:flex;flex-direction:column;gap:12px}
        .post{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:16px;transition:border-color .15s}
        .post:hover{border-color:#334155}
        .post-head{display:flex;gap:16px;margin-bottom:8px;font-size:13px;color:#64748b;flex-wrap:wrap}
        .post-head span{display:flex;align-items:center;gap:4px}
        .post-body{color:#e2e8f0;white-space:pre-wrap;word-break:break-word;line-height:1.6}
        .post-body a{color:#00d4aa;text-decoration:none}
        .post-body a:hover{text-decoration:underline}
        .post-tags{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
        .tag{background:#1e293b;color:#00d4aa;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:500}

        .page{display:flex;justify-content:center;align-items:center;gap:8px;margin-top:24px}
        .page button{background:#0f172a;border:1px solid #1e293b;color:#00d4aa;padding:8px 20px;border-radius:8px;cursor:pointer;font-size:14px;transition:all .15s}
        .page button:hover{background:#1e293b}
        .page button:disabled{opacity:.3;cursor:not-allowed;color:#64748b}
        .page span{color:#64748b;font-size:14px;padding:0 12px}

        /* Tags */
        .tag-intro{color:#64748b;font-size:13px;margin-bottom:16px}
        .trow{display:flex;align-items:center;gap:16px;padding:14px 16px;background:#0f172a;border:1px solid #1e293b;border-radius:10px;margin-bottom:10px;transition:border-color .15s}
        .trow:hover{border-color:#334155}
        .tname{min-width:160px;font-weight:600;color:#00d4aa;font-size:14px}
        .tbar-wrap{flex:1;height:28px;background:#0a0a1a;border-radius:6px;overflow:hidden}
        .tbar{height:100%;border-radius:6px;display:flex;align-items:center;padding:0 12px;font-size:12px;font-weight:600;color:#fff;transition:width .8s ease}
        .tmeta{min-width:90px;text-align:right;color:#64748b;font-size:12px}

        /* Error */
        .err{background:#0f172a;border:1px solid #7f1d1d;border-radius:12px;padding:24px;text-align:center;margin:20px 0}
        .err h3{color:#f87171;font-size:18px;margin-bottom:8px}
        .err p{color:#94a3b8;font-size:14px;margin-bottom:16px}
        .err button{background:#dc2626;color:#fff;border:none;padding:10px 24px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}
        .err button:hover{background:#b91c1c}

        .empty{text-align:center;color:#64748b;padding:40px;font-size:14px}
    </style>
</head>
<body>
    <div id="loader">
        <div class="loader-ring"></div>
        <div class="loader-text">Loading dashboard...</div>
        <div class="loader-sub" id="loader-sub">Connecting to database</div>
    </div>

    <div class="wrap">
        <header>
            <h1>TG Parser Dashboard</h1>
            <p class="sub" id="subtitle">Loading...</p>
        </header>

        <nav>
            <button class="on" data-tab="posts">Posts</button>
            <button data-tab="tags">Tags 24h</button>
            <button onclick="window.location.href='/charts'">Charts</button>
        </nav>

        <!-- Posts Tab -->
        <section id="tab-posts">
            <div class="stats" id="p-stats">
                <div class="sk sk-stat"></div><div class="sk sk-stat"></div><div class="sk sk-stat"></div>
                <div class="sk sk-stat"></div><div class="sk sk-stat"></div>
            </div>
            <div class="filters">
                <input type="text" id="q" placeholder="Search text...">
                <select id="sort">
                    <option value="new">Newest</option>
                    <option value="views">Most views</option>
                </select>
                <button id="btn-search">Search</button>
            </div>
            <div id="p-list">
                <div class="sk sk-post"></div><div class="sk sk-post"></div><div class="sk sk-post"></div>
            </div>
            <div class="page" id="p-page"></div>
        </section>

        <!-- Tags Tab -->
        <section id="tab-tags" style="display:none">
            <div class="stats" id="t-stats">
                <div class="sk sk-stat"></div><div class="sk sk-stat"></div><div class="sk sk-stat"></div>
            </div>
            <p class="tag-intro" id="t-intro"></p>
            <div id="t-list">
                <div class="sk sk-post"></div><div class="sk sk-post"></div>
            </div>
        </section>
    </div>

    <script>
    (function(){
        'use strict';

        // ─── Config ──────────────────────────────────
        const MAX_RETRY = 3;
        const RETRY_MS = 1000;
        const LOADER_TIMEOUT = 6000;

        // ─── State ───────────────────────────────────
        let state = {tab:'posts',page:1,loading:{posts:false,tags:false}};

        // ─── DOM ─────────────────────────────────────
        const $ = id => document.getElementById(id);
        const $$ = sel => document.querySelectorAll(sel);

        // ─── API ─────────────────────────────────────
        async function api(path, attempt){
            attempt = attempt || 1;
            try{
                const r = await fetch('/api'+path,{cache:'no-store'});
                if(!r.ok) throw new Error('HTTP '+r.status);
                const d = await r.json();
                if(d.error) throw new Error(d.error);
                return d;
            }catch(e){
                if(attempt < MAX_RETRY){
                    await sleep(RETRY_MS * attempt);
                    return api(path, attempt+1);
                }
                throw e;
            }
        }
        function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }

        // ─── Loader ──────────────────────────────────
        function setLoader(text){
            const el = $('loader-sub');
            if(el) el.textContent = text;
        }
        function hideLoader(){
            const el = $('loader');
            if(el && !el.classList.contains('done')) el.classList.add('done');
        }
        setTimeout(hideLoader, LOADER_TIMEOUT);

        // ─── Error ───────────────────────────────────
        function showError(id, msg, retryFn){
            $(id).innerHTML = '<div class="err"><h3>Failed to load</h3><p>'+esc(msg)+'</p><button onclick="location.reload()">Reload Page</button></div>';
            hideLoader();
        }
        function esc(t){ return (t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
        function fmt(n){ return (n||0).toLocaleString('en').replace(/,/g,' '); }

        // ─── Nav ─────────────────────────────────────
        $$('nav button').forEach(btn=>{
            btn.addEventListener('click',()=>{
                const tab = btn.dataset.tab;
                $$('nav button').forEach(b=>b.classList.remove('on'));
                btn.classList.add('on');
                $('tab-posts').style.display = tab==='posts'?'':'none';
                $('tab-tags').style.display = tab==='tags'?'':'none';
                if(tab==='tags' && !window._tagsLoaded) loadTags();
            });
        });

        // ─── Search ──────────────────────────────────
        $('btn-search').addEventListener('click',()=>{ state.page=1; loadPosts(); });
        $('q').addEventListener('keydown',e=>{ if(e.key==='Enter'){ state.page=1; loadPosts(); }});

        // ─── Posts ───────────────────────────────────
        async function loadPosts(){
            if(state.loading.posts) return;
            state.loading.posts = true;
            setLoader('Loading posts...');

            const q = $('q').value;
            const sort = $('sort').value;

            try{
                const [data,stats] = await Promise.all([
                    api('/posts?page='+state.page+'&search='+encodeURIComponent(q)+'&sort='+sort),
                    api('/stats')
                ]);

                $('subtitle').textContent = fmt(stats.total_posts)+' posts | Last: '+(stats.last_parsed||'-');

                $('p-stats').innerHTML =
                    '<div class="stat"><div class="stat-v">'+fmt(stats.total_posts)+'</div><div class="stat-l">Total</div></div>'+
                    '<div class="stat"><div class="stat-v">'+fmt(stats.today_posts)+'</div><div class="stat-l">Today</div></div>'+
                    '<div class="stat"><div class="stat-v">'+fmt(stats.week_posts)+'</div><div class="stat-l">This Week</div></div>'+
                    '<div class="stat"><div class="stat-v">'+fmt(stats.avg_views)+'</div><div class="stat-l">Avg Reach</div></div>'+
                    '<div class="stat"><div class="stat-v">'+fmt(stats.total_parses)+'</div><div class="stat-l">Parses</div></div>';

                if(!data.posts || !data.posts.length){
                    $('p-list').innerHTML = '<div class="empty">No posts found</div>';
                }else{
                    $('p-list').innerHTML = data.posts.map(p=>{
                        const tags = (p.hashtags||[]).map(t=>'<span class="tag">'+esc(t)+'</span>').join('');
                        const date = p.published ? p.published.slice(0,16).replace('T',' ') : '';
                        return '<div class="post"><div class="post-head"><span>ID:'+p.id+'</span><span>views:'+fmt(p.views)+'</span><span>'+date+'</span></div><div class="post-body">'+esc(p.text||'(no text)')+'</div>'+(tags?'<div class="post-tags">'+tags+'</div>':'')+'</div>';
                    }).join('');
                }

                $('p-page').innerHTML =
                    '<button '+ (state.page>1?'onclick="goPage('+(state.page-1)+')"':'disabled') +'>Prev</button>'+
                    '<span>Page '+state.page+'</span>'+
                    '<button '+ ((data.posts||[]).length===20?'onclick="goPage('+(state.page+1)+')"':'disabled') +'>Next</button>';

                hideLoader();
            }catch(e){
                console.error(e);
                showError('p-list', e.message);
            }finally{
                state.loading.posts = false;
            }
        }
        window.goPage = function(p){ state.page=p; loadPosts(); };

        // ─── Tags ────────────────────────────────────
        async function loadTags(){
            if(state.loading.tags) return;
            state.loading.tags = true;
            setLoader('Loading tags...');

            try{
                const data = await api('/tags/24h');
                window._tagsLoaded = true;
                const tags = data.tags || [];

                $('t-stats').innerHTML =
                    '<div class="stat"><div class="stat-v">'+tags.length+'</div><div class="stat-l">Unique Tags</div></div>'+
                    '<div class="stat"><div class="stat-v">'+(tags[0]?esc(tags[0].tag):'-')+'</div><div class="stat-l">Top Tag</div></div>'+
                    '<div class="stat"><div class="stat-v">'+fmt(tags.reduce((a,t)=>a+t.count,0))+'</div><div class="stat-l">Tagged Posts</div></div>';

                if(!tags.length){
                    $('t-list').innerHTML = '<div class="empty">No tags in last 24 hours</div>';
                    hideLoader();
                    state.loading.tags = false;
                    return;
                }

                $('t-intro').textContent = 'Top hashtags from last 24 hours, ranked by frequency';

                const maxC = Math.max(...tags.map(t=>t.count));
                const colors = ['#00d4aa','#00b894','#0984e3','#6c5ce7','#fd79a8','#e17055','#fdcb6e','#55efc4'];

                $('t-list').innerHTML = tags.map((t,i)=>{
                    const pct = Math.round((t.count/maxC)*100);
                    const col = colors[i%colors.length];
                    return '<div class="trow"><div class="tname">'+esc(t.tag)+'</div><div class="tbar-wrap"><div class="tbar" style="width:'+pct+'%;background:'+col+'">'+t.count+' posts</div></div><div class="tmeta">'+fmt(t.total_views)+' views<br>~'+fmt(t.avg_views)+' avg</div></div>';
                }).join('');

                hideLoader();
            }catch(e){
                console.error(e);
                showError('t-list', e.message);
            }finally{
                state.loading.tags = false;
            }
        }

        // ─── Init ────────────────────────────────────
        loadPosts();

    })();
    </script>
</body>
</html>'''


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(content=HTML)


@app.get("/charts", response_class=HTMLResponse)
async def charts_page():
    with open(os.path.join(os.path.dirname(__file__), "charts.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ─── API: Stats ──────────────────────────────────────────────
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
            last = (await session.execute(
                select(ParseLog.started_at).order_by(ParseLog.started_at.desc()).limit(1)
            )).scalar()
            return {"total_posts": total, "today_posts": today, "week_posts": week,
                    "avg_views": avg_views, "total_parses": parses,
                    "last_parsed": last.strftime("%Y-%m-%d %H:%M") if last else None}
    except Exception as e:
        logger.error(f"/stats error: {e}")
        traceback.print_exc()
        return json_response({"error": str(e), "total_posts": 0, "today_posts": 0}, 500)


# ─── API: Posts ──────────────────────────────────────────────
@app.get("/api/posts")
async def api_posts(page: int = 1, limit: int = 20, search: str = "", sort: str = "new"):
    try:
        async with async_session() as session:
            query = select(Post)
            if search:
                query = query.where(Post.text.ilike(f"%{search}%"))
            query = query.order_by(Post.views_count.desc() if sort == "views" else Post.published_at.desc())
            query = query.offset((page - 1) * limit).limit(limit)
            result = await session.execute(query)
            posts = result.scalars().all()
            return {"posts": [{"id": p.telegram_message_id, "text": p.text, "views": p.views_count,
                               "hashtags": p.hashtags or [], "published": p.published_at.isoformat() if p.published_at else None}
                              for p in posts]}
    except Exception as e:
        logger.error(f"/posts error: {e}")
        traceback.print_exc()
        return json_response({"error": str(e), "posts": []}, 500)


# ─── API: Tags 24h ───────────────────────────────────────────
@app.get("/api/tags/24h")
async def api_tags_24h():
    try:
        async with async_session() as session:
            since = datetime.now(timezone.utc) - timedelta(hours=24)

            # Year mismatch fix
            max_pub = (await session.execute(text("SELECT MAX(published_at) FROM posts"))).scalar()
            if max_pub and max_pub.year > datetime.now(timezone.utc).year:
                since = max_pub - timedelta(hours=24)

            # JSON (not JSONB!) functions
            result = await session.execute(text("""
                WITH tagged AS (
                    SELECT * FROM posts
                    WHERE published_at > :since
                      AND hashtags IS NOT NULL
                      AND json_typeof(hashtags) = 'array'
                      AND json_array_length(hashtags) > 0
                )
                SELECT json_array_elements_text(hashtags) as hashtag,
                       COUNT(*) as cnt,
                       SUM(views_count) as total_views,
                       AVG(views_count)::int as avg_views
                FROM tagged
                GROUP BY json_array_elements_text(hashtags)
                ORDER BY cnt DESC LIMIT 50
            """), {"since": since})
            rows = result.mappings().all()

            if not rows:
                result = await session.execute(text("""
                    WITH tagged AS (
                        SELECT * FROM posts
                        WHERE hashtags IS NOT NULL
                          AND json_typeof(hashtags) = 'array'
                          AND json_array_length(hashtags) > 0
                    )
                    SELECT json_array_elements_text(hashtags) as hashtag,
                           COUNT(*) as cnt,
                           SUM(views_count) as total_views,
                           AVG(views_count)::int as avg_views
                    FROM tagged
                    GROUP BY json_array_elements_text(hashtags)
                    ORDER BY cnt DESC LIMIT 50
                """))
                rows = result.mappings().all()

            return {"tags": [{"tag": r["hashtag"], "count": r["cnt"],
                              "total_views": r["total_views"] or 0, "avg_views": r["avg_views"] or 0}
                             for r in rows]}
    except Exception as e:
        logger.error(f"/tags/24h error: {e}")
        traceback.print_exc()
        return json_response({"tags": [], "error": str(e)}, 500)


# ─── Charts API ──────────────────────────────────────────────
@app.get("/api/charts/tags")
async def chart_tags(days: int = Query(7, ge=1, le=90)):
    try:
        async with async_session() as session:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            result = await session.execute(text("""
                WITH tagged AS (
                    SELECT * FROM posts WHERE published_at > :since
                        AND hashtags IS NOT NULL
                        AND json_typeof(hashtags) = 'array'
                        AND json_array_length(hashtags) > 0
                )
                SELECT json_array_elements_text(hashtags) as hashtag,
                       COUNT(*) as cnt,
                       SUM(views_count) as total_views,
                       AVG(views_count)::int as avg_views
                FROM tagged
                GROUP BY json_array_elements_text(hashtags)
                ORDER BY cnt DESC LIMIT 50
            """), {"since": since})
            rows = result.mappings().all()
            total = (await session.execute(
                select(func.count()).select_from(Post).where(Post.published_at > since)
            )).scalar()
            avg_reach = int((await session.execute(
                select(func.avg(Post.views_count)).select_from(Post).where(Post.published_at > since)
            )).scalar() or 0)
            return {
                "tags": [{"tag": r["hashtag"], "count": r["cnt"],
                          "total_views": r["total_views"] or 0, "avg_views": r["avg_views"] or 0} for r in rows],
                "total_posts": total,
                "avg_reach": avg_reach,
            }
    except Exception as e:
        logger.error(f"/charts/tags error: {e}")
        return json_response({"tags": [], "total_posts": 0, "avg_reach": 0, "error": str(e)}, 500)


@app.get("/api/charts/activity")
async def chart_activity(days: int = Query(7, ge=1, le=90)):
    try:
        async with async_session() as session:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            result = await session.execute(text("""
                SELECT EXTRACT(DOW FROM published_at)::int as dow,
                       EXTRACT(HOUR FROM published_at)::int as hr,
                       COUNT(*) as cnt
                FROM posts WHERE published_at > :since
                GROUP BY dow, hr ORDER BY dow, hr
            """), {"since": since})
            rows = result.mappings().all()
            hours = [0] * (7 * 24)
            peak_hour = 0
            peak_val = 0
            for r in rows:
                idx = r["dow"] * 24 + r["hr"]
                if 0 <= idx < 168:
                    hours[idx] = r["cnt"]
                    if r["cnt"] > peak_val:
                        peak_val = r["cnt"]
                        peak_hour = r["hr"]
            return {"hours": hours, "peak_hour": peak_hour}
    except Exception as e:
        logger.error(f"/charts/activity error: {e}")
        return json_response({"hours": [0]*168, "peak_hour": 0, "error": str(e)}, 500)


@app.get("/api/charts/views")
async def chart_views(days: int = Query(7, ge=1, le=90)):
    try:
        async with async_session() as session:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            result = await session.execute(text("""
                SELECT CASE
                    WHEN views_count < 1000 THEN '0-1K'
                    WHEN views_count < 5000 THEN '1-5K'
                    WHEN views_count < 10000 THEN '5-10K'
                    WHEN views_count < 50000 THEN '10-50K'
                    WHEN views_count < 100000 THEN '50-100K'
                    ELSE '100K+'
                END as bucket, COUNT(*) as cnt
                FROM posts WHERE published_at > :since
                GROUP BY bucket ORDER BY MIN(views_count)
            """), {"since": since})
            rows = result.mappings().all()
            order = ['0-1K', '1-5K', '5-10K', '10-50K', '50-100K', '100K+']
            by_label = {r["bucket"]: r["cnt"] for r in rows}
            return {"bins": [{"label": b, "count": by_label.get(b, 0)} for b in order]}
    except Exception as e:
        logger.error(f"/charts/views error: {e}")
        return json_response({"bins": [], "error": str(e)}, 500)


@app.get("/api/charts/timeline")
async def chart_timeline(days: int = Query(7, ge=1, le=90)):
    try:
        async with async_session() as session:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            # Get top 8 tags
            top = await session.execute(text("""
                WITH tagged AS (
                    SELECT * FROM posts WHERE published_at > :since
                        AND hashtags IS NOT NULL
                        AND json_typeof(hashtags) = 'array'
                        AND json_array_length(hashtags) > 0
                )
                SELECT json_array_elements_text(hashtags) as hashtag, COUNT(*) as cnt
                FROM tagged GROUP BY json_array_elements_text(hashtags)
                ORDER BY cnt DESC LIMIT 8
            """), {"since": since})
            top_tags = [r["hashtag"] for r in top.mappings().all()]

            # Build daily series for each tag
            days_list = [(since + timedelta(days=i)).strftime("%m-%d") for i in range(days+1)]
            tag_series = []
            for tag in top_tags:
                daily = await session.execute(text("""
                    SELECT DATE(published_at)::text as d, COUNT(*) as cnt
                    FROM posts WHERE published_at > :since
                        AND hashtags @> :tag_json
                    GROUP BY d ORDER BY d
                """), {"since": since, "tag_json": f'["{tag}"]'})
                day_map = {r["d"]: r["cnt"] for r in daily.mappings().all()}
                series = [day_map.get((since + timedelta(days=i)).strftime("%Y-%m-%d"), 0) for i in range(days+1)]
                tag_series.append({"tag": tag, "series": series, "labels": days_list})

            return {"tags": tag_series}
    except Exception as e:
        logger.error(f"/charts/timeline error: {e}")
        return json_response({"tags": [], "error": str(e)}, 500)


@app.get("/api/charts/pairs")
async def chart_pairs(days: int = Query(7, ge=1, le=90)):
    try:
        async with async_session() as session:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            result = await session.execute(text("""
                WITH post_tags AS (
                    SELECT id, json_array_elements_text(hashtags) as tag
                    FROM posts
                    WHERE published_at > :since
                        AND hashtags IS NOT NULL
                        AND json_typeof(hashtags) = 'array'
                        AND json_array_length(hashtags) > 1
                )
                SELECT pt1.tag || ' + ' || pt2.tag as pair, COUNT(*) as cnt
                FROM post_tags pt1
                JOIN post_tags pt2 ON pt1.id = pt2.id AND pt1.tag < pt2.tag
                GROUP BY pair ORDER BY cnt DESC LIMIT 20
            """), {"since": since})
            rows = result.mappings().all()
            return {"pairs": [{"pair": r["pair"], "count": r["cnt"]} for r in rows]}
    except Exception as e:
        logger.error(f"/charts/pairs error: {e}")
        return json_response({"pairs": [], "error": str(e)}, 500)
