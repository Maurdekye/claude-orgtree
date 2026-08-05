FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mailhub/ mailhub/
# /data is the named volume: hub.sqlite3 (WAL) + blobs/
ENV HUB_DATA=/data
EXPOSE 7370
CMD ["uvicorn", "mailhub.app:app", "--host", "0.0.0.0", "--port", "7370"]
