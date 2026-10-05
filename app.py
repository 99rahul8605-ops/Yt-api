import os
import time
import subprocess
import requests
import yt_dlp
import yt_dlp.version
from flask import Flask, jsonify, request, Response, stream_with_context

app = Flask(__name__)

COOKIE_URL = os.getenv("COOKIE_URL", "")
COOKIE_FILE = os.getenv("COOKIE_FILE", "/app/cookies.txt")
API_KEY = os.getenv("API_KEY", "")
# Separate cache TTLs.
# Signed YouTube media URLs should be cached only briefly; search results can
# safely live longer.
SONG_CACHE_TTL = max(0, int(os.getenv("SONG_CACHE_TTL", "15")))
SEARCH_CACHE_TTL = max(0, int(os.getenv("SEARCH_CACHE_TTL", "300")))
VIDEO_CACHE_TTL = max(0, int(os.getenv("VIDEO_CACHE_TTL", "60")))
DEFAULT_CACHE_TTL = max(0, int(os.getenv("CACHE_TTL", "60")))

cache = {}

print(f"yt-dlp version: {yt_dlp.version.__version__}")
try:
    deno_ver = subprocess.check_output(["deno", "--version"], text=True).splitlines()[0]
    print(f"Deno version: {deno_ver}")
except Exception as e:
    print(f"Deno not found: {e}")


def _cache_ttl_for_key(key):
    if key.startswith("song:"):
        return SONG_CACHE_TTL
    if key.startswith("search:"):
        return SEARCH_CACHE_TTL
    if key.startswith("video:"):
        return VIDEO_CACHE_TTL
    return DEFAULT_CACHE_TTL


def get_cache(key):
    if key not in cache:
        return None

    data, ts = cache[key]
    ttl = _cache_ttl_for_key(key)

    if ttl > 0 and time.time() - ts < ttl:
        return data

    cache.pop(key, None)
    return None


def set_cache(key, data):
    cache[key] = (data, time.time())


def wants_fresh():
    """Allow callers to force a fresh yt-dlp extraction with ?fresh=1."""
    value = str(request.args.get("fresh", "")).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


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
        "audio_proxy": True,
        "cache_ttl": {
            "song": SONG_CACHE_TTL,
            "search": SEARCH_CACHE_TTL,
            "video": VIDEO_CACHE_TTL,
        },
    })


def _extract_audio_data(video_id, force_fresh=False):
    """
    Extract one playable audio URL.

    This helper is shared by /song and /audio. Keeping extraction on the API
    host avoids giving the bot VPS a raw googlevideo URL that may be tied to
    the API host/session/IP.
    """
    cache_key = f"song:{video_id}"

    if force_fresh:
        cache.pop(cache_key, None)
    else:
        cached = get_cache(cache_key)
        if cached:
            return cached

    url = f"https://www.youtube.com/watch?v={video_id}"
    format_candidates = [
        "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
        "bestaudio/best",
        "best",
    ]

    last_error = None

    for selector in format_candidates:
        opts = get_base_opts()
        opts["format"] = selector

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            stream_url = info.get("url")
            chosen = info

            if not stream_url and info.get("requested_formats"):
                for fmt in info["requested_formats"]:
                    if fmt.get("acodec") != "none" and fmt.get("url"):
                        stream_url = fmt.get("url")
                        chosen = {**info, **fmt}
                        break

            if not stream_url and info.get("formats"):
                audio_formats = [
                    fmt
                    for fmt in info["formats"]
                    if fmt.get("url")
                    and fmt.get("acodec") not in (None, "none")
                ]
                if audio_formats:
                    audio_formats.sort(
                        key=lambda fmt: (
                            fmt.get("abr") or 0,
                            fmt.get("tbr") or 0,
                        ),
                        reverse=True,
                    )
                    chosen = {**info, **audio_formats[0]}
                    stream_url = audio_formats[0].get("url")

            if not stream_url:
                raise RuntimeError("No playable audio URL found")

            data = {
                "status": "done",
                "link": stream_url,
                "format": chosen.get("ext") or info.get("ext") or "webm",
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "channel": info.get("channel") or info.get("uploader"),
                "selector": selector,
                # yt-dlp sometimes returns headers that are useful when
                # requesting the signed media URL.
                "http_headers": info.get("http_headers") or {},
                "cached": False,
            }
            set_cache(cache_key, data)
            return data

        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        str(last_error) if last_error else "Audio extraction failed"
    )


