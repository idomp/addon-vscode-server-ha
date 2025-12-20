# Mission: Convert `addon-vscode-server-ha` into a VS Code **Server** Home Assistant add-on

You are an AI coding assistant (Codex) working on the repository:

- **Repo URL:** `https://github.com/idomp/addon-vscode-server-ha`
- **Goal:** Turn this into a Home Assistant add-on that runs **Microsoft VS Code Server** (via `ahmadnassri/vscode-server`), so the user can:
  - Use the **official VS Code extension marketplace**.
  - Install and use **GitHub Copilot** and **GitHub Copilot Chat** normally from the UI.
  - Run this add-on **next to** the official Studio Code Server add-on for testing (no port/slug conflicts).

The design is based on:

- Upstream add-on: `hassio-addons/addon-vscode`
- VS Code Server Docker image: `ahmadnassri/vscode-server`

Follow the steps below. **Do not guess paths or env vars if you can inspect them from the upstream repos or images.** Always prefer _reading_ the existing Dockerfiles/scripts over inventing new conventions.

---

## 1. Understand the current structure

1. Clone the repo and open it in VS Code.
2. Identify the main add-on directory (likely `vscode/`), and within it:
   - `Dockerfile`
   - `config.yaml`
   - `rootfs/` (cont-init scripts, `services.d` scripts, etc.)
3. For reference, open the upstream repo `hassio-addons/addon-vscode` and inspect the same files in its `vscode/` folder:
   - Note how the original add-on:
     - Starts `code-server`.
     - Sets up services in `rootfs/etc/services.d`.
     - Uses `cont-init.d` scripts.
     - Configures `ingress`, `ports`, and `map` in `config.yaml`.

**Goal at this step:** You understand exactly where `code-server` is currently wired in and what HA-specific logic must be preserved.

---

## 2. Implement a multi-stage Dockerfile using `ahmadnassri/vscode-server` (Option A)

We want to:

- Use `ahmadnassri/vscode-server` as a **builder stage** (Stage 1).
- Keep the existing **Home Assistant base image** (Stage 2) and all HA add-on plumbing (s6, bashio, logging, etc.).
- Copy VS Code Server binaries/config from Stage 1 into Stage 2.

### 2.1 Add the builder stage

Edit `vscode/Dockerfile`:

1. Keep the existing `ARG BUILD_FROM` and final `FROM ${BUILD_FROM}` stage that the upstream add-on uses.
2. Above the final stage, add:

```dockerfile
# Stage 1: VS Code Server image
ARG VSCODE_SERVER_IMAGE="ahmadnassri/vscode-server"
ARG VSCODE_SERVER_TAG="latest"
FROM ${VSCODE_SERVER_IMAGE}:${VSCODE_SERVER_TAG} AS vscode-server
```

3. In this `vscode-server` stage, **do not** add new logic yet. We will only copy files from it.

### 2.2 Inspect the `ahmadnassri/vscode-server` image

From this repo (or from the upstream `docker-vscode-server` repo):

1. Read its Dockerfile and startup script(s):
   - Identify:
     - The **binary / entrypoint** used to start VS Code Server (e.g. `start-vscode`, a `code` binary, or similar).
     - The **directories** for:
       - Server data
       - User data
       - Extensions
     - Any **environment variables** used (e.g. port, mode, data paths).

2. Note the exact paths (e.g. `/usr/local/bin/start-vscode`, `/root/.vscode/server-data`, etc.). We’ll rely on these instead of guessing.

### 2.3 Final HA base stage

In the final stage:

```dockerfile
ARG BUILD_FROM
FROM ${BUILD_FROM}
```

1. Preserve:
   - All labels (`LABEL` commands).
   - Environment variables and package installs from the original Dockerfile (bashio, s6, etc.).
2. Add any extra packages the user typically needs (if there are already additions in this repo, keep them; otherwise don’t add more than necessary).

### 2.4 Copy VS Code Server into final image

