# Yt-api fast fixed

This version is optimized for fast search + fresh audio URL extraction.

## Start

```bash
cp env.example .env
docker compose up -d --build
```

Put a fresh Netscape-format `cookies.txt` beside `docker-compose.yml`.

## Health

```bash
curl http://127.0.0.1:8010/health
```

## Search

```bash
curl --get \
  --data-urlencode "q=arijit singh" \
  --data-urlencode "limit=5" \
  -H "X-API-Key: YOUR_KEY" \
  http://127.0.0.1:8010/search
```

The JSON includes `elapsed` seconds.

## Song/audio URL

```bash
curl \
  -H "X-API-Key: YOUR_KEY" \
  http://127.0.0.1:8010/song/VIDEO_ID
```

Response contains `link`, which is the extracted audio media URL.

## Browser testing

This version also accepts `api_key=` in the query string:

```
http://SERVER_IP:8010/search?q=arijit%20singh&limit=5&api_key=YOUR_KEY
```

and:

```
http://SERVER_IP:8010/song/VIDEO_ID?api_key=YOUR_KEY
```

Do not publish URLs containing your real API key.
