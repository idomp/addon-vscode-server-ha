# VS Code Server Home Assistant add-on

This add-on packages the Microsoft VS Code Server (via
[`ahmadnassri/vscode-server`](https://github.com/ahmadnassri/docker-vscode-server))
inside the Home Assistant add-on base image so you can use the official VS Code
marketplace, GitHub Copilot, and Copilot Chat from the Home Assistant frontend.

## Ports and ingress

- Internal port: `8000`
- Optional SSH port: `2222` (can be remapped in the add-on network UI)
- Home Assistant ingress is enabled by default.
- You can optionally expose `8000/tcp` for direct access when running outside of
  ingress.

## Remote SSH access (for desktop VS Code)

You can connect from a desktop VS Code using the **Remote - SSH** extension.

1. Enable SSH in the add-on configuration by setting `enable_ssh: true`. Add
   one or more SSH public keys to `ssh_authorized_keys` (recommended).
2. Start the add-on and ensure the SSH port (`2222` by default) is exposed under
   **Network**. You may change the host port number if desired.
3. On your desktop, add an entry to `~/.ssh/config`:

   ```sshconfig
   Host ha-vscode-addon
     HostName <HOME_ASSISTANT_HOST_IP>
     Port <EXPOSED_PORT_IN_ADDON_UI>
     User root
   ```

4. In desktop VS Code, run `Remote-SSH: Connect to Host...` and choose
   `ha-vscode-addon`.

Password authentication can be enabled with `ssh_password_auth: true` and
`ssh_password`, but key-based authentication is preferred. Avoid exposing the
port to the internet; keep it limited to your LAN.

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
   ack "patched: run_in_terminal" /usr/lib/code /usr/lib/vscode-server /opt/vscode-server
   ```

You should see at least one match inside a `workbench*.js` file. If not, the
patch failed and the add-on should be considered unhealthy.

## Manual test plan

To validate Copilot's `run_in_terminal` tool end-to-end:

1. Build the image and start the add-on (either under Home Assistant or with
   `docker run`).
2. Open the VS Code UI in your browser and trigger Copilot's `run_in_terminal`
   twice.
3. Check the container logs to confirm no `ENOPRO` errors appear when the tool
   runs.
4. Inspect the served workbench bundle for `/* patched: run_in_terminal */`
   (as shown above) to ensure the patch was applied to the workbench assets.
