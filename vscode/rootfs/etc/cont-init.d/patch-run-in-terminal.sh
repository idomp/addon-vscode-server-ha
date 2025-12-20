#!/usr/bin/with-contenv bashio
set -euo pipefail

bashio::log.info "Verifying Copilot run_in_terminal patch in VS Code workbench bundle..."

if python3 /usr/local/bin/patch_run_in_terminal.py --require-patch; then
    bashio::log.info "run_in_terminal patch verified and marker present."
else
    bashio::log.error "run_in_terminal patch verification failed. Check bundle paths above."
    exit 1
fi
