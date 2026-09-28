FROM python:3.12-slim
WORKDIR /app
COPY bot.py config.json ./
RUN useradd --uid 10001 --create-home bot && mkdir /state && chown bot:bot /state
USER 10001:10001
CMD ["python", "bot.py", "--daemon", "--dry-run", "--state", "/state/bot.sqlite3"]
