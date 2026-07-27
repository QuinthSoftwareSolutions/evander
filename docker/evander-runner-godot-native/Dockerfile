ARG EVANDER_GODOT_BASE_IMAGE=evander-runner-godot:4.7.1
FROM ${EVANDER_GODOT_BASE_IMAGE}

LABEL org.evander.runner.capability="godot-native" \
      org.evander.runner.optional="true" \
      org.evander.runner.auto-deploy="false"

RUN apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
      build-essential \
      clang \
      cmake \
      ninja-build \
      pkg-config \
      scons \
    && rm -rf /var/lib/apt/lists/*

CMD ["godot", "--headless", "--version"]
