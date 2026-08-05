FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mailhub/ mailhub/
# /data is the named volume: hub.sqlite3 (WAL) + blobs/
ENV HUB_DATA=/data
EXPOSE 7370
# 7371 = the FR-10 public listener (API-only; served only when HUB_PUBLIC=1)
EXPOSE 7371
CMD ["python", "-m", "mailhub.serve"]
