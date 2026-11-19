# herjev log watcher. The portal is a Cloudflare Worker (dashboard/), not part of this image.
# Build:  docker compose build     (or: docker build -f docker/Dockerfile -t herjev .)

FROM python:3.13-slim

# ssh sources shell out to the OpenSSH client.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client \
 && rm -rf /var/lib/apt/lists/*

# Run as your host user so files in the data dir stay yours. ssh needs a
# passwd entry for the uid, hence a real user rather than `user:` in compose.
ARG HERJEV_UID=1000
ARG HERJEV_GID=1000
RUN groupadd -o -g "$HERJEV_GID" herjev && useradd -o -u "$HERJEV_UID" -g herjev -M -d /data herjev

COPY pyproject.toml README.md /opt/herjev/
COPY src /opt/herjev/src
COPY examples /opt/herjev/examples
RUN pip install --no-cache-dir "/opt/herjev[mcp]"

COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/herjev-entrypoint

# Config, .env and state (status, Teams token, ssh keys) live in the /data volume; incidents are in Postgres.
ENV HERJEV_HOME=/data HERJEV_CONFIG=/data/herjev.yaml HOME=/data
WORKDIR /data
USER herjev

# webhook source (HTTP push), syslog source (UDP), MCP endpoint for agents
EXPOSE 8787 5514/udp 8789
# The commit this image was built from (set by the deploy workflow); the watcher reports
# it in its status and the VM's updater reads the label. Last, so it never busts the cache.
ARG HERJEV_VERSION=""
ENV HERJEV_VERSION=$HERJEV_VERSION
LABEL org.opencontainers.image.revision=$HERJEV_VERSION

ENTRYPOINT ["herjev-entrypoint"]
CMD ["herjev", "run"]
