# VS Code Server Home Assistant add-on

This add-on packages the Microsoft VS Code Server (via
[`ahmadnassri/vscode-server`](https://github.com/ahmadnassri/docker-vscode-server))
inside the Home Assistant add-on base image so you can use the official VS Code
marketplace, GitHub Copilot, and Copilot Chat from the Home Assistant frontend.

## Ports and ingress

- Internal port: `8000`
- Home Assistant ingress is enabled by default.
- You can optionally expose `8000/tcp` for direct access when running outside of
  ingress.

## Building locally

To build the add-on image manually:

```bash
docker build \
  --build-arg BUILD_FROM=ghcr.io/hassio-addons/debian-base:9.1.0 \
  --build-arg VSCODE_SERVER_TAG=latest \
  -t local/vscode-server-ha \
  vscode
```

Replace `BUILD_FROM` with the Home Assistant base image for your architecture
and, if desired, pin `VSCODE_SERVER_TAG` to a specific VS Code Server release.

## Verifying the Copilot run_in_terminal patch

The add-on patches the bundled VS Code assets on startup to keep Copilot's
`run_in_terminal` tool working in the browser build. To confirm the patch is
present:

Startup patch failures now emit a warning by default; set
`PATCH_RUN_IN_TERMINAL_STRICT=true` to fail the container when the patcher
cannot adjust the bundles.

1. Build the image (as shown above) and start a container shell:

   ```bash
   docker run --rm -it --entrypoint bash local/vscode-server-ha
   ```

2. Run the patcher and verify it exits successfully (idempotent runs should also
   return `0`):

   ```bash
   python3 /usr/local/bin/patch_run_in_terminal.py
   ```

3. Search for the marker (`/* patched: run_in_terminal */`) in the served
   workbench bundle:

   ```bash
   ack "patched: run_in_terminal" \
     "${VSCODE_CLI_DATA_DIR:-/data/vscode/cli-data}" \
     /usr/lib/code /usr/lib/vscode-server /opt/vscode-server /data/vscode/extensions /root/.vscode/cli-data
   ```

You should see at least one match inside a `workbench*.js` file. If not, the
patch failed and the add-on should be considered unhealthy.

## Test plan

1. Build the add-on image and start it inside Home Assistant or via `docker run`.
2. Connect to VS Code in the browser and trigger Copilot's `run_in_terminal`
   twice (for example, by asking Copilot Chat to run a command in the terminal).
3. Confirm no `ENOPRO: No file system provider found for resource` messages
   appear in the add-on logs after either invocation.
4. Verify the `/* patched: run_in_terminal */` marker exists in a
   `workbench*.js` bundle under `${VSCODE_CLI_DATA_DIR:-/data/vscode/cli-data}`
   (or the other default search roots above).
