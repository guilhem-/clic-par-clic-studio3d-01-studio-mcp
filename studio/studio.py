"""The organization's 3D studio: MCP server (HTTP, stateless) and a queue of Blender renders.

Open WebUI calls it with a shared token (Authorization: Bearer) and the user's address
(X-User-Email, template {{USER_EMAIL}}). Each request goes into a queue; a fresh Blender
("blender -b") renders it, with a maximum duration. The files produced are served through
short signed links, readable from the browser (CORS).
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

TOKEN = os.environ["STUDIO_TOKEN"]
SECRET = os.environ.get("STUDIO_SECRET", TOKEN).encode()
PUBLIC = os.environ.get("STUDIO_PUBLIC_URL", "http://studio.localhost:8080").rstrip("/")
BLENDER = os.environ.get("BLENDER", "/opt/blender/blender")
RENDER = Path(__file__).with_name("render.py")
FOLDER = Path(os.environ.get("RENDERS", "/data/renders"))
MAX_DURATION = int(os.environ.get("MAX_DURATION", "300"))

mcp = FastMCP("studio", host="0.0.0.0", port=8000, stateless_http=True)
requests: dict[int, dict] = {}
queue: asyncio.Queue[int] = asyncio.Queue()
_worker: asyncio.Task | None = None


# ---------------------------------------------------------------- Identity and links

def _user(ctx: Context) -> str:
    request = ctx.request_context.request
    headers = request.headers if request is not None else {}
    if headers.get("authorization", "") != f"Bearer {TOKEN}":
        raise PermissionError("invalid studio token")
    email = headers.get("x-user-email", "").strip().lower()
    if not email:
        raise PermissionError("unknown user: X-User-Email header missing")
    return email


def _signature(number: int, extension: str) -> str:
    return hmac.new(SECRET, f"{number}.{extension}".encode(), hashlib.sha256).hexdigest()[:10]


def _link(number: int, extension: str) -> str:
    return f"{PUBLIC}/r/{number}/{_signature(number, extension)}.{extension}"


def _artifact(number: int, title: str) -> str:
    return (
        '```html\n'
        '<script type="module" src="https://cdn.jsdelivr.net/npm/@google/model-viewer@4.0.0/dist/model-viewer.min.js"></script>\n'
        f'<model-viewer src="{_link(number, "glb")}" poster="{_link(number, "png")}" alt="{title}" '
        'camera-controls auto-rotate shadow-intensity="1" '
        'style="width:100%;height:480px;background:#f4f4f5"></model-viewer>\n'
        '```'
    )


# ---------------------------------------------------------------- Queue and rendering

def _position(number: int) -> int:
    """Position in the queue: 1 = the next one to run; the render in progress counts."""
    before = sum(1 for n, d in requests.items() if n < number and d["status"] in ("queued", "running"))
    return 1 + before


async def _work() -> None:
    while True:
        number = await queue.get()
        d = requests[number]
        d.update(status="running", started=time.time())
        folder = FOLDER / str(number)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "scene.py").write_text(d["code"], encoding="utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                BLENDER, "-b", "--factory-startup", "--python", str(RENDER), "--", str(folder),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(proc.communicate(), MAX_DURATION)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError(f"render stopped after {MAX_DURATION} s")
            result = folder / "result.json"
            if not result.exists():
                lines = output.decode(errors="replace").strip().splitlines()
                raise RuntimeError("\n".join(lines[-12:]) or "Blender produced nothing")
            info = json.loads(result.read_text(encoding="utf-8"))
            if info.get("error"):
                raise RuntimeError(info["error"])
            d.update(status="done", finished=time.time())
        except Exception as e:  # the message goes back to the model, which can fix the code and submit again
            d.update(status="error", finished=time.time(), error=str(e)[-1500:])
        finally:
            queue.task_done()


def _start_worker() -> None:
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.get_running_loop().create_task(_work())


# ---------------------------------------------------------------- MCP tools

@mcp.tool()
async def submit_render(title: str, code: str, ctx: Context) -> dict:
    """Queues the render of a Blender scene and answers immediately.

    code: Python script (bpy) that builds the scene in an empty scene: objects, materials,
    light, camera. Do not set the render engine or save anything: the studio does it
    (Cycles, 1280 x 720, PNG + GLB 3D model). Decor objects (floor, background) must have
    a name starting with "Decor": they are rendered but not exported in the 3D model.
    Returns the request number and its position in the queue."""
    user = _user(ctx)
    number = max(requests, default=0) + 1
    requests[number] = {"number": number, "title": title[:80], "code": code, "user": user,
                        "status": "queued", "submitted": time.time()}
    _start_worker()
    await queue.put(number)
    return {"number": number, "title": title, "position": _position(number),
            "message": "Request recorded. Rendering takes one to two minutes; ask for its status with render_status."}


@mcp.tool()
def render_status(number: int, ctx: Context) -> dict:
    """Gives the status of one of the user's requests: queued (with its position), running,
    done (with the image in Markdown and the 3D artifact to copy as is) or error."""
    user = _user(ctx)
    d = requests.get(number)
    if d is None or d["user"] != user:
        return {"number": number, "error": "no request with this number for this user"}
    status = {"number": number, "title": d["title"], "status": d["status"]}
    if d["status"] == "queued":
        status["position"] = _position(number)
    elif d["status"] == "running":
        status["running_seconds"] = round(time.time() - d["started"])
    elif d["status"] == "done":
        status["duration_seconds"] = round(d["finished"] - d["started"])
        status["image_markdown"] = f'![{d["title"]}]({_link(number, "png")})'
        status["model_3d"] = _artifact(number, d["title"])
    else:
        status["error"] = d.get("error", "")
    return status


@mcp.tool()
def my_renders(ctx: Context) -> list[dict]:
    """Lists the signed-in user's requests, most recent first."""
    user = _user(ctx)
    return [{"number": d["number"], "title": d["title"], "status": d["status"]}
            for d in sorted(requests.values(), key=lambda d: -d["number"]) if d["user"] == user]


# ---------------------------------------------------------------- Files (signed links)

@mcp.custom_route("/r/{number}/{name}", methods=["GET"])
async def serve(request: Request) -> Response:
    try:
        number = int(request.path_params["number"])
        signature, extension = request.path_params["name"].rsplit(".", 1)
    except ValueError:
        return Response(status_code=404)
    name = {"png": "render.png", "glb": "model.glb"}.get(extension)
    path = FOLDER / str(number) / (name or "")
    if not name or not hmac.compare_digest(signature, _signature(number, extension)) or not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "private, max-age=3600"},
                        media_type="image/png" if extension == "png" else "model/gltf-binary")


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return JSONResponse({"ok": True, "requests": len(requests), "queued": queue.qsize(),
                         "blender": Path(BLENDER).exists()})


if __name__ == "__main__":
    FOLDER.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")
