app.py
Python
isme dekho downlod ka code hoga

Ab ek important cheez mili: tumhara Python test actually run hi nahi hua. docker exec me -i missing tha, isliye heredoc container ke Python tak gaya hi nahi.

Ye exact command chalao:

docker exec -i yt-fast-api python - <<'PY'
import yt_dlp

opts = {
    "verbose": True,
    "skip_download": True,
    "format": "bestaudio/best",
    "cookiefile": "/app/cookies.txt",
    "js_runtimes": {"deno": {}},
    "remote_components": {"ejs:npm"},
}

with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.extract_info(
        "https://www.youtube.com/watch?v=DS-raAyMxl4",
        download=False
    )
    print("\nSUCCESS")
    print(info.get("url"))
PY

Ab output zaroor aayega.

CLI already work kar raha hai:

[jsc:deno] Solving JS challenges using deno
[info] ... format(s): 251

Isliye ye Python test decisive hoga:

SUCCESS + googlevideo URL aaye → API ke app.py me koi aur difference hai.
Sign in to confirm... aaye → Python yt-dlp options CLI ke equivalent nahi ja rahe.

Jo output aaye woh bhejo. Ab exact root cause isolate ho jayega.

song success hogya

Good — iska matlab Python API-style yt-dlp extraction bhi work kar raha hai. So Deno, EJS, cookies, and Python YoutubeDL options sab sahi hain.

Ab remaining issue almost certainly running /song endpoint ke actual code path me hai — ya old options use ho rahe hain, ya /song me extra/fallback logic different hai.

Ab ek simple test karo browser me wahi /song URL dubara:

http://35.180.73.26:8010/song/DS-raAyMxl4?api_key=6f42736a5ee67b7e57f9845f277bf8fb6b228b0180f70a2a99cdb1e111336510

Agar ab bhi fail hota hai, mujhe current /song function ka container wala exact code bhejo:

docker exec yt-fast-api sed -n '85,130p' /app/app.py

Usko Python-success test ke exact equivalent bana denge.

"message":"ERROR: [youtube] DS-raAyMxl4: Sign in to confirm you\u2019re not a bot. Use --cookies-from-browser or --cookies for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies. Also see  https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies  for tips on effectively exporting YouTube cookies","status":"error"}
Analyzed
from pathlib import Path

src = Path("/mnt/data/app.py")
text = src.read_text(encoding="utf-8")

text = text.replace(
    'COOKIE_FILE = "/tmp/cookies.txt"',
    'COOKIE_FILE = os.getenv("COOKIE_FILE", "/app/cookies.txt")'
)

text = text.replace(
    'opts = {"quiet": True, "no_warnings": True}',
    '''opts = {
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"deno": {}},
        "remote_components": {"ejs:npm"},
    }'''
)

out = Path("/mnt/data/app-final-fixed.py")
out.write_text(text, encoding="utf-8")
print(out)

STDOUT/STDERR
/mnt/data/app-final-fixed.py


Root cause mil gaya. Tumhare API code me cookie path hardcoded tha:

COOKIE_FILE = "/tmp/cookies.txt"

Lekin Docker me actual working cookies yahan mounted hain:

/app/cookies.txt

CLI isi /app/cookies.txt ko use karke successful hua, isliye browser API fail kar rahi thi. Original file me hardcoded /tmp/cookies.txt hi tha.

Maine final file me:

cookie path /app/cookies.txt env-aware kar diya
Deno JS runtime add kiya
ejs:npm add kiya

Download final fixed app.py

AWS par isko app.py naam se replace karo, phir:

docker compose down
docker compose up -d --build

Phir browser test:

http://35.180.73.26:8010/song/DS-raAyMxl4?api_key=6f42736a5ee67b7e57f9845f277bf8fb6b228b0180f70a2a99cdb1e111336510

Ab API aur successful CLI dono same cookie file use karenge. 

app-final-fixed.py
Code

Library
/
app-final-fixed.py
999
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
28
29
30
31
32
33
34
35
36
37
38
39
40
41
42
43
44
45
46
47
48
49
50
51
52
53
54
55
56
57
58
59
import os
import time
import subprocess
import requests
import yt_dlp
import yt_dlp.version
from flask import Flask, jsonify, request


app = Flask(__name__)


COOKIE_URL = os.getenv("COOKIE_URL", "")
COOKIE_FILE = os.getenv("COOKIE_FILE", "/app/cookies.txt")
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
