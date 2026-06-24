FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN mkdir -p /app/agents/entity_matcher
COPY . /app/agents/entity_matcher
RUN touch /app/agents/__init__.py

ENV PORT=8080
CMD exec uvicorn agents.entity_matcher.app:app --host 0.0.0.0 --port ${PORT}
