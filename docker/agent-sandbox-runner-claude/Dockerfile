FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/agent
ENV PATH=/home/agent/bin:/opt/agent-sandbox/toolchains/node-v24.14.0/bin:/usr/local/bin:/usr/bin:/bin

RUN apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
      bash \
      ca-certificates \
      coreutils \
      curl \
      file \
      findutils \
      git \
      gzip \
      jq \
      less \
      openssh-client \
      procps \
      python3 \
      python3-pip \
      python3-venv \
      ripgrep \
      tar \
      unzip \
      xz-utils \
      zip \
      fonts-liberation \
      libasound2t64 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libatspi2.0-0 \
      libcairo2 \
      libcups2 \
      libdbus-1-3 \
      libdrm2 \
      libgbm1 \
      libglib2.0-0 \
      libgtk-3-0 \
      libnspr4 \
      libnss3 \
      libpango-1.0-0 \
      libx11-6 \
      libx11-xcb1 \
      libxcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxext6 \
      libxfixes3 \
      libxrandr2 \
      libxrender1 \
      libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

COPY claude /usr/local/bin/claude

RUN chmod 0755 /usr/local/bin/claude \
    && install -d -m 0775 /home/agent /home/agent/repos /artifacts \
    && install -d -m 0700 /home/agent/.agent-sandbox /home/agent/.claude \
    && install -d -m 0755 /opt/agent-sandbox/toolchains /opt/agent-sandbox/playwright-runtime \
      /opt/agent-sandbox/ms-playwright /opt/agent-sandbox/nuget-cache-ro \
    && cat >/etc/profile.d/agent-sandbox.sh <<'PROFILE'
export HOME="${HOME:-/home/agent}"
export PATH="/opt/agent-sandbox/toolchains/node-v24.14.0/bin:$PATH"
if [ -f /home/agent/.agent-sandbox/session.env ]; then
  set -a
  . /home/agent/.agent-sandbox/session.env
  set +a
fi
export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-relay-placeholder}"
export DISABLE_TELEMETRY="${DISABLE_TELEMETRY:-1}"
PROFILE

WORKDIR /home/agent

CMD ["bash", "-lc", "claude --version"]
