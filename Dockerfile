FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 写入 git commit 供 /version 接口展示
RUN (apt-get update -qq && apt-get install -y --no-install-recommends git) \
    && (git rev-parse HEAD 2>/dev/null > VERSION || echo "unknown" > VERSION) \
    && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

EXPOSE 8000

CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
