ARG EVANDER_GODOT_BASE_IMAGE=evander-runner-godot:4.7.1
FROM ${EVANDER_GODOT_BASE_IMAGE}

LABEL org.evander.runner.capability="godot-render" \
      org.evander.runner.optional="true" \
      org.evander.runner.auto-deploy="false"

RUN apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
      libegl-mesa0 \
      libgl1-mesa-dri \
      libglx-mesa0 \
      libvulkan1 \
      mesa-utils \
      mesa-vulkan-drivers \
      xauth \
      xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY godot-render-launcher.sh /usr/local/bin/godot-render

RUN chmod 0755 /usr/local/bin/godot-render

CMD ["godot-render", "--version"]
