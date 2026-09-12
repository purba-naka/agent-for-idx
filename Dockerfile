FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Jalankan dengan .env di-mount atau --env-file:
#   docker build -t idx-webhook-gateway .
#   docker run --env-file .env -p 8000:8000 idx-webhook-gateway
# Untuk persistensi database SQLite, mount volume ke /srv/data dan set
# DATABASE_PATH=/srv/data/idx_agent.sqlite3
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
