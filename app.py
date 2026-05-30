import os
import requests
import yt_dlp
from flask import Flask, jsonify, request

app = Flask(__name__)

COOKIE_URL = os.getenv("COOKIE_URL", "")
COOKIE_FILE = "/tmp/cookies.txt"
API_KEY = os.getenv("API_KEY", "")


def download_cookies():
    if COOKIE_URL:
        try:
            r = requests.get(COOKIE_URL, timeout=10)
            with open(COOKIE_FILE, "w") as f:
                f.write(r.text)
        except Exception as e:
            print(f"Cookie download failed: {e}")


def get_ydl_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
    }
    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts


def check_auth():
    if not API_KEY:
        return True
    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    return key == API_KEY


# Download cookies on startup
download_cookies()


@app.route("/")
def index():
    return jsonify({"status": "running", "message": "YouTube API is live!"})


@app.route("/search")
def search():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    query = request.args.get("q")
    limit = int(request.args.get("limit", 5))

    if not query:
        return jsonify({"error": "Query parameter 'q' required"}), 400

    opts = get_ydl_opts()
    opts["extract_flat"] = True
    opts["default_search"] = f"ytsearch{limit}"

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
    if not url:
        return jsonify({"error": "URL parameter required"}), 400

    opts = get_ydl_opts()

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "stream_url": info.get("url"),
                "channel": info.get("channel") or info.get("uploader"),
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
