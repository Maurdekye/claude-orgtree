# Frozen kiosk sandbox. Build from the repository root, never from frozen/:
#   docker build -f frozen/sandbox.Dockerfile ... .
# The multi-architecture index digest makes the base immutable while allowing
# Docker to select the native Linux architecture.
FROM docker.io/library/node:22.23.2-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5

ARG ORGTREE_FROZEN_CONFIG
RUN test -n "$ORGTREE_FROZEN_CONFIG"
LABEL io.orgtree.frozen.config="$ORGTREE_FROZEN_CONFIG" \
      io.orgtree.frozen.component="sandbox" \
      io.orgtree.frozen.platform="linux/amd64" \
      org.opencontainers.image.base.name="docker.io/library/node:22.23.2-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5" \
      org.opencontainers.image.version="2.1.220"

# Exact direct and transitive Debian closure. If an approved artifact leaves
# the configured repository, the build fails instead of selecting a newer
# package under the same Dockerfile/configuration digest.
COPY frozen/sandbox-apt.txt /tmp/sandbox-apt.txt
RUN apt-get update \
    && xargs apt-get install -y --no-install-recommends \
        < /tmp/sandbox-apt.txt \
    && rm -rf /var/lib/apt/lists/*

# npm ci enforces every transitive version and registry integrity recorded in
# this dedicated lock. The standard sandbox still follows sandbox/Dockerfile.
COPY frozen/sandbox-provider/package.json \
     frozen/sandbox-provider/package-lock.json /opt/orgtree-cli/
RUN npm ci --prefix /opt/orgtree-cli --omit=dev --no-audit --no-fund
ENV PATH="/opt/orgtree-cli/node_modules/.bin:${PATH}"

RUN useradd -m -s /bin/bash agent \
    && echo 'agent ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/agent \
    && chmod 0440 /etc/sudoers.d/agent
USER agent
ENV HOME=/home/agent
WORKDIR /home/agent
CMD ["sleep", "infinity"]
