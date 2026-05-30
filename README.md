# YouTube API 🎵

Simple YouTube search & stream link API using yt-dlp.

## Endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | API status |
| `/search?q=song name` | GET | Search YouTube |
| `/link?url=youtube_url` | GET | Get stream link |
| `/reload_cookies` | GET | Reload cookies |

## Environment Variables:

| Variable | Description |
|---|---|
| `API_KEY` | Secret key for auth |
| `COOKIE_URL` | Pastebin link for cookies.txt |

## Usage:

### Search:
```
GET /search?q=arijit+singh&limit=5&api_key=your_key
```

### Get Stream Link:
```
GET /link?url=https://youtube.com/watch?v=xxxxx&api_key=your_key
```
