ARG EVANDER_RUNNER_BASE_IMAGE=evander-runner:latest
FROM ${EVANDER_RUNNER_BASE_IMAGE}

ARG GODOT_VERSION=4.7.1
ARG GODOT_TEMPLATE_VERSION=4.7.1.stable

ENV GODOT_VERSION=${GODOT_VERSION}
ENV GODOT_TEMPLATE_VERSION=${GODOT_TEMPLATE_VERSION}
ENV GODOT_REAL_BINARY=/opt/evander/godot/bin/godot
ENV GODOT_EXPORT_TEMPLATES_DIR=/opt/evander/godot/export_templates/${GODOT_TEMPLATE_VERSION}

LABEL org.evander.runner.capability="godot" \
      org.evander.runner.optional="true" \
      org.evander.runner.auto-deploy="false" \
      org.evander.godot.version="${GODOT_VERSION}"

COPY godot /opt/evander/godot/bin/godot
COPY export_templates/ /opt/evander/godot/export_templates/${GODOT_TEMPLATE_VERSION}/
COPY godot-launcher.sh /usr/local/bin/godot
COPY LICENSE.godot.txt /usr/share/doc/evander-runner-godot/LICENSE.godot.txt

RUN chmod 0755 /opt/evander/godot/bin/godot /usr/local/bin/godot \
    && ln -s /usr/local/bin/godot /usr/local/bin/godot4 \
    && test -f "/opt/evander/godot/export_templates/${GODOT_TEMPLATE_VERSION}/version.txt" \
    && /opt/evander/godot/bin/godot --headless --version | grep -F "${GODOT_VERSION}"

CMD ["godot", "--headless", "--version"]
