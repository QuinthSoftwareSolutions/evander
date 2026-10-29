ARG RUNNER_IMAGE=evander-runner:latest
FROM ${RUNNER_IMAGE}
USER root
COPY runtime/ /opt/evander/codex-security/
ENV PATH=/opt/evander/codex-security:/home/agent/bin:/usr/local/bin:/usr/bin:/bin
