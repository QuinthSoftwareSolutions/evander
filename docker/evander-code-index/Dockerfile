ARG RUNNER_IMAGE=evander-runner:latest
FROM ${RUNNER_IMAGE}

# Everything is staged by the host build script. No network is used in this build.
COPY dotnet /opt/code-index/dotnet
COPY node /opt/code-index/node
COPY dotnet-indexer /opt/code-index/dotnet-indexer
COPY typescript /opt/code-index/typescript
COPY python /opt/code-index/python
COPY manifest.json /opt/code-index/manifest.json
COPY scip-dotnet /usr/local/bin/scip-dotnet
ENV DOTNET_ROOT=/opt/code-index/dotnet
ENV PYTHONPATH=/opt/code-index/python
ENV PATH=/opt/code-index/node/bin:/opt/code-index/typescript/node_modules/.bin:/opt/code-index/dotnet:/usr/local/bin:/usr/bin:/bin
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
RUN chmod 0755 /usr/local/bin/scip-dotnet \
    && python3 -c 'from evander.code_index.runner import main' \
    && scip-dotnet --version && scip-typescript --version
CMD ["python3", "-m", "evander.code_index.runner", "--help"]
