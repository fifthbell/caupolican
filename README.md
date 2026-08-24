# Caupolicán

A multi-channel HLS router with hot switching, built with FastAPI and ffmpeg.

## Architecture

```
[Live Sources] --> [Channel Workers] --> [HLS Segments]
                     |                        |
                     v                        v
               [FastAPI API] --> [Nginx Static] --> [Players]
                     ^
                     |
               [Global Standby Slate]
```

- **Router**: FastAPI app managing channels, workers, and API.
- **Workers**: Per-channel ffmpeg processes normalizing inputs to H.264/AAC 1080p30 HLS.
- **Standby**: Global SMPTE bars or image + audio slate for fallbacks.
- **Nginx**: Serves HLS segments statically.

Hot switching: Workers copy/link segments from live or standby, inserting discontinuities.

## Quickstart (Local)

1. Build and run:
```
docker build -t caupolican .
docker run –rm -it -p 9999:9999 \
  -v $(pwd)/out:/out \
  caupolican
```

2. API: http://localhost:9999/api/health
3. HLS: http://localhost:9999/hls/news/index.m3u8 (after creating channel)

## Production

After pulling the image:
```
docker pull ghcr.io/${{ github.repository_owner }}/caupolican:latest
docker run -d –name cuapolican \
  –restart=always \
  –ulimit nofile=65536:65536 \
  -e TARGET_DURATION=2 \
  -e WINDOW_SEGMENTS=15 \
  -e STANDBY_TEXT="Caupolican — Standby" \
  -e CHANNELS="news,sports" \
  -v /tmp/caupolican/out:/out \
  -p 80:9999 \
  ghcr.io/${{ github.repository_owner }}/caupolican:latest
```

## Endpoints

- API: `/api/health`, `/api/channels`, `/api/channels/{id}/set-source`, `/api/channels/{id}/stop`
- HLS: `http://host/hls/<channel>/index.m3u8`
- Private Prometheus scrape: `/metrics`

Channels are created dynamically when setting a source.

## API

- `GET /health` → `{"ok": true, "channels": {"news": {"active": false, "media_seq": 0, "window": 6}}}`
- `GET /channels` → `[{"id": "news", "active": false, "media_seq": 0, "window": 6}]`
- `GET /channels/{id}/status` → `{"id": "news", "active": false, "media_seq": 0, "window": 6}`
- `POST /channels/{id}/set-source` body `{"url": "rtmp://..."}` → Switch to live
- `POST /channels/{id}/stop` → Fallback to standby
- `DELETE /channels/{id}/segments` → Reset window

Auth: If `ROUTER_TOKEN` set, include `Authorization: Bearer <token>` on POST/DELETE.

## Observability

`GET /metrics` exposes Prometheus text format and always requires
`Authorization: Bearer <ROUTER_TOKEN>`. The endpoint fails closed with `503` if
`ROUTER_TOKEN` is not configured and returns `401` for a missing or invalid
credential. Production passes the token from the `ROUTER_TOKEN` GitHub secret;
the Prometheus scraper must use that same credential, and deployment networking
must restrict access to the scraper.

The collector exports:

- service/build identity plus Python, garbage-collection, and process metrics;
- HTTP request count and duration with only method, normalized route template,
  and status class labels;
- FFmpeg and filesystem dependency outcomes;
- current live/standby worker counts and disk-usage ratio;
- bounded worker transition, restart/backoff, stall, periodic cleanup,
  disk-pressure cleanup, and segment cleanup outcomes.

Metrics never label channel IDs, source URLs, filesystem paths, tokens, or raw
errors. Application instrumentation and its private endpoint are maintained in
this repository. Scrape configuration, storage, dashboards, and alerts remain a
deployment dependency of `gaulatti/prometheus` and are intentionally outside
this ticket.

## Production Deploy

Deployment is automatic on push to `main`. The workflow builds the image, pushes to GHCR, and deploys to the on-premises server.

Prerequisites:
- Set GitHub Secrets: `DEPLOYMENT_TOKEN`, `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `ROUTER_TOKEN`
- Environment variables are configured in the GitHub Actions workflow

## Environment Variables

- `CHANNELS`: Comma-separated channel IDs
- `OUT_ROOT`: /out
- `TARGET_DURATION`: 2 (seconds)
- `WINDOW_SEGMENTS`: 6
- `STANDBY_TEXT`: Text for slate
- `STANDBY_IMAGE`: Optional image path
- `ROUTER_TOKEN`: Bearer token for control mutations and the private Prometheus scrape; required for `/metrics`
- `RESTART_MAX`: 6
- `BACKOFF_BASE_MS`: 500
- `BACKOFF_FACTOR`: 2.0
- `BACKOFF_CAP_MS`: 30000
- `COOLOFF_SEC`: 300
- `STALL_FACTOR`: 3

## Troubleshooting

- SELinux: Use `:Z` on volumes
- Health flaps: Check logs, disk space
- ffmpeg crashes: Tune presets, increase mem
- Inodes: Monitor segment cleanup
