FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/agent
ENV CODEX_HOME=/home/agent/.config/agent-sandbox/codex
ENV PATH=/home/agent/bin:/usr/local/bin:/usr/bin:/bin

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
      libgssapi-krb5-2 \
      libicu74 \
      libssl3t64 \
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
      zlib1g \
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

COPY codex /usr/local/bin/codex

RUN chmod 0755 /usr/local/bin/codex \
    && install -d -m 0775 /home/agent /home/agent/repos /artifacts \
    && install -d -m 0700 /home/agent/.agent-sandbox /home/agent/.codex \
      /home/agent/.config/agent-sandbox/codex \
    && install -d -m 0755 /opt/agent-sandbox/toolchains /opt/agent-sandbox/playwright-runtime \
      /opt/agent-sandbox/ms-playwright /opt/agent-sandbox/nuget-cache-ro \
    && cat >/etc/profile.d/agent-sandbox.sh <<'PROFILE'
export HOME="${HOME:-/home/agent}"
export CODEX_HOME="${CODEX_HOME:-/home/agent/.config/agent-sandbox/codex}"
if [ -f /home/agent/.agent-sandbox/session.env ]; then
  set -a
  . /home/agent/.agent-sandbox/session.env
  set +a
fi
export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"
PROFILE

WORKDIR /home/agent

CMD ["bash", "-lc", "codex --version"]
