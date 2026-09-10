import os
import time
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
import yt_dlp
import yt_dlp.version
from flask import Flask, jsonify, request

app = Flask(__name__)

COOKIE_URL = os.getenv("COOKIE_URL", "").strip()
COOKIE_FILE = os.getenv("COOKIE_FILE", "/app/cookies.txt")
API_KEY = os.getenv("API_KEY", "").strip()

SEARCH_CACHE_TTL = int(os.getenv("SEARCH_CACHE_TTL", "300"))
SONG_CACHE_TTL = int(os.getenv("SONG_CACHE_TTL", "180"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

cache = {}
cache_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=int(os.getenv("MAX_WORKERS", "4")))

print(f"yt-dlp version: {yt_dlp.version.__version__}")

try:
    deno_ver = subprocess.check_output(
        ["deno", "--version"], text=True, timeout=5
    ).splitlines()[0]
    print(f"Deno version: {deno_ver}")
except Exception as e:
    print(f"Deno not found: {e}")


def get_cache(key):
    with cache_lock:
        item = cache.get(key)
        if not item:
            return None
        data, ts, ttl = item
        if time.time() - ts < ttl:
            return data
        cache.pop(key, None)
    return None


def set_cache(key, data, ttl):
    with cache_lock:
        cache[key] = (data, time.time(), ttl)


def check_auth():
    if not API_KEY:
        return True
    key = (
        request.headers.get("X-API-Key")
        or request.args.get("api_key")
        or request.args.get("api")
    )
    return key == API_KEY


def download_cookies():
    if not COOKIE_URL:
        return False, "COOKIE_URL not configured"

    try:
        r = requests.get(COOKIE_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        content = r.text.strip()
        lines = [
            l for l in content.splitlines()
            if not l.startswith("<") and not l.startswith("#!")
        ]
        content = "\n".join(lines).strip()

        if not content.startswith("# Netscape HTTP Cookie File"):
            content = "# Netscape HTTP Cookie File\n" + content

        os.makedirs(os.path.dirname(COOKIE_FILE) or ".", exist_ok=True)
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            f.write(content + "\n")

        return True, "Cookies downloaded successfully"
    except Exception as e:
        return False, str(e)


def base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": REQUEST_TIMEOUT,
        "retries": 1,
        "fragment_retries": 1,
    }

    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE

    # Deno is enabled by default in modern yt-dlp if present in PATH.
    return opts


def extract_song(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = base_opts()
    opts["format"] = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"

    # Fresh YoutubeDL instance per extraction:
    # avoids stale YouTube page/player state across requests.
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    stream_url = info.get("url")

    if not stream_url:
        for f in info.get("requested_formats") or []:
            if f.get("acodec") and f.get("acodec") != "none" and f.get("url"):
                stream_url = f["url"]
                break

    if not stream_url:
        for f in reversed(info.get("formats") or []):
            if f.get("acodec") and f.get("acodec") != "none" and f.get("url"):
                stream_url = f["url"]
                break

    if not stream_url:
        raise RuntimeError("No usable audio stream URL found")

    return {
        "status": "done",
        "link": stream_url,
        "format": info.get("ext") or "m4a",
        "title": info.get("title"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "channel": info.get("channel") or info.get("uploader"),
        "video_id": video_id,
    }


def extract_search(query, limit):
    opts = base_opts()
    opts.update({
        "extract_flat": True,
        "default_search": f"ytsearch{limit}",
    })

    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.extract_info(query, download=False)

    data = []
    for e in result.get("entries") or []:
        if not e:
            continue
        vid = e.get("id")
        data.append({
            "id": vid,
            "title": e.get("title"),
            "duration": e.get("duration"),
            "url": f"https://youtube.com/watch?v={vid}" if vid else e.get("url"),
            "thumbnail": e.get("thumbnail") or (
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None
            ),
            "channel": e.get("channel") or e.get("uploader"),
            "views": e.get("view_count"),
        })
    return data


@app.route("/")
def index():
    try:
        deno_ver = subprocess.check_output(
            ["deno", "--version"], text=True, timeout=5
        ).splitlines()[0]
    except Exception:
        deno_ver = "not found"

    return jsonify({
        "status": "running",
        "message": "Fast YouTube audio API is live",
        "yt_dlp_version": yt_dlp.version.__version__,
        "deno_version": deno_ver,
        "cookies_found": os.path.exists(COOKIE_FILE),
        "cache_size": len(cache),
    })


@app.route("/health")
def health():
    return index()


@app.route("/search")
def search():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    query = (request.args.get("q") or "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", "5")), 20))
    except ValueError:
        return jsonify({"error": "Invalid limit"}), 400

    if not query:
        return jsonify({"error": "Query parameter 'q' required"}), 400

    key = f"search:{query.lower()}:{limit}"
    cached = get_cache(key)
    if cached is not None:
        return jsonify({"results": cached, "cached": True})

    started = time.perf_counter()

    try:
        data = extract_search(query, limit)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "elapsed": round(time.perf_counter() - started, 3),
        }), 500

    set_cache(key, data, SEARCH_CACHE_TTL)

    return jsonify({
        "results": data,
        "cached": False,
        "elapsed": round(time.perf_counter() - started, 3),
    })


@app.route("/song/<video_id>")
def song(video_id):
    if not check_auth():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    key = f"song:{video_id}"
    cached = get_cache(key)
    if cached is not None:
        return jsonify({**cached, "cached": True, "elapsed": 0})

    started = time.perf_counter()

    try:
        data = extract_song(video_id)
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "elapsed": round(time.perf_counter() - started, 3),
        }), 500

    set_cache(key, data, SONG_CACHE_TTL)

    return jsonify({
        **data,
        "cached": False,
        "elapsed": round(time.perf_counter() - started, 3),
    })


@app.route("/reload_cookies", methods=["POST", "GET"])
def reload_cookies():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    ok, msg = download_cookies()
    if ok:
        with cache_lock:
            cache.clear()
        return jsonify({"status": "ok", "message": msg})

    return jsonify({"status": "error", "message": msg}), 500


@app.route("/clear_cache", methods=["POST", "GET"])
def clear_cache():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    with cache_lock:
        cache.clear()
    return jsonify({"status": "Cache cleared"})


if COOKIE_URL:
    ok, msg = download_cookies()
    print(msg)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
