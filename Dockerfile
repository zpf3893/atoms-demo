FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATABASE_PATH=/data/demo.db
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home demo && mkdir /data && chown demo:demo /data
COPY --chown=demo:demo app ./app
USER demo
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
