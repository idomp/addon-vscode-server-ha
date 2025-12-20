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

## Verifying the Copilot `run_in_terminal` patch

After rebuilding/updating the add-on:

1. Locate the active workbench bundle path being served:
   ```bash
   docker exec -it <addon_container> sh -c 'find /usr/lib /usr/share/code /usr/lib/vscode-server /opt/vscode-server -type f -name "workbench*.js" | grep "/vs/code/browser/workbench" | head -n 5'
   ```
2. Confirm the patch marker is present in the served workbench bundle (automatically uses the first result from step 1):
   ```bash
   docker exec -it <addon_container> sh -c 'grep -n "patched: run_in_terminal" $(find /usr/lib /usr/share/code /usr/lib/vscode-server /opt/vscode-server -type f -name "workbench*.js" | grep "/vs/code/browser/workbench" | head -n 1)'
   ```
3. Invalidate browser cache for the VS Code UI:
   - Open the VS Code Server page in your browser and open Developer Tools.
   - Check "Disable cache" (or use a hard reload).
   - Perform a hard reload (e.g., <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd> or long-press refresh and choose Hard Reload).
