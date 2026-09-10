import os
import time
import subprocess
import requests
import yt_dlp
import yt_dlp.version
from flask import Flask, jsonify, request

app = Flask(__name__)

COOKIE_URL = os.getenv("COOKIE_URL", "")
COOKIE_FILE = "/tmp/cookies.txt"
API_KEY = os.getenv("API_KEY", "")
CACHE_TTL = int(os.getenv("CACHE_TTL", 3600))

cache = {}

print(f"yt-dlp version: {yt_dlp.version.__version__}")
try:
    deno_ver = subprocess.check_output(["deno", "--version"], text=True).splitlines()[0]
    print(f"Deno version: {deno_ver}")
except Exception as e:
    print(f"Deno not found: {e}")


def get_cache(key):
    if key in cache:
        data, ts = cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
        del cache[key]
    return None


def set_cache(key, data):
    cache[key] = (data, time.time())


def download_cookies():
    if COOKIE_URL:
        try:
            r = requests.get(COOKIE_URL, timeout=10)
            content = r.text.strip()
            lines = [l for l in content.splitlines() if not l.startswith("<") and not l.startswith("#!")]
            content = "\n".join(lines).strip()
            if not content.startswith("# Netscape HTTP Cookie File"):
                content = "# Netscape HTTP Cookie File\n" + content
            with open(COOKIE_FILE, "w") as f:
                f.write(content)
            print("Cookies downloaded successfully!")
        except Exception as e:
            print(f"Cookie download failed: {e}")


def get_base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"deno": {}},
        "remote_components": {"ejs:npm"},
    }
    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts


def check_auth():
    if not API_KEY:
        return True
    key = request.headers.get("X-API-Key") or request.args.get("api_key") or request.args.get("api")
    return key == API_KEY


download_cookies()


@app.route("/")
def index():
    try:
        deno_ver = subprocess.check_output(["deno", "--version"], text=True).splitlines()[0]
    except:
        deno_ver = "not found"
    return jsonify({
        "status": "running",
        "message": "YouTube API is live!",
        "yt_dlp_version": yt_dlp.version.__version__,
        "deno_version": deno_ver,
        "cache_size": len(cache),
    })


# === AnnieXMusic compatible endpoints ===

@app.route("/song/<video_id>")
def song(video_id):
    """Audio endpoint - AnnieXMusic format"""
    if not check_auth():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    cache_key = f"song:{video_id}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = get_base_opts()
    opts["format"] = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info.get("url")
            if not stream_url and info.get("requested_formats"):
                for f in info["requested_formats"]:
                    if f.get("acodec") != "none":
                        stream_url = f.get("url")
                        break
            ext = info.get("ext", "m4a")
            data = {
                "status": "done",
                "link": stream_url,
                "format": ext,
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "channel": info.get("channel") or info.get("uploader"),
            }
            set_cache(cache_key, data)
            return jsonify(data)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/video/<video_id>")
def video(video_id):
    """Video endpoint - AnnieXMusic format"""
    if not check_auth():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    cache_key = f"video:{video_id}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = get_base_opts()
    opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio/best[height<=720]/best"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info.get("url")
            if not stream_url and info.get("requested_formats"):
                for f in info["requested_formats"]:
                    if f.get("vcodec") != "none":
                        stream_url = f.get("url")
                        break
            ext = info.get("ext", "mp4")
            data = {
                "status": "done",
                "link": stream_url,
                "format": ext,
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
            }
            set_cache(cache_key, data)
            return jsonify(data)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# === Extra endpoints ===

@app.route("/search")
def search():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    query = request.args.get("q")
    limit = int(request.args.get("limit", 5))
    if not query:
        return jsonify({"error": "Query parameter 'q' required"}), 400

    cache_key = f"search:{query}:{limit}"
    cached = get_cache(cache_key)
    if cached:
        return jsonify({"results": cached, "cached": True})

    opts = get_base_opts()
    opts["extract_flat"] = True
    opts["default_search"] = f"ytsearch{limit}"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            results = ydl.extract_info(query, download=False)
            data = []
            for e in results.get("entries", []):
                data.append({
                    "id": e.get("id"),
                    "title": e.get("title"),
                    "duration": e.get("duration"),
                    "url": f"https://youtube.com/watch?v={e.get('id')}",
                    "thumbnail": e.get("thumbnail"),
                    "channel": e.get("channel") or e.get("uploader"),
                    "views": e.get("view_count"),
                })
            set_cache(cache_key, data)
            return jsonify({"results": data, "cached": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reload_cookies")
def reload_cookies():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    download_cookies()
    return jsonify({"status": "Cookies reloaded!"})


@app.route("/clear_cache")
def clear_cache():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    cache.clear()
    return jsonify({"status": "Cache cleared!"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