Using the paths discovered in 2.2, copy the necessary VS Code Server files into the final stage. For example (this is pseudocode – **use the real paths you find**):

```dockerfile
# Copy VS Code Server binaries/config from builder stage
COPY --from=vscode-server /usr/local/bin/start-vscode /usr/local/bin/start-vscode
COPY --from=vscode-server /usr/local/bin/code /usr/local/bin/code
COPY --from=vscode-server /root/.vscode /root/.vscode
```

- Ensure any copied scripts are executable (`chmod +x` if needed).
- If the builder image expects certain directories for server/user data and extensions, make sure they exist in the final stage too (`RUN mkdir -p ...`).

### 2.5 Environment variables

If the builder image uses environment variables (e.g. `VSCODE_SERVER_PORT`, `VSCODE_SERVER_MODE`), set them in the final stage:

```dockerfile
ENV VSCODE_SERVER_PORT=8000
# Example only; set other env vars as required by the upstream image.
```

**Important:** Derive these names from the actual upstream Dockerfile/scripts, not from guesswork.

---

## 3. Replace `code-server` startup with VS Code Server

The original add-on starts `code-server` via s6 services in `rootfs/etc/services.d`.

### 3.1 Locate and adapt service definitions

1. In `vscode/rootfs/etc/services.d/`, look for the service responsible for launching `code-server` (usually a directory like `code-server/` with a `run` script).
2. Either:
   - Rename it to `vscode-server/`, or
   - Keep the directory name and just change its internal logic to start VS Code Server.

### 3.2 Implement the `run` script for VS Code Server

Create or update `rootfs/etc/services.d/vscode-server/run` (or the equivalent):

```sh
#!/usr/bin/with-contenv bashio
set -e

bashio::log.info "Starting VS Code Server on port ${VSCODE_SERVER_PORT}"

# Ensure VS Code data directories exist.
# Adjust these paths to match how the builder image expects things.
mkdir -p /data/vscode/server-data /data/vscode/user-data /data/vscode/extensions || true

# Export any environment variables required by the start script
export VSCODE_SERVER_PORT

# If the upstream image uses additional env vars, set them here too,
# based on the documentation / Dockerfile.

# Start VS Code Server using the script/binary from the builder stage.
exec /usr/local/bin/start-vscode
```

**Important:**

- Replace `/usr/local/bin/start-vscode` and data paths with the real paths from the upstream `ahmadnassri/vscode-server` image.
- Preserve all the HA logging and error-handling patterns (use `bashio::log.*` where appropriate).

### 3.3 Remove `code-server`-specific logic

1. If there are `cont-init.d` scripts that configure or patch `code-server`, review them:
   - If they only apply to `code-server`, either remove them or adapt them to VS Code Server.
   - If they do general configuration (e.g. create `/config` workspace, install HA extensions), keep them and just switch the CLI calls to `code` instead of `code-server`.

2. Search the repo for `code-server` references and update them to VS Code Server semantics.

---

## 4. Configure ports & ingress to coexist with the official add-on

We need:

- **Internal VS Code Server port:** `8000`
- Add-on must coexist with official Studio Code Server (which uses `1337` internally).

### 4.1 Update `vscode/config.yaml`

1. Open `vscode/config.yaml`.

2. Keep `ingress: true` so the add-on appears in the HA sidebar.

3. Ensure the `ports` section uses `8000` for the internal VS Code Server port:

```yaml
ports:
  8000/tcp: 8000
ports_description:
  8000/tcp: VS Code Server web UI
```

4. Set `ingress_port` appropriately:

- If the add-on uses **host network** (as the original may do), set `ingress_port: 0` to let Supervisor handle port mapping.
- If it uses a custom network, you can set `ingress_port: 8000` so ingress proxies directly to that port.

Mirror the network mode of the original `addon-vscode`; only change the port values so they don’t collide with `1337`.

5. If there is a `watchdog` field referring to `tcp://[HOST]:1337`, update it to `tcp://[HOST]:8000`.

### 4.2 Slug / repository coexistence

