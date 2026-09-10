# Fast yt-dlp API — AWS Docker setup

This build includes:
- Python 3.12
- yt-dlp
- ffmpeg
- Deno JavaScript runtime
- FastAPI
- Docker Compose
- host port 8010 -> container port 8000
- cookies mounted at `/app/cookies.txt`

## Setup

```bash
cp env.example .env
```

Edit `.env` and set a strong `API_KEY`.

Place a fresh Netscape-format `cookies.txt` in this folder.

Build and run:

```bash
docker compose up -d --build
```

Check:

```bash
docker compose ps
docker exec yt-fast-api deno --version
curl http://127.0.0.1:8010/health
```

Search test:

```bash
curl --get \
  --data-urlencode "q=arijit singh" \
  --data-urlencode "limit=5" \
  -H "X-API-Key: YOUR_API_KEY" \
  http://127.0.0.1:8010/search
```

Best-audio extraction test:

```bash
docker exec yt-fast-api yt-dlp \
  -f "bestaudio/best" \
  --cookies /app/cookies.txt \
  --skip-download \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

Do not commit `.env` or `cookies.txt` to GitHub.
