# Production image for the API and the cron jobs. Render runs this same image for
# all three services; only the start command differs (see render.yaml).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 1000 appuser
USER appuser

# Render sets PORT. The default is for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# The start command is shown here for the web service. render.yaml sets it explicitly.
# --proxy-headers and --forwarded-allow-ips make the rate limit see real client
# addresses. Only Render's proxy can reach this container, so trusting forwarded
# headers is safe here.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
