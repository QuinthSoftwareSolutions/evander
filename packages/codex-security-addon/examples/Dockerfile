ARG RUNNER_IMAGE=evander-runner:latest
FROM ${RUNNER_IMAGE}
USER root
COPY runtime/ /opt/evander/codex-security/
RUN chmod -R go-w /opt/evander/codex-security \
    && chmod 0755 /opt/evander/codex-security/codex /opt/evander/codex-security/node
ENV PATH=/opt/evander/codex-security:/home/agent/bin:/usr/local/bin:/usr/bin:/bin