- Leave the `slug` as configured in this repo (e.g. `vscode` or `vscode_server`).
- Because this repository is different from the official `hassio-addons/addon-vscode`, Home Assistant will generate a different **addon ID** (prefix), allowing both to be installed at the same time.

Do _not_ reuse the exact combination of repo + slug used by the official add-on.

---

## 5. Extension behaviour & GitHub Copilot

We do **not** hard-code Copilot in the image. Instead we ensure that:

1. VS Code Server uses the **official VS Code extension marketplace**.
2. The web UI allows the user to:
   - Open the Extensions panel.
   - Search for:
     - `GitHub Copilot`
     - `GitHub Copilot Chat`
   - Install both and sign in to GitHub normally.

To support this:

- Preserve the upstream `ahmadnassri/vscode-server` settings for marketplace usage.
- Ensure any environment or configuration that might redirect to Open VSX is **not** present.

(Optional but recommended) also pre-install commonly used HA-related extensions via `cont-init.d` using the `code` CLI:

```sh
code --install-extension redhat.vscode-yaml || true
code --install-extension esphome.esphome-vscode || true
code --install-extension keesschollaart.vscode-home-assistant || true
```

Use whichever CLI path is correct for the VS Code Server binary inside this image.

---

## 6. Docs & metadata

1. Update or create `vscode/README.md` to explain:
   - This add-on runs **Microsoft VS Code Server**, not `code-server`.
   - It is designed to support:
     - Official VS Code marketplace
     - GitHub Copilot & Copilot Chat
   - It uses internal port `8000` and supports HA ingress and an optional direct port.

2. In `config.yaml`:
   - Update `name` (e.g. `"VS Code Server"`).
   - Update `description` (e.g. `"VS Code Server with Copilot support for Home Assistant"`).
   - Increment `version`.

3. Ensure all required HA add-on fields (arch, startup, boot, map, etc.) are present and consistent with the original `addon-vscode` add-on.

---

## 7. Build & test workflow

### 7.1 Build

1. Add a simple build script or document steps (README):
   - How to build the add-on image locally using `docker build` with:
     - `--build-arg BUILD_FROM=...` matching HA’s base image for the target architecture.
     - Optional `--build-arg VSCODE_SERVER_TAG=...` to pin the VS Code Server image.

2. The user will ultimately rely on **Home Assistant Supervisor** to build the add-on from this repo, but a manual build can help catch obvious errors.

### 7.2 Local Home Assistant testing

Document steps for the user:

1. Copy the `vscode` add-on folder into the Home Assistant `/addons` directory (e.g. via Samba or SSH).
2. In HA:
   - Go to **Settings → Add-ons → Add-on Store → Local add-ons**.
   - Install this new add-on.
   - Start it and check logs for VS Code Server starting on port 8000.
3. Verify:
   - Official Studio Code Server add-on still works (port 1337).
   - New add-on appears in the sidebar.
   - VS Code web UI loads correctly.
   - Extensions panel can install GitHub Copilot and GitHub Copilot Chat.
   - Copilot completions and Chat function in the browser as expected.

---

## 8. Cleanup & commits

1. Ensure all new shell scripts in `rootfs` are executable.
2. Run any existing lint/format tasks configured in the repo (if present).
3. Commit in logical chunks:
   - Dockerfile changes.
   - `rootfs` service changes.
   - `config.yaml` adjustments.
   - Docs/README updates.
4. Push changes to the `addon-vscode-server-ha` repo.

---

## Important rules for you (Codex)

- **Don’t guess**:
  - Whenever you need a path, command, or env var for VS Code Server, first inspect:
    - This repo’s current code.
    - The upstream `hassio-addons/addon-vscode` code.
    - The upstream `ahmadnassri/vscode-server` Dockerfile/scripts.
- Preserve all Home Assistant add-on conventions from the upstream add-on and only change what’s required to:
  - Replace `code-server` with VS Code Server.
  - Move from port `1337` to port `8000`.
- Prefer small, safe changes over large rewrites.
