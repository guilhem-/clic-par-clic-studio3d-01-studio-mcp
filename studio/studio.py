"""Studio 3D de l'organisation : serveur MCP (HTTP, sans session) et file de rendus Blender.

Open WebUI l'appelle avec un jeton partagé (Authorization: Bearer) et l'adresse de
l'utilisateur (X-Utilisateur-Email, modèle {{USER_EMAIL}}). Chaque demande passe dans une
file ; un Blender neuf (« blender -b ») la rend, avec une durée maximale. Les fichiers
produits sont servis par des liens signés, courts, lisibles depuis le navigateur (CORS).
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

JETON = os.environ["STUDIO_JETON"]
SECRET = os.environ.get("STUDIO_SECRET", JETON).encode()
PUBLIC = os.environ.get("STUDIO_PUBLIC_URL", "http://studio.localhost:8080").rstrip("/")
BLENDER = os.environ.get("BLENDER", "/opt/blender/blender")
RENDU = Path(__file__).with_name("rendu.py")
DOSSIER = Path(os.environ.get("DOSSIER", "/data/rendus"))
DUREE_MAX = int(os.environ.get("DUREE_MAX", "300"))

mcp = FastMCP("studio", host="0.0.0.0", port=8000, stateless_http=True)
demandes: dict[int, dict] = {}
file_attente: asyncio.Queue[int] = asyncio.Queue()
_travailleur: asyncio.Task | None = None


# ---------------------------------------------------------------- Identité et liens

def _utilisateur(ctx: Context) -> str:
    requete = ctx.request_context.request
    entetes = requete.headers if requete is not None else {}
    if entetes.get("authorization", "") != f"Bearer {JETON}":
        raise PermissionError("jeton du studio invalide")
    email = entetes.get("x-utilisateur-email", "").strip().lower()
    if not email:
        raise PermissionError("utilisateur inconnu : en-tête X-Utilisateur-Email absent")
    return email


def _signature(numero: int, extension: str) -> str:
    return hmac.new(SECRET, f"{numero}.{extension}".encode(), hashlib.sha256).hexdigest()[:10]


def _lien(numero: int, extension: str) -> str:
    return f"{PUBLIC}/r/{numero}/{_signature(numero, extension)}.{extension}"


def _artefact(numero: int, titre: str) -> str:
    return (
        '```html\n'
        '<script type="module" src="https://cdn.jsdelivr.net/npm/@google/model-viewer@4.0.0/dist/model-viewer.min.js"></script>\n'
        f'<model-viewer src="{_lien(numero, "glb")}" poster="{_lien(numero, "png")}" alt="{titre}" '
        'camera-controls auto-rotate shadow-intensity="1" '
        'style="width:100%;height:480px;background:#f4f4f5"></model-viewer>\n'
        '```'
    )


# ---------------------------------------------------------------- File et rendu

def _position(numero: int) -> int:
    """Position dans la file : 1 = la prochaine à passer ; le rendu en cours compte."""
    avant = sum(1 for n, d in demandes.items() if n < numero and d["statut"] in ("en_attente", "en_cours"))
    return 1 + avant


async def _travailler() -> None:
    while True:
        numero = await file_attente.get()
        d = demandes[numero]
        d.update(statut="en_cours", debut=time.time())
        dossier = DOSSIER / str(numero)
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "scene.py").write_text(d["code"], encoding="utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                BLENDER, "-b", "--factory-startup", "--python", str(RENDU), "--", str(dossier),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                sortie, _ = await asyncio.wait_for(proc.communicate(), DUREE_MAX)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError(f"rendu interrompu après {DUREE_MAX} s")
            resultat = dossier / "resultat.json"
            if not resultat.exists():
                lignes = sortie.decode(errors="replace").strip().splitlines()
                raise RuntimeError("\n".join(lignes[-12:]) or "Blender n'a rien produit")
            info = json.loads(resultat.read_text(encoding="utf-8"))
            if info.get("erreur"):
                raise RuntimeError(info["erreur"])
            d.update(statut="termine", fin=time.time())
        except Exception as e:  # le message revient au modèle, qui peut corriger et resoumettre
            d.update(statut="erreur", fin=time.time(), erreur=str(e)[-1500:])
        finally:
            file_attente.task_done()


def _demarrer_travailleur() -> None:
    global _travailleur
    if _travailleur is None or _travailleur.done():
        _travailleur = asyncio.get_running_loop().create_task(_travailler())


# ---------------------------------------------------------------- Outils MCP

@mcp.tool()
async def soumettre_rendu(titre: str, code: str, ctx: Context) -> dict:
    """Met en file d'attente le rendu d'une scène Blender et répond immédiatement.

    code : script Python (bpy) qui construit la scène dans une scène vide : objets, matériaux,
    lumière, caméra. Ne pas régler le moteur ni enregistrer : le studio s'en charge
    (Cycles, 1280 x 720, PNG + modèle 3D GLB). Les objets de décor (sol, fond) doivent avoir
    un nom commençant par « Decor » : ils sont rendus mais pas exportés dans le modèle 3D.
    Renvoie le numéro de la demande et sa position dans la file."""
    utilisateur = _utilisateur(ctx)
    numero = max(demandes, default=0) + 1
    demandes[numero] = {"numero": numero, "titre": titre[:80], "code": code, "utilisateur": utilisateur,
                        "statut": "en_attente", "soumis": time.time()}
    _demarrer_travailleur()
    await file_attente.put(numero)
    return {"numero": numero, "titre": titre, "position": _position(numero),
            "message": "Demande enregistrée. Le rendu prend une à deux minutes ; demandez son état avec etat_rendu."}


@mcp.tool()
def etat_rendu(numero: int, ctx: Context) -> dict:
    """Donne l'état d'une demande de l'utilisateur : en_attente (avec sa position), en_cours,
    termine (avec l'image en Markdown et l'artefact 3D à recopier tels quels) ou erreur."""
    utilisateur = _utilisateur(ctx)
    d = demandes.get(numero)
    if d is None or d["utilisateur"] != utilisateur:
        return {"numero": numero, "erreur": "aucune demande de ce numéro pour cet utilisateur"}
    etat = {"numero": numero, "titre": d["titre"], "statut": d["statut"]}
    if d["statut"] == "en_attente":
        etat["position"] = _position(numero)
    elif d["statut"] == "en_cours":
        etat["depuis_secondes"] = round(time.time() - d["debut"])
    elif d["statut"] == "termine":
        etat["duree_secondes"] = round(d["fin"] - d["debut"])
        etat["image_markdown"] = f'![{d["titre"]}]({_lien(numero, "png")})'
        etat["artefact_3d"] = _artefact(numero, d["titre"])
    else:
        etat["erreur"] = d.get("erreur", "")
    return etat


@mcp.tool()
def mes_rendus(ctx: Context) -> list[dict]:
    """Liste les demandes de l'utilisateur connecté, de la plus récente à la plus ancienne."""
    utilisateur = _utilisateur(ctx)
    return [{"numero": d["numero"], "titre": d["titre"], "statut": d["statut"]}
            for d in sorted(demandes.values(), key=lambda d: -d["numero"]) if d["utilisateur"] == utilisateur]


# ---------------------------------------------------------------- Fichiers (liens signés)

@mcp.custom_route("/r/{numero}/{fichier}", methods=["GET"])
async def fichier(request: Request) -> Response:
    try:
        numero = int(request.path_params["numero"])
        signature, extension = request.path_params["fichier"].rsplit(".", 1)
    except ValueError:
        return Response(status_code=404)
    nom = {"png": "rendu.png", "glb": "modele.glb"}.get(extension)
    chemin = DOSSIER / str(numero) / (nom or "")
    if not nom or not hmac.compare_digest(signature, _signature(numero, extension)) or not chemin.is_file():
        return Response(status_code=404)
    return FileResponse(chemin, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "private, max-age=3600"},
                        media_type="image/png" if extension == "png" else "model/gltf-binary")


@mcp.custom_route("/sante", methods=["GET"])
async def sante(request: Request) -> Response:
    return JSONResponse({"ok": True, "demandes": len(demandes), "en_attente": file_attente.qsize(),
                         "blender": Path(BLENDER).exists()})


if __name__ == "__main__":
    DOSSIER.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")
