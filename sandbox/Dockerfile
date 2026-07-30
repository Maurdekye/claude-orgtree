# The kiosk-org sandbox image (user spec): every kiosk org runs its agents'
# turns inside ONE dedicated container built from this image — real terminal
# use with no view of the host machine. Rebuild: docker build -t orgtree-sandbox .
FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 git curl ca-certificates bash procps \
    && rm -rf /var/lib/apt/lists/*

# the Claude Code CLI, current enough that tool hooks fire headless (steering)
RUN npm install -g @anthropic-ai/claude-code

RUN useradd -m -s /bin/bash agent
USER agent
ENV HOME=/home/agent
WORKDIR /home/agent

# the container idles; turns arrive via `docker exec`
CMD ["sleep", "infinity"]
