#!/usr/bin/env bash
# Render build for the web service: backend deps + built SPA + static + migrate.
set -euo pipefail

echo "── Backend dependencies"
pip install -r backend/requirements.txt

echo "── Frontend build"
cd frontend
npm ci
npm run build
cd ..

echo "── Collect static + migrate"
cd backend
python manage.py collectstatic --noinput
python manage.py migrate --noinput

echo "── Build complete (SPA at frontend/dist, served by Django/WhiteNoise)"
