ARG RUNNER_OS_BASE_IMAGE=ubuntu:24.04
FROM ${RUNNER_OS_BASE_IMAGE}

ARG RUNNER_BASE_INPUT_SHA256=unknown
LABEL io.evander.runner.base-input-sha256="${RUNNER_BASE_INPUT_SHA256}"

ENV DEBIAN_FRONTEND=noninteractive

RUN sed -Ei \
      -e 's|https?://archive\.ubuntu\.com/ubuntu/?|http://azure.archive.ubuntu.com/ubuntu/|g' \
      -e 's|https?://security\.ubuntu\.com/ubuntu/?|http://azure.archive.ubuntu.com/ubuntu/|g' \
      /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
      bash \
      bubblewrap \
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
      libunwind8 \
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
