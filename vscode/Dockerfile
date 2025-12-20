ARG BUILD_FROM=ghcr.io/hassio-addons/debian-base:9.1.0

# Stage 1: VS Code Server image
ARG VSCODE_SERVER_IMAGE="ahmadnassri/vscode-server"
ARG VSCODE_SERVER_TAG="latest"
FROM ${VSCODE_SERVER_IMAGE}:${VSCODE_SERVER_TAG} AS vscode-server

ARG BUILD_FROM
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Configure locale
ENV \
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    VSCODE_SERVER_PORT=8000

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Copy Python requirements file
COPY requirements.txt /tmp/requirements.txt

# Copy in extensions list
COPY vscode.extensions /root/vscode.extensions

# Stage VS Code Server assets
COPY --from=vscode-server /usr /opt/vscode-server-root/usr
COPY --from=vscode-server /opt /opt/vscode-server-root/opt

# Setup base system
ARG BUILD_ARCH=amd64
ARG HA_CLI_VERSION="4.42.0"
# hadolint ignore=SC2181, DL3008
RUN \
    apt-get update \
    \
    && apt-get install -y --no-install-recommends \
        ack=3.8.1-1 \
        libarchive-tools=3.7.4-4 \
        build-essential=12.12 \
        colordiff=1.0.21-1 \
        git=1:2.47.3-0+deb13u1 \
        iputils-ping=3:20240905-3 \
        locales=2.41-12 \
        mariadb-client=1:11.8.3-0+deb13u1 \
        mosquitto-clients=2.0.21-1 \
        net-tools=2.10-1.3 \
        nmap=7.95+dfsg-3 \
        openssh-client=1:10.0p1-7 \
        openssl=3.5.4-1~deb13u1 \
        python3-dev=3.13.5-1 \
        python3-venv=3.13.5-1 \
        python3=3.13.5-1 \
        unzip=6.0-29 \
        uuid-runtime=2.41-5 \
        wget=1.25.0-2 \
        zip=3.0-15 \
        zsh=5.9-8+b14 \
        less=668-1 \
    \
    && sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    \
    && curl https://bootstrap.pypa.io/get-pip.py | python3 \
    \
    && if [[ "${BUILD_ARCH}" = "aarch64" ]]; then ARCH="arm64"; fi \
    && if [[ "${BUILD_ARCH}" = "amd64" ]]; then ARCH="amd64"; fi \
    \
    && curl -L -s -o /usr/bin/ha \
        "https://github.com/home-assistant/cli/releases/download/${HA_CLI_VERSION}/ha_${BUILD_ARCH}" \
    && chmod a+x /usr/bin/ha \
    \
    && git clone --branch master --single-branch --depth 1 \
        "https://github.com/robbyrussell/oh-my-zsh.git" ~/.oh-my-zsh \
    \
    && git clone --branch master --single-branch --depth 1 \
        "https://github.com/zsh-users/zsh-autosuggestions" \
        ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions \
    && git clone --branch master --single-branch --depth 1 \
        "https://github.com/zsh-users/zsh-syntax-highlighting.git" \
        ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting \
    \
    && sed -i -e "s#bin/bash#bin/zsh#" /etc/passwd \
    \
    && update-alternatives \
        --install /usr/bin/python python /usr/bin/python3 10 \
    \
    && pip3 install --no-cache-dir -r /tmp/requirements.txt \
    \
    && apt-get purge -y --auto-remove \
        build-essential \
        libarchive-tools \
        python3-dev \
        uuid-runtime \
    \
    && find /usr/local \
        \( -type d -a -name test -o -name tests -o -name '__pycache__' \) \
        -o \( -type f -a -name '*.pyc' -o -name '*.pyo' \) \
        -exec rm -rf '{}' + \
    \
    && rm -fr \
        /root/.cache \
        /tmp/* \
        /var/{cache,log}/* \
        /var/lib/apt/lists/*

# Copy VS Code Server binaries/config from builder stage
RUN set -o errexit -o nounset -o pipefail \
    && if [ -d /opt/vscode-server-root/usr/share/code ]; then \
        cp -a /opt/vscode-server-root/usr/share/code /usr/share/code; \
    fi \
    && if [ -d /opt/vscode-server-root/usr/lib/code ]; then \
        cp -a /opt/vscode-server-root/usr/lib/code /usr/lib/code; \
    fi \
    && if [ -d /opt/vscode-server-root/usr/lib/vscode-server ]; then \
        cp -a /opt/vscode-server-root/usr/lib/vscode-server /usr/lib/vscode-server; \
    fi \
    && if [ -d /opt/vscode-server-root/opt/vscode-server ]; then \
        cp -a /opt/vscode-server-root/opt/vscode-server /opt/vscode-server; \
    fi \
    # Ensure we always end up with /usr/local/bin/code \
    && if [ -x /opt/vscode-server-root/usr/local/bin/code ]; then \
        cp /opt/vscode-server-root/usr/local/bin/code /usr/local/bin/code; \
    elif [ -x /opt/vscode-server-root/usr/bin/code ]; then \
        cp /opt/vscode-server-root/usr/bin/code /usr/local/bin/code; \
    else \
        code_candidate=$(find /opt/vscode-server-root -type f \( -name code -o -name start-vscode \) | head -n 1); \
        if [ -n "${code_candidate}" ]; then \
            cp "${code_candidate}" /usr/local/bin/code; \
        else \
            echo "VS Code binary not found in builder image" >&2; \
            exit 1; \
        fi; \
    fi \
    && chmod +x /usr/local/bin/code \
    && rm -rf /opt/vscode-server-root

# Patch Copilot run_in_terminal to avoid file-scheme provider requirements
COPY scripts/patch_run_in_terminal.py /usr/local/bin/patch_run_in_terminal.py
RUN python3 /usr/local/bin/patch_run_in_terminal.py --require-match=false || true

# Copy root filesystem
COPY rootfs /

# Health check
HEALTHCHECK \
    CMD curl --fail http://127.0.0.1:8000/ || exit 1

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Franck Nijhof <frenck@addons.community>" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Home Assistant Community Add-ons" \
    org.opencontainers.image.authors="Franck Nijhof <frenck@addons.community>" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://addons.community" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
