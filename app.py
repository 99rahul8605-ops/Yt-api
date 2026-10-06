import os
from pathlib import Path
import time
import subprocess
import threading
import glob
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

# yt-dlp server-side audio cache.
# HTTP chunking is intentional: it is much more tolerant of YouTube CDN
# throttling/resets than relaying one long requests.get() connection.
AUDIO_DL_CACHE_DIR = os.getenv("AUDIO_DL_CACHE_DIR", "/tmp/yt_audio_dl")
AUDIO_DL_TTL = max(60, int(os.getenv("AUDIO_DL_TTL", "1800")))
AUDIO_DL_MAX_ITEMS = max(5, int(os.getenv("AUDIO_DL_MAX_ITEMS", "40")))
AUDIO_HTTP_CHUNK_SIZE = max(
    256 * 1024,
    int(os.getenv("AUDIO_HTTP_CHUNK_SIZE", str(1024 * 1024))),
)

os.makedirs(AUDIO_DL_CACHE_DIR, exist_ok=True)

cache = {}
_audio_dl_locks = {}
_audio_dl_locks_guard = threading.Lock()

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
        "audio_mode": "ytdlp_chunked_server_download",
        "audio_http_chunk_size": AUDIO_HTTP_CHUNK_SIZE,
        "download_endpoint": "/download/<video_id>",
        "cache_ttl": {
            "song": SONG_CACHE_TTL,
            "search": SEARCH_CACHE_TTL,
            "video": VIDEO_CACHE_TTL,
        },
    })


def _extract_audio_data(video_id, force_fresh=False):
    """
    Extract a stable AUDIO-ONLY stream.

    Important:
    - Prefer YouTube m4a/AAC (usually itag 140) for VC compatibility.
    - Never intentionally fall back to a combined video+audio "best" stream.
    - webm/opus is used only when no m4a/AAC audio-only stream is available.
    """
    cache_key = f"song:{video_id}"

    if force_fresh:
        cache.pop(cache_key, None)
    else:
        cached = get_cache(cache_key)
        if cached:
            return cached

    url = f"https://www.youtube.com/watch?v={video_id}"

    # Keep every choice audio-only. The old code eventually used "best",
    # which can be a combined A/V format and behaves differently per video.
    format_candidates = [
        "bestaudio[ext=m4a][acodec^=mp4a]/bestaudio[ext=m4a]",
        "bestaudio[acodec^=mp4a]/bestaudio[acodec^=aac]",
        "bestaudio[ext=webm][acodec=opus]/bestaudio[ext=webm]",
        "bestaudio",
    ]

    last_error = None

    for selector in format_candidates:
        opts = get_base_opts()
        opts["format"] = selector

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            chosen = info
            stream_url = info.get("url")

            # Some yt-dlp results expose selected formats separately.
            if (not stream_url or info.get("vcodec") not in (None, "none")) and info.get("requested_formats"):
                selected_audio = None
                for fmt in info["requested_formats"]:
                    if (
                        fmt.get("url")
                        and fmt.get("acodec") not in (None, "none")
                        and fmt.get("vcodec") in (None, "none")
                    ):
                        selected_audio = fmt
                        break

                if selected_audio:
                    chosen = {**info, **selected_audio}
                    stream_url = selected_audio.get("url")

            # Absolute safety fallback: choose only an audio-only format.
            if (
                not stream_url
                or chosen.get("acodec") in (None, "none")
                or chosen.get("vcodec") not in (None, "none")
            ):
                audio_formats = [
                    fmt
                    for fmt in (info.get("formats") or [])
                    if (
                        fmt.get("url")
                        and fmt.get("acodec") not in (None, "none")
                        and fmt.get("vcodec") in (None, "none")
                    )
                ]

                # Prefer m4a/AAC first, then highest bitrate audio-only.
                audio_formats.sort(
                    key=lambda fmt: (
                        1 if str(fmt.get("ext") or "").lower() == "m4a" else 0,
                        1 if str(fmt.get("acodec") or "").lower().startswith(("mp4a", "aac")) else 0,
                        fmt.get("abr") or 0,
                        fmt.get("tbr") or 0,
                    ),
                    reverse=True,
                )

                if not audio_formats:
                    raise RuntimeError("No audio-only format available")

                chosen = {**info, **audio_formats[0]}
                stream_url = audio_formats[0].get("url")

            if not stream_url:
                raise RuntimeError("No playable audio URL found")

            if chosen.get("acodec") in (None, "none"):
                raise RuntimeError("Selected format has no audio codec")

            if chosen.get("vcodec") not in (None, "none"):
                raise RuntimeError("Selected format is not audio-only")

            ext = str(chosen.get("ext") or info.get("ext") or "").lower()
            acodec = str(chosen.get("acodec") or "")
            format_id = str(chosen.get("format_id") or "")
            protocol = str(chosen.get("protocol") or "")

            data = {
                "status": "done",
                "link": stream_url,
                "format": ext,
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "channel": info.get("channel") or info.get("uploader"),
                "selector": selector,
                "format_id": format_id,
                "acodec": acodec,
                "vcodec": chosen.get("vcodec"),
                "protocol": protocol,
                "abr": chosen.get("abr") or chosen.get("tbr"),
                "http_headers": (
                    chosen.get("http_headers")
                    or info.get("http_headers")
                    or {}
                ),
                "cached": False,
            }

            print(
                "Audio source selected: "
                f"id={format_id or '?'} "
                f"ext={ext or '?'} "
                f"acodec={acodec or '?'} "
                f"protocol={protocol or '?'}"
            )

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

