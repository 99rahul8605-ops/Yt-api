import os, time, asyncio
from fastapi import FastAPI, HTTPException, Header, Query
from yt_dlp import YoutubeDL

app = FastAPI(title="Fast yt-dlp API")

COOKIE_FILE = os.getenv("COOKIE_FILE", "cookies.txt")
API_KEY = os.getenv("API_KEY", "").strip()
SEARCH_TTL = int(os.getenv("SEARCH_TTL", "300"))
EXTRACT_TTL = int(os.getenv("EXTRACT_TTL", "600"))

search_cache = {}
extract_cache = {}

search_opts = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": True,
    "noplaylist": True,
}
extract_opts = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
    "format": "bestaudio/best",
}
if os.path.exists(COOKIE_FILE):
    search_opts["cookiefile"] = COOKIE_FILE
    extract_opts["cookiefile"] = COOKIE_FILE

search_ydl = YoutubeDL(search_opts)
extract_ydl = YoutubeDL(extract_opts)

search_lock = asyncio.Lock()
extract_lock = asyncio.Lock()

def auth(key):
    if API_KEY and key != API_KEY:
        raise HTTPException(401, "Invalid API key")

def cache_get(cache, key):
    item = cache.get(key)
    if not item:
        return None
    exp, val = item
    if time.time() >= exp:
        cache.pop(key, None)
        return None
    return val

def cache_set(cache, key, val, ttl):
    cache[key] = (time.time() + ttl, val)

@app.get("/health")
async def health():
    return {
        "ok": True,
        "cookies_found": os.path.exists(COOKIE_FILE),
        "search_cache": len(search_cache),
        "extract_cache": len(extract_cache),
    }

@app.get("/search")
async def search(q: str = Query(..., min_length=1),
                 limit: int = Query(5, ge=1, le=20),
                 x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    key = f"{q.strip().lower()}::{limit}"
    cached = cache_get(search_cache, key)
    if cached is not None:
        return {"ok": True, "cached": True, "results": cached}

    async with search_lock:
        cached = cache_get(search_cache, key)
        if cached is not None:
            return {"ok": True, "cached": True, "results": cached}

        try:
            data = await asyncio.to_thread(
                search_ydl.extract_info, f"ytsearch{limit}:{q}", False
            )
        except Exception as e:
            raise HTTPException(502, f"yt-dlp search failed: {e}")

        results = []
        for e in (data or {}).get("entries", []):
            if not e:
                continue
            vid = e.get("id")
            results.append({
                "id": vid,
                "title": e.get("title"),
                "duration": e.get("duration"),
                "channel": e.get("channel") or e.get("uploader"),
                "thumbnail": e.get("thumbnail"),
                "webpage_url": e.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={vid}" if vid else None
                ),
            })

        cache_set(search_cache, key, results, SEARCH_TTL)
        return {"ok": True, "cached": False, "results": results}

@app.get("/extract")
async def extract(url: str = Query(..., min_length=5),
                  x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    cached = cache_get(extract_cache, url)
    if cached is not None:
        return {"ok": True, "cached": True, "data": cached}

    async with extract_lock:
        cached = cache_get(extract_cache, url)
        if cached is not None:
            return {"ok": True, "cached": True, "data": cached}

        try:
            info = await asyncio.to_thread(extract_ydl.extract_info, url, False)
        except Exception as e:
            raise HTTPException(502, f"yt-dlp extract failed: {e}")

        result = {
            "id": info.get("id"),
            "title": info.get("title"),
            "duration": info.get("duration"),
            "channel": info.get("channel") or info.get("uploader"),
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url") or url,
            "stream_url": info.get("url"),
            "ext": info.get("ext"),
            "acodec": info.get("acodec"),
            "abr": info.get("abr"),
        }
        cache_set(extract_cache, url, result, EXTRACT_TTL)
        return {"ok": True, "cached": False, "data": result}
