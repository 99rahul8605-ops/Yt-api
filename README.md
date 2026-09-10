# Fast yt-dlp API

Install:
```bash
pip install -r requirements.txt
```

Run:
```bash
export API_KEY='your-secret-key'
uvicorn api:app --host 0.0.0.0 --port 8000
```

Health:
```bash
curl http://127.0.0.1:8000/health
```

Search:
```bash
curl --get --data-urlencode "q=arijit singh" --data-urlencode "limit=5" \
-H "X-API-Key: your-secret-key" http://127.0.0.1:8000/search
```

Extract:
```bash
curl --get --data-urlencode "url=https://www.youtube.com/watch?v=VIDEO_ID" \
-H "X-API-Key: your-secret-key" http://127.0.0.1:8000/extract
```

The API creates long-lived yt-dlp objects once at startup. Search uses extract_flat=True and a short in-memory cache, so repeated searches can return almost instantly. Full extraction happens only when /extract is called.
