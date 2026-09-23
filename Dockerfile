FROM node:22-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app
RUN python -m slackline.schedule.build_gtfs --out data/schedule_index.sqlite --download \
    && python -m slackline.events.build_events --out data/events.json
COPY --from=web /src/frontend/dist /app/static
ENV STATIC_DIR=/app/static
ENV SCHEDULE_INDEX_PATH=/app/data/schedule_index.sqlite
ENV EVENTS_DATASET_PATH=/app/data/events.json
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn slackline.api:app --host 0.0.0.0 --port ${PORT}"]
