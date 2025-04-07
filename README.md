# Huntarr/4tdarr

A Docker container that manages Tdarr nodes based on Plex transcoding activity. This tool helps optimize your GPU usage by dynamically adjusting Tdarr workers or stopping Tdarr completely when Plex needs transcoding resources.

## Configuration Options

| Variable | Description | Example |
|----------|-------------|---------|
| `TDARR_NODE_LOG_PATH` | Path to your Tdarr Node log file | `/tdarr/logs/Tdarr_Node_Log.txt` |
| `TDARR_ALTER_WORKERS` | Whether to adjust workers or kill container | `true` or `false` |
| `TDARR_DEFAULT_LIMIT` | Maximum number of GPU workers | `3` |
| `TAUTULLI_API_KEY` | Your Tautulli API key | `abcdef1234567890` |
| `TAUTULLI_URL` | URL to your Tautulli API | `http://10.0.0.10:8181/api/v2` |
| `TDARR_API_URL` | URL to your Tdarr server | `http://10.0.0.10:8265` |
| `CONTAINER_NAME` | Name of your Tdarr Node container | `tdarr_node` |
| `OFFSET_THRESHOLD` | Transcodes to start reducing GPU workers | `1` |
| `TRANSCODE_THRESHOLD` | Transcodes to kill the Tdarr node | `1` |
| `RESTART_DELAY` | Delay before restarting workers/node | `30` |

## Method 1: Worker Scaling Mode

This mode dynamically adjusts the number of Tdarr GPU workers based on Plex transcoding activity, allowing both Plex and Tdarr to share your GPU resources. When Plex needs more resources for transcoding, Huntarr automatically reduces Tdarr workers to ensure smooth playback. When transcoding stops, Tdarr workers are restored.

**Docker Run:**
```bash
docker run -d \
  --name huntarr-tdarr \
  --restart unless-stopped \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /path/to/tdarr/logs:/tdarr/logs:ro \
  -e TDARR_NODE_LOG_PATH=/tdarr/logs/Tdarr_Node_Log.txt \
  -e TDARR_ALTER_WORKERS=true \
  -e TDARR_DEFAULT_LIMIT=3 \
  -e TAUTULLI_API_KEY=your_api_key \
  -e TAUTULLI_URL=http://10.0.0.10:8181/api/v2 \
  -e TDARR_API_URL=http://10.0.0.10:8265 \
  -e CONTAINER_NAME=tdarr_node \
  -e OFFSET_THRESHOLD=1 \
  huntarr/4tdarr:latest
```

**Docker Compose:**
```yaml
version: '3'

services:
  huntarr-tdarr:
    image: huntarr/4tdarr:latest
    container_name: huntarr-tdarr
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /path/to/tdarr/logs:/tdarr/logs:ro
    environment:
      - TDARR_NODE_LOG_PATH=/tdarr/logs/Tdarr_Node_Log.txt
      - TDARR_ALTER_WORKERS=true
      - TDARR_DEFAULT_LIMIT=3
      - TAUTULLI_API_KEY=your_api_key
      - TAUTULLI_URL=http://10.0.0.10:8181/api/v2
      - TDARR_API_URL=http://10.0.0.10:8265
      - CONTAINER_NAME=tdarr_node
      - OFFSET_THRESHOLD=1
```

With this configuration:
- Maximum of 3 GPU workers when no transcodes are active
- With `OFFSET_THRESHOLD=1`:
  - 0 transcodes → 3 workers
  - 1 transcode → 3 workers
  - 2 transcodes → 2 workers
  - 3 transcodes → 1 worker
  - 4+ transcodes → 0 workers

## Method 2: Node Killer Mode

This mode completely stops the Tdarr container when Plex transcoding is detected. This is ideal for systems with limited GPU resources where Plex and Tdarr cannot effectively run simultaneously. When transcoding is detected, the Tdarr node is immediately stopped, and it will only restart when transcoding activity has ceased for the specified delay period.

**Docker Run:**
```bash
docker run -d \
  --name huntarr-tdarr \
  --restart unless-stopped \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /path/to/tdarr/logs:/tdarr/logs:ro \
  -e TDARR_NODE_LOG_PATH=/tdarr/logs/Tdarr_Node_Log.txt \
  -e TDARR_ALTER_WORKERS=false \
  -e TDARR_DEFAULT_LIMIT=2 \
  -e TAUTULLI_API_KEY=your_api_key \
  -e TAUTULLI_URL=http://10.0.0.10:8181/api/v2 \
  -e TDARR_API_URL=http://10.0.0.10:8265 \
  -e CONTAINER_NAME=tdarr_node \
  -e TRANSCODE_THRESHOLD=1 \
  -e RESTART_DELAY=60 \
  huntarr/4tdarr:latest
```

**Docker Compose:**
```yaml
version: '3'

services:
  huntarr-tdarr:
    image: huntarr/4tdarr:latest
    container_name: huntarr-tdarr
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /path/to/tdarr/logs:/tdarr/logs:ro
    environment:
      - TDARR_NODE_LOG_PATH=/tdarr/logs/Tdarr_Node_Log.txt
      - TDARR_ALTER_WORKERS=false
      - TDARR_DEFAULT_LIMIT=2
      - TAUTULLI_API_KEY=your_api_key
      - TAUTULLI_URL=http://10.0.0.10:8181/api/v2
      - TDARR_API_URL=http://10.0.0.10:8265
      - CONTAINER_NAME=tdarr_node
      - TRANSCODE_THRESHOLD=1
      - RESTART_DELAY=60
```

With this configuration:
- When a single transcode is detected, the entire Tdarr node is stopped immediately
- The node will restart only when all transcodes are finished and after the restart delay
- `RESTART_DELAY=60` means it will wait 60 seconds before restarting the node after transcodes stop

## Logs

View the logs to monitor operations:
```bash
docker logs -f huntarr-tdarr
```

## Troubleshooting

- If the application can't find the node ID, check that the `TDARR_NODE_LOG_PATH` is correct and that the container has access to the log file
- If Docker operations fail, ensure that your container has access to the Docker socket
- Check Tautulli connection by verifying your API key and URL
