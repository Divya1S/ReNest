FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 dormcycle \
    && mkdir -p /app/media \
    && chown -R dormcycle:dormcycle /app
USER dormcycle

EXPOSE 8000

# ASGI server so SSE (/api/events) doesn't pin a worker per connection.
# docker-compose overrides this with `manage.py runserver` for local dev.
CMD ["uvicorn", "dormcycle.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
