ARG RUNNER_BASE_IMAGE=evander-runner-base:latest
FROM ${RUNNER_BASE_IMAGE}

ENV HOME=/home/agent
ENV CODEX_HOME=/home/agent/.config/evander/codex
ENV PATH=/home/agent/bin:/usr/local/bin:/usr/bin:/bin

COPY codex /usr/local/bin/codex
COPY codex-code-mode-host /usr/local/bin/codex-code-mode-host

RUN chmod 0755 /usr/local/bin/codex /usr/local/bin/codex-code-mode-host \
    && install -d -m 0775 /home/agent /home/agent/repos /artifacts \
    && install -d -m 0700 /home/agent/.evander /home/agent/.codex \
      /home/agent/.config/evander/codex \
    && install -d -m 0755 /opt/evander/toolchains /opt/evander/playwright-runtime \
      /opt/evander/ms-playwright /opt/evander/nuget-cache-ro \
    && cat >/etc/profile.d/evander.sh <<'PROFILE'
export HOME="${HOME:-/home/agent}"
export CODEX_HOME="${CODEX_HOME:-/home/agent/.config/evander/codex}"
if [ -f /opt/evander/toolchains/mssql-client/env.sh ]; then
  . /opt/evander/toolchains/mssql-client/env.sh
fi
if [ -f /home/agent/.evander/session.env ]; then
  set -a
  . /home/agent/.evander/session.env
  set +a
fi
export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"
PROFILE

WORKDIR /home/agent

CMD ["bash", "-lc", "codex --version"]
