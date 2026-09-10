FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DENO_INSTALL=/root/.deno \
    PATH="/root/.deno/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    unzip \
    ffmpeg \
    && curl -fsSL https://deno.land/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8000", "--timeout", "60", "app:app"]
