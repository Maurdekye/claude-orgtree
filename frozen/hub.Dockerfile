# Frozen mail-hub image. Build from the repository root.
FROM docker.io/library/python:3.12.14-slim-trixie@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

ARG ORGTREE_FROZEN_CONFIG
RUN test -n "$ORGTREE_FROZEN_CONFIG"
LABEL io.orgtree.frozen.config="$ORGTREE_FROZEN_CONFIG" \
      io.orgtree.frozen.component="mailhub" \
      io.orgtree.frozen.platform="linux/amd64" \
      org.opencontainers.image.base.name="docker.io/library/python:3.12.14-slim-trixie@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217" \
      org.opencontainers.image.version="3.12.14"

WORKDIR /app
COPY frozen/requirements-hub.txt /tmp/requirements-hub.txt
RUN pip install --no-cache-dir --require-hashes \
        --requirement /tmp/requirements-hub.txt
COPY hub/mailhub/ mailhub/
ENV HUB_DATA=/data
EXPOSE 7370 7371
CMD ["python", "-m", "mailhub.serve"]
