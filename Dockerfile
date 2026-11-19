FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY config ./config
COPY static ./static
ENV AHA_DB=/data/aha.sqlite PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
# AHA_PASSWORD (opzionale ma consigliata se pubblica): password richiesta a tutti (nome utente qualsiasi)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
