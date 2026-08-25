# Stage 1: frontend build. Runs inside `az acr build`, so no local Node
# install is needed and the image can never ship a stale bundle (the old
# flow copied a locally-built backend/static and silently shipped
# whatever was lying there).
# (The MCR mirror doesn't carry node tags - manifest unknown - so this
# one comes straight from Docker Hub.)
FROM docker.io/library/node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.js points outDir at ../backend/static for local dev;
# override to a fixed path inside this stage.
RUN npm run build -- --outDir /fe/dist --emptyOutDir

FROM mcr.microsoft.com/mirror/docker/library/python:3.11-slim

# Install ODBC driver for Azure SQL
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl gnupg2 apt-transport-https unixodbc-dev && \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app/ ./app/
COPY --from=frontend /fe/dist/ ./static/

# Serve frontend from FastAPI static files
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
