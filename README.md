# A 3D studio for the whole organization — MCP server and Blender on Kubernetes

Code for module 1 of the “Click by Click” series **Building an agent-ready AI service: the 3D studio**
(also published in French as « Clic par clic » and in German as « Klick für Klick »).

Video: {{video}}

## Goal

An artificial intelligence service shared by the whole organization, which Open WebUI assistants
call over **MCP**:

- the user asks for an image in plain language; the model writes the Python code (`bpy`) of the
  scene and calls the `submit_render` tool;
- the studio puts the request in a **queue** and answers right away with a number;
- a **headless Blender 5.2** renders the image (Cycles, 1280 × 720) and the 3D model (GLB);
- the files come back through **signed links**, readable by a `<model-viewer>` artifact;
- the user's **identity** (`X-User-Email` header, filled in by Open WebUI) files each request under
  their name: everyone sees only their own renders;
- a **network rule with no egress** isolates the code written by the model.

Deliberately simple version: a single pod, an in-memory queue, one render at a time.

## Contents

| File | Role |
|---|---|
| `studio/studio.py` | MCP server (FastMCP, stateless HTTP): tools `submit_render`, `render_status`, `my_renders`, queue, signed links `/r/…`, health check `/health` |
| `studio/render.py` | Run by headless Blender: builds the scene, adds a camera and a light if needed, renders the image, exports the GLB |
| `studio/Dockerfile` | Image: Python 3.12, Blender 5.2 (official mirror, SHA-256 checksum verified), MCP server |
| `k8s.yaml` | `studio3d` namespace, deployment, service, publishing of the renders only (Traefik), network rule with no egress |
| `open-webui/headers.json` | Identity header to paste into the Open WebUI MCP connection |
| `open-webui/system-prompt.txt` | System prompt of the “Studio 3D” model |
| `.env.example` | Variable to fill in (shared token), without any value |

## Prerequisites

- Docker, `kubectl` and a Kubernetes cluster. The video uses **k3d** (cluster `tterm`, Traefik
  published on port 8080 of the machine).
- **Open WebUI 0.11** deployed in the same cluster (it reaches the studio by its service name).
- A language model able to call tools (native function calling).
- About 2 GB of memory and 2 CPUs for the studio pod.

## Step-by-step installation

1. **Shared token.** Copy `.env.example` to `.env` and fill in `STUDIO_TOKEN` (for example
   `openssl rand -hex 24`). Never commit it.

2. **Image.**

   ```bash
   docker build -t studio3d:1 studio
   k3d image import studio3d:1 -c tterm      # k3d; otherwise, push the image to your registry
   ```

3. **Secret, then manifest.**

   ```bash
   set -a; . ./.env; set +a
   kubectl create namespace studio3d
   kubectl -n studio3d create secret generic studio --from-literal=token=$STUDIO_TOKEN
   kubectl apply -f k8s.yaml
   kubectl -n studio3d rollout status deployment/studio
   ```

   In `k8s.yaml`, adapt the public address of the renders (`STUDIO_PUBLIC_URL`, `Ingress` host)
   to your cluster.

4. **Connection in Open WebUI** (administrator account): Admin Panel › Settings › Integrations ›
   tool servers › “+”; type **MCP**; URL `http://studio.studio3d.svc.cluster.local:8000/mcp`;
   API key: the token; name “Studio 3D”, ID `studio`; “Advanced”: paste the contents of
   `open-webui/headers.json`; access control “Public”; “Verify Connection”, then “Save”.

5. **“Studio 3D” model** (Workspace › Models › “+”): any base model, system prompt
   `open-webui/system-prompt.txt`, tool “Studio 3D”, native function calling, builtin tools
   unchecked, public access.

6. **Try it.** With a user account: “Create a red mug on a wooden table, with warm lighting.”,
   then, some twenty seconds later, “How is the first request going?”. The image appears and the
   3D object opens as an artifact.

Useful checks: `kubectl -n studio3d port-forward svc/studio 8000:8000`, then
`curl http://127.0.0.1:8000/health`.

## Security

- The MCP endpoint (`/mcp`) is not published outside the cluster; only the rendered files (`/r/…`)
  are, through signed links (HMAC): a tampered link returns 404.
- The code written by the model runs in a pod without any outbound connection
  (`NetworkPolicy` `egress: []`, enforced if your cluster network supports network policies).
- Teaching example: before going to production, isolate Blender in a separate container, keep the
  files in an object storage and manage the token in your secrets vault.

## Next in the series

- Next module: **Scaling up the studio** (shared Redis queue, several Blender workers, automatic
  scaling with KEDA) — repository
  [`clic-par-clic-studio3d-02-echelle`](https://github.com/guilhem-/clic-par-clic-studio3d-02-echelle).

## License

MIT, see [`LICENSE`](LICENSE). Blender is distributed under the GPL license by the Blender
Foundation; this series is independent of the publishers of the software shown.
