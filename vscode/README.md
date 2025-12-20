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

The build/startup logs now show which bundles were patched and confirm the marker
`/* patched: run_in_terminal */` is present. You can also verify manually:

1. Locate the served `workbench` bundle path:
   ```bash
   python3 /usr/local/bin/patch_run_in_terminal.py --require-patch | grep -i workbench
   ```
2. Confirm the marker exists in the served bundle (replace the path if different):
   ```bash
   grep -n "patched: run_in_terminal" /opt/vscode-server/resources/app/out/vs/workbench/workbench.web.main.js
   ```
3. Hard-refresh the VS Code web UI to drop cached bundles:
   - Open the VS Code tab.
   - Open DevTools (F12), then right-click the reload button and choose **Empty
     Cache and Hard Reload** (or Shift+Reload).
