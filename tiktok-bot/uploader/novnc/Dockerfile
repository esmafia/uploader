# Virtual browser container for in-browser TikTok login.
#
# Stack: Xvfb :99 + x11vnc + websockify (serving noVNC HTML at :6080) +
# Chromium driven by undetected-chromedriver + a small FastAPI control server
# at :7900 that the api service calls to start/stop the browser.
#
# One container, one display, one process tree — per design-review
# recommendation (splitting noVNC proxy from browser forces tcp X11 sharing
# which is fragile). Single-slot serial login queue keeps it simple.
FROM tiktok-base:latest

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    SCREEN_GEOMETRY=1920x1080x24

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc fluxbox \
        novnc websockify \
        chromium chromium-driver \
        net-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY novnc/control_server.py /app/novnc/control_server.py
COPY novnc/entrypoint.sh /app/novnc/entrypoint.sh
RUN chmod +x /app/novnc/entrypoint.sh

# Symlink noVNC's vnc.html as the index so /novnc/ serves the client directly.
RUN ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

EXPOSE 6080 7900

ENTRYPOINT ["/app/novnc/entrypoint.sh"]
