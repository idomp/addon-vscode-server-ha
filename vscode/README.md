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

Run these commands against a running container (replace `<container>` with the
add-on container name returned by `docker ps`):

1. Locate the active `workbench.web.api.js` bundle path being served and confirm
   the patch summary:

   ```bash
   docker exec -it <container> sh -c "python3 /usr/local/bin/patch_run_in_terminal.py --list-bundles --verify-only"
   ```

2. Assert the marker is present in the served bundle(s):

   ```bash
   docker exec -it <container> sh -c "python3 /usr/local/bin/patch_run_in_terminal.py --list-bundles --verify-only | awk '/^ - \\\// {print $2}' | xargs -r grep -n \"patched: run_in_terminal\""
   ```

3. If you just rebuilt or updated the add-on, hard-reload the VS Code tab to
   bypass cached assets (open the VS Code tab, then use **Ctrl+Shift+R**
   / **Cmd+Shift+R** or open DevTools and choose **Empty Cache and Hard
   Reload**). Reload twice to ensure Copilot’s web worker and bundle updates are
   fully applied.