def _get_audio_dl_lock(video_id):
    with _audio_dl_locks_guard:
        lock = _audio_dl_locks.get(video_id)
        if lock is None:
            lock = threading.Lock()
            _audio_dl_locks[video_id] = lock
        return lock


def _find_api_audio_file(video_id):
    now = time.time()
    for path in glob.glob(os.path.join(AUDIO_DL_CACHE_DIR, f"{video_id}.*")):
        if not os.path.isfile(path):
            continue
        if path.endswith((".part", ".ytdl")):
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size < 16384:
            continue
        if now - st.st_mtime <= AUDIO_DL_TTL:
            return path
    return None


def _cleanup_api_audio_cache():
    try:
        now = time.time()
        files = []
        for path in glob.glob(os.path.join(AUDIO_DL_CACHE_DIR, "*")):
            if not os.path.isfile(path) or path.endswith((".part", ".ytdl")):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue

            if now - st.st_mtime > AUDIO_DL_TTL:
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue

            files.append((st.st_mtime, path))

        files.sort(reverse=True)
        for _, path in files[AUDIO_DL_MAX_ITEMS:]:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        pass


def _download_audio_with_ytdlp(video_id, force_fresh=False):
    """
    Download on AWS using yt-dlp's own downloader.

    Unlike the old /download relay, this does not keep one fragile
    requests.get() connection open to googlevideo. yt-dlp handles:
      - HTTP chunking
      - retries
      - resumed partial downloads
      - YouTube request headers/cookies/runtime
    """
    lock = _get_audio_dl_lock(video_id)

    with lock:
        if not force_fresh:
            cached_path = _find_api_audio_file(video_id)
            if cached_path:
                os.utime(cached_path, None)
                return cached_path

        if force_fresh:
            for old in glob.glob(os.path.join(AUDIO_DL_CACHE_DIR, f"{video_id}.*")):
                try:
                    os.remove(old)
                except OSError:
                    pass

        url = f"https://www.youtube.com/watch?v={video_id}"
        outtmpl = os.path.join(AUDIO_DL_CACHE_DIR, f"{video_id}.%(ext)s")

        opts = get_base_opts()
        opts.update({
            # Keep the same safe VC preference.
            "format": (
                "bestaudio[ext=m4a][acodec^=mp4a]/"
                "bestaudio[ext=m4a]/"
                "bestaudio"
            ),
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 3,
            "socket_timeout": 20,
            # This is the key speed/stability change.
            "http_chunk_size": AUDIO_HTTP_CHUNK_SIZE,
        })

        started = time.monotonic()

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        path = None

        # Newer yt-dlp often exposes the real downloaded filepath here.
        for item in info.get("requested_downloads") or []:
            candidate = item.get("filepath")
            if candidate and os.path.exists(candidate):
                path = candidate
                break

        if not path:
            candidate = info.get("_filename")
            if candidate and os.path.exists(candidate):
                path = candidate

        if not path:
            candidates = [
                p for p in glob.glob(
                    os.path.join(AUDIO_DL_CACHE_DIR, f"{video_id}.*")
                )
                if os.path.isfile(p)
                and not p.endswith((".part", ".ytdl"))
            ]
            if candidates:
                candidates.sort(key=os.path.getmtime, reverse=True)
                path = candidates[0]

        if not path or not os.path.exists(path):
            raise RuntimeError("yt-dlp finished but audio file was not found")

        size = os.path.getsize(path)
        if size < 16384:
            raise RuntimeError(f"yt-dlp audio file too small: {size} bytes")

        elapsed = max(0.001, time.monotonic() - started)
        print(
            f"yt-dlp audio cached: {video_id} "
            f"{size / 1024 / 1024:.1f} MB in {elapsed:.1f}s "
            f"({size / 1024 / 1024 / elapsed:.2f} MB/s)"
        )

        os.utime(path, None)
        _cleanup_api_audio_cache()
        return path



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
        if upstream.status_code in (403, 410):
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
            "X-Audio-Format": str(data.get("format") or ""),
            "X-Audio-Codec": str(data.get("acodec") or ""),
            "X-Audio-Format-Id": str(data.get("format_id") or ""),
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



@app.route("/download/<video_id>", methods=["GET", "HEAD"])
@app.route("/download", methods=["GET", "HEAD"])
def download_audio_bytes(video_id=None):
    """
    Reliable fast download endpoint.

    First request:
      YouTube -> yt-dlp on AWS (chunked/retried) -> AWS local cache -> bot

    Retry/resume:
      bot sends Range -> Flask serves the same AWS local file

    PyTgCalls still receives only the bot's finished local file.
    """
    if not check_auth():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    if not video_id:
        raw = (request.args.get("url") or request.args.get("video_id") or "").strip()
        if "youtu.be/" in raw:
            video_id = raw.split("youtu.be/")[-1].split("?")[0]
        elif "v=" in raw:
            video_id = raw.split("v=")[-1].split("&")[0]
        else:
            video_id = raw

    video_id = (video_id or "").strip()
    if not video_id:
        return jsonify({"status": "error", "message": "video_id required"}), 400

    try:
        path = _download_audio_with_ytdlp(
            video_id,
            force_fresh=wants_fresh(),
        )

        ext = Path(path).suffix.lower().lstrip(".") or "m4a"

        response = send_file(
            path,
            conditional=True,
            as_attachment=False,
            max_age=0,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["X-Audio-Ext"] = ext
        response.headers["X-Video-Id"] = video_id
        return response

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
