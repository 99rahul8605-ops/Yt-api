import os
import subprocess
import requests
import yt_dlp
import yt_dlp.version
from flask import Flask, jsonify, request

app = Flask(__name__)

COOKIE_URL = os.getenv("COOKIE_URL", "")
COOKIE_FILE = "/tmp/cookies.txt"
API_KEY = os.getenv("API_KEY", "")

print(f"yt-dlp version: {yt_dlp.version.__version__}")
try:
    deno_ver = subprocess.check_output(["deno", "--version"], text=True).splitlines()[0]
    print(f"Deno version: {deno_ver}")
except Exception as e:
    print(f"Deno not found: {e}")


def download_cookies():
    if COOKIE_URL:
        try:
            r = requests.get(COOKIE_URL, timeout=10)
            content = r.text.strip()
            lines = content.splitlines()
            clean_lines = []
            for line in lines:
                if line.startswith("<") or line.startswith("#!"):
                    continue
                clean_lines.append(line)
            content = "\n".join(clean_lines).strip()
            if not content.startswith("# Netscape HTTP Cookie File"):
                content = "# Netscape HTTP Cookie File\n" + content
            with open(COOKIE_FILE, "w") as f:
                f.write(content)
            print("Cookies downloaded and cleaned successfully!")
        except Exception as e:
            print(f"Cookie download failed: {e}")


def build_format_selector(quality: str) -> str:
    """Build yt-dlp format selector — same logic as downloader.py"""
    if quality == "best":
        return "bestvideo+bestaudio/best"
    target_h = {
        "360p": 360, "480p": 480, "720p": 720,
        "1080p": 1080, "1440p": 1440, "2160p": 2160, "4k": 2160
    }.get(quality, 1080)
    return (
        f"bestvideo[height<={target_h}]+bestaudio"
        f"/best[height<={target_h}]"
        f"/bestvideo+bestaudio"
        f"/best"
    )


def get_ydl_opts(quality: str = "best"):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": build_format_selector(quality),
        "format_sort": ["res", "vcodec:h264", "acodec:m4a", "br"],
    }
    if False and os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts


def check_auth():
    if not API_KEY:
        return True
    key = request.headers.get("X-API-Key") or request.args.get("api_key")
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
    })


@app.route("/formats")
def list_formats():
    """List all available formats for a video"""
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "URL parameter required"}), 400
    opts = {"quiet": True, "no_warnings": True}
    if False and os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            for f in info.get("formats", []):
                formats.append({
                    "format_id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "acodec": f.get("acodec"),
                    "vcodec": f.get("vcodec"),
                    "height": f.get("height"),
                    "abr": f.get("abr"),
                    "format_note": f.get("format_note"),
                })
            return jsonify({"title": info.get("title"), "formats": formats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/search")
def search():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    query = request.args.get("q")
    limit = int(request.args.get("limit", 5))
    if not query:
        return jsonify({"error": "Query parameter 'q' required"}), 400
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
            "default_search": f"ytsearch{limit}"}
    if False and os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            results = ydl.extract_info(query, download=False)
            entries = results.get("entries", [])
            data = []
            for e in entries:
                data.append({
                    "id": e.get("id"),
                    "title": e.get("title"),
                    "duration": e.get("duration"),
                    "url": f"https://youtube.com/watch?v={e.get('id')}",
                    "thumbnail": e.get("thumbnail"),
                    "channel": e.get("channel") or e.get("uploader"),
                    "views": e.get("view_count"),
                })
            return jsonify({"results": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/link")
def get_link():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    url = request.args.get("url")
    quality = request.args.get("quality", "best")
    if not url:
        return jsonify({"error": "URL parameter required"}), 400
    opts = get_ydl_opts(quality)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "stream_url": info.get("url"),
                "channel": info.get("channel") or info.get("uploader"),
                "format": info.get("format"),
                "height": info.get("height"),
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reload_cookies")
def reload_cookies():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    download_cookies()
    return jsonify({"status": "Cookies reloaded!"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
