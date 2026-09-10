# Fast yt-dlp API — AWS Docker setup

## 1. Files
Put your Netscape-format `cookies.txt` in this folder.

## 2. Configure
Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit the API key in `.env`.

## 3. Build and start
```bash
docker compose up -d --build
```

## 4. Check
```bash
docker compose ps
docker compose logs -f
curl http://127.0.0.1:8000/health
```

## 5. Search test
```bash
curl --get   --data-urlencode "q=arijit singh"   --data-urlencode "limit=5"   -H "X-API-Key: YOUR_API_KEY"   http://127.0.0.1:8000/search
```

## 6. Extract test
```bash
curl --get   --data-urlencode "url=https://www.youtube.com/watch?v=VIDEO_ID"   -H "X-API-Key: YOUR_API_KEY"   http://127.0.0.1:8000/extract
```

## AWS note
Do not expose port 8000 to `0.0.0.0/0` unless necessary. Prefer allowing only your bot server IP in the EC2 Security Group, or put the API behind a reverse proxy/HTTPS.