def _open_audio_upstream(video_id, range_header=None, force_fresh=False):
    data = _extract_audio_data(video_id, force_fresh=force_fresh)
    stream_url = data["link"]

    headers = {
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }

    for key, value in (data.get("http_headers") or {}).items():
        if key and value:
            headers[str(key)] = str(value)

    if range_header:
        headers["Range"] = range_header

    upstream = requests.get(
        stream_url,
        headers=headers,
        stream=True,
        allow_redirects=True,
        timeout=(10, 35),
    )
    return upstream, data

# === AnnieXMusic compatible endpoints ===

@app.route("/song/<video_id>")
def song(video_id):
    """Return extracted audio metadata / signed URL."""
    if not check_auth():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    try:
        data = _extract_audio_data(video_id, force_fresh=wants_fresh())
        return jsonify(data)
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@app.route("/audio/<video_id>", methods=["GET", "HEAD"])
def audio_proxy(video_id):
    """
    Stable streaming proxy for voice-chat playback.

    FFmpeg/PyTgCalls connects to this API URL instead of directly to the
    googlevideo signed URL. Range requests are forwarded. If YouTube rejects
    a cached signed URL with 403, the API re-extracts once and retries.
    """
    if not check_auth():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    range_header = request.headers.get("Range")

    try:
        upstream, data = _open_audio_upstream(
            video_id,
            range_header=range_header,
            force_fresh=wants_fresh(),
        )

        # A cached signed URL can occasionally expire/become invalid.
        # Refresh it once on the API host and retry there.
        if upstream.status_code == 403:
            upstream.close()
            cache.pop(f"song:{video_id}", None)
            upstream, data = _open_audio_upstream(
                video_id,
                range_header=range_header,
                force_fresh=True,
            )

        if upstream.status_code not in (200, 206):
            status = upstream.status_code
            upstream.close()
            return jsonify({
                "status": "error",
                "message": f"Upstream media returned {status}",
            }), status

        response_headers = {
            "Content-Type": upstream.headers.get(
                "Content-Type",
                "audio/mp4" if data.get("format") == "m4a" else "audio/webm",
            ),
            "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
            "Cache-Control": "no-store",
        }

        for header_name in ("Content-Length", "Content-Range"):
            value = upstream.headers.get(header_name)
            if value:
                response_headers[header_name] = value

        if request.method == "HEAD":
            status = upstream.status_code
            upstream.close()
            return Response(status=status, headers=response_headers)

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return Response(
            stream_with_context(generate()),
            status=upstream.status_code,
            headers=response_headers,
            direct_passthrough=True,
        )

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@app.route("/video/<video_id>")
def video(video_id):
    """Video endpoint - AnnieXMusic format"""
    if not check_auth():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    cache_key = f"video:{video_id}"
    if wants_fresh():
        cache.pop(cache_key, None)
    else:
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

    query = (request.args.get("q") or "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", 5)), 20))
    except Exception:
        limit = 5

    if not query:
        return jsonify({"error": "Query parameter 'q' required"}), 400

    # Normalize whitespace so equivalent searches share one cache entry.
    normalized_query = " ".join(query.split())
    cache_key = f"search:{normalized_query.lower()}:{limit}"

    if wants_fresh():
        cache.pop(cache_key, None)
    else:
        cached = get_cache(cache_key)
        if cached:
            return jsonify({
                "results": cached,
                "cached": True,
            })

    opts = get_base_opts()
    opts["extract_flat"] = True

    try:
        # Explicit ytsearch is more deterministic than relying only on
        # default_search for a plain text string.
        target = f"ytsearch{limit}:{normalized_query}"

        with yt_dlp.YoutubeDL(opts) as ydl:
            results = ydl.extract_info(target, download=False) or {}

        data = []
        for entry in results.get("entries") or []:
            if not entry:
                continue

            video_id = entry.get("id")
            if not video_id:
                continue

            data.append({
                "id": video_id,
                "title": entry.get("title"),
                "duration": entry.get("duration"),
                "url": f"https://youtube.com/watch?v={video_id}",
                "thumbnail": entry.get("thumbnail"),
                "channel": entry.get("channel") or entry.get("uploader"),
                "views": entry.get("view_count"),
            })

        # IMPORTANT: never cache [].
        # A temporary YouTube extraction failure must not poison the query for
        # SEARCH_CACHE_TTL seconds.
        if data:
            set_cache(cache_key, data)

        return jsonify({
            "results": data,
            "cached": False,
        })

    except Exception as e:
        return jsonify({
            "error": str(e),
            "results": [],
        }), 500


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
