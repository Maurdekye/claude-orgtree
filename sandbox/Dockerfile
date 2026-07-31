# The kiosk-org sandbox image (user spec): every kiosk org runs its agents'
# turns inside ONE dedicated container built from this image — real terminal
# use with no view of the host machine. The backend builds it on demand,
# tagged with the HOST CLI's version (№44): when the host CLI updates, the
# next sandbox turn rebuilds the image automatically instead of running a
# CLI frozen at first-build forever.
FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 git curl ca-certificates bash procps \
    && rm -rf /var/lib/apt/lists/*

# the Claude Code CLI, pinned to the HOST's version so host and sandbox
# agents run the same binary (build-arg supplied by sandbox.ensure_image;
# "latest" only for manual builds)
ARG CLAUDE_VERSION=latest
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_VERSION}

RUN useradd -m -s /bin/bash agent
USER agent
ENV HOME=/home/agent
WORKDIR /home/agent

# the container idles; turns arrive via `docker exec`
CMD ["sleep", "infinity"]
