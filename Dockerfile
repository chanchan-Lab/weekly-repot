FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m bot && mkdir /app/data && chown bot:bot /app/data
COPY app ./app
COPY templates ./templates
USER bot
CMD ["python", "-m", "app.bot"]
