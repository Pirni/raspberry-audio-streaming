# Raspberry Audio Streaming

Raspberry Audio Streaming is a lightweight set of client and server components to capture audio on a Raspberry Pi (or other Linux device) and stream it to a remote server for playback or further distribution.

## Project Overview

The project includes:
- **Client** (`client/`) — audio capture and streaming application with systemd service examples
- **Server** (`server/`) — nginx-based streaming server with a web UI for playback
- **Configuration** — example configs for network, audio, and systemd services

## ⚠️ Important Security Note

**This software MUST only be used over a trusted VPN tunnel. Do NOT run this on a public or untrusted network.**

## Architecture

### Client Side
- `controller/controller.py` — main client controller
- `stream/stream.py` — audio streaming helper
- `systemd/` — systemd service units for automatic startup
- `user-config/config.json` — runtime configuration. Is configured using the web-UI

### Server Side
- `docker-compose.yaml` — containerized nginx server
- `nginx.conf` — streaming server configuration
- `web/` — simple HLS player web UI

## Prerequisites

### Client (Raspberry Pi or Linux device)
- Linux OS
- Python 3.8 or later
- Audio input device accessible to the user

### Server
- Docker and Docker Compose
- Or a way to run the provided nginx configuration

## Default Ports

| Service | Port | Purpose |
|---------|------|---------|
| **RTMP Ingest** | `8081` | Stream audio input from client to server |
| **HLS Playback** | `8080` | Web UI for listening to the stream |
| **Web Configurator** | `80` | Client-side configuration interface (optional) |

## Setup Guide

### Client Setup (Raspberry Pi)

1. **Install dependencies and systemd services:**
   ```bash
   # Make setup script executable
   chmod +x setup.sh
   
   # Run setup
   sudo ./setup.sh
   ```

2. **Configure the client (optional):**
   - You can use the web-based configurator to set up streaming parameters
   - Access it at `http://<client-ip>:80` (if the web service is running)
   - Or manually edit `client/user-config/config.json` to set:
     - Server endpoint (`stream.url`) — point to your VPN server on port 1935
     - Audio input device
     - Any additional ffmpeg arguments

### Server Setup

1. **Navigate to the server directory:**
   ```bash
   cd server
   ```

2. **Start the server with Docker Compose:**
   ```bash
   docker-compose up -d
   ```

3. **Access the web UI:**
   - Navigate to your VPN server IP on port 8080: `http://<vpn-server-ip>:8080`
   - The RTMP stream will be ingested on port 1935 from the client
   - Verify the stream is being received and test playback in the HLS player

### PiVPN
The easiest way to setup a vpn is using the following tool:
https://www.pivpn.io/

## Contributing

Contributions are welcome! Please feel free to:
- Open an issue for bug reports or feature requests
- Submit a pull request with improvements
- Provide feedback on security or usability

See the `LICENSE` file for license terms.

---

**Last updated:** March 2026
