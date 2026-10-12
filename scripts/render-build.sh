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

# WhiteNoise never compresses on the fly: it serves a gzip response only when
# a .gz file already sits next to the original. collectstatic produces those
# for /static/, but nothing did for the SPA, so the JS and CSS bundles went
# out uncompressed. (Brotli variants are written too if the brotli package is
# ever installed.)
echo "── Precompress the SPA"
python -m whitenoise.compress --quiet frontend/dist

echo "── Collect static + migrate"
cd backend
python manage.py collectstatic --noinput
python manage.py migrate --noinput

echo "── Build complete (SPA at frontend/dist, served by Django/WhiteNoise)"
