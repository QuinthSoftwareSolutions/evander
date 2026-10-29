ARG RUNNER_BASE_IMAGE=evander-runner-base:latest
FROM ${RUNNER_BASE_IMAGE}

ENV HOME=/home/agent
ENV PATH=/home/agent/bin:/opt/evander/toolchains/node-v24.22.1/bin:/usr/local/bin:/usr/bin:/bin

COPY claude /usr/local/bin/claude

RUN chmod 0755 /usr/local/bin/claude \
    && install -d -m 0775 /home/agent /home/agent/repos /artifacts \
    && install -d -m 0700 /home/agent/.evander /home/agent/.claude \
    && install -d -m 0755 /opt/evander/toolchains /opt/evander/playwright-runtime \
      /opt/evander/ms-playwright /opt/evander/nuget-cache-ro \
    && cat >/etc/profile.d/evander.sh <<'PROFILE'
export HOME="${HOME:-/home/agent}"
export PATH="/opt/evander/toolchains/node-v24.22.1/bin:$PATH"
if [ -f /opt/evander/toolchains/mssql-client/env.sh ]; then
  . /opt/evander/toolchains/mssql-client/env.sh
fi
if [ -f /home/agent/.evander/session.env ]; then
  set -a
  . /home/agent/.evander/session.env
  set +a
fi
export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-relay-placeholder}"
export DISABLE_TELEMETRY="${DISABLE_TELEMETRY:-1}"
PROFILE

WORKDIR /home/agent

CMD ["bash", "-lc", "claude --version"]
