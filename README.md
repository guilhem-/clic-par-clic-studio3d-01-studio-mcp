# Un studio 3D pour toute l'organisation — serveur MCP et Blender dans Kubernetes

Code de la démonstration du module 1 de la série « Clic par clic » **Créer un service d'IA
compatible agentique : le studio 3D**.

Vidéo : {{video}}

## But

Un service d'intelligence artificielle partagé par toute l'organisation, que les assistants
d'Open WebUI appellent par **MCP** :

- l'utilisateur demande une image en langage naturel ; le modèle écrit le code Python (`bpy`) de
  la scène et appelle l'outil `soumettre_rendu` ;
- le studio range la demande dans une **file** et répond aussitôt par un numéro ;
- un **Blender 5.2 sans interface** calcule l'image (Cycles, 1280 × 720) et le modèle 3D (GLB) ;
- les fichiers reviennent par des **liens signés**, lisibles par un artefact `<model-viewer>` ;
- l'**identité** de l'utilisateur (en-tête `X-Utilisateur-Email`, rempli par Open WebUI) range
  chaque demande à son nom : chacun ne voit que ses rendus ;
- une **règle réseau sans sortie** isole le code écrit par le modèle.

Version volontairement simple : un seul pod, une file en mémoire, un rendu à la fois.

## Contenu

| Fichier | Rôle |
|---|---|
| `studio/studio.py` | Serveur MCP (FastMCP, HTTP sans session) : outils `soumettre_rendu`, `etat_rendu`, `mes_rendus`, file, liens signés `/r/…`, santé `/sante` |
| `studio/rendu.py` | Exécuté par Blender sans interface : construit la scène, ajoute caméra et lumière au besoin, rend l'image, exporte le GLB |
| `studio/Dockerfile` | Image : Python 3.12, Blender 5.2 (miroir officiel, empreinte SHA-256 vérifiée), serveur MCP |
| `k8s.yaml` | Espace de noms `studio3d`, déploiement, service, publication des seuls rendus (Traefik), règle réseau sans sortie |
| `open-webui/entetes.json` | En-tête d'identité à coller dans la connexion MCP d'Open WebUI |
| `open-webui/consignes-modele.txt` | Prompt système du modèle « Studio 3D » |
| `.env.example` | Variable à renseigner (jeton partagé), sans valeur |

## Prérequis

- Docker, `kubectl` et un cluster Kubernetes. La vidéo utilise **k3d** (cluster `tterm`, Traefik
  publié sur le port 8080 de la machine).
- **Open WebUI 0.11** déployé dans le même cluster (il joint le studio par le nom de son service).
- Un modèle de langage capable d'appeler des outils (appel de fonction natif).
- Environ 2 Go de mémoire et 2 processeurs pour le pod du studio.

## Installation pas à pas

1. **Jeton partagé.** Copiez `.env.example` en `.env` et renseignez `STUDIO_JETON` (par exemple
   `openssl rand -hex 24`). Ne le versionnez jamais.

2. **Image.**

   ```bash
   docker build -t studio3d:1 studio
   k3d image import studio3d:1 -c tterm      # k3d ; sinon, poussez l'image dans votre registre
   ```

3. **Secret, puis manifeste.**

   ```bash
   kubectl create namespace studio3d
   kubectl -n studio3d create secret generic studio --from-literal=jeton=$STUDIO_JETON
   kubectl apply -f k8s.yaml
   kubectl -n studio3d rollout status deployment/studio
   ```

   Adaptez dans `k8s.yaml` l'adresse publique des rendus (`STUDIO_PUBLIC_URL`, hôte de
   l'`Ingress`) à votre cluster.

4. **Connexion dans Open WebUI** (compte administrateur) : Réglages d'administration ›
   Intégrations › serveurs d'outils › « + » ; type **MCP** ; URL
   `http://studio.studio3d.svc.cluster.local:8000/mcp` ; clé API : le jeton ; nom « Studio 3D »,
   ID `studio` ; « Avancé » : coller le contenu de `open-webui/entetes.json` ; contrôle d'accès
   « Public » ; « Vérifier la connexion », puis « Enregistrer ».

5. **Modèle « Studio 3D »** (Espace de travail › Modèles › « + ») : modèle de base au choix,
   prompt système `open-webui/consignes-modele.txt`, outil « Studio 3D », appel de fonction
   natif, outils intégrés décochés, accès public.

6. **Essai.** Avec un compte utilisateur : « Crée une tasse rouge posée sur une table en bois,
   avec une lumière chaude. », puis, une vingtaine de secondes plus tard, « Où en est la
   première demande ? ». L'image s'affiche et l'objet 3D s'ouvre en artefact.

Contrôles utiles : `kubectl -n studio3d port-forward svc/studio 8000:8000` puis
`curl http://127.0.0.1:8000/sante`.

## Sécurité

- Le point MCP (`/mcp`) n'est pas publié hors du cluster ; seuls les fichiers rendus (`/r/…`) le
  sont, par des liens signés (HMAC) : un lien modifié renvoie 404.
- Le code écrit par le modèle s'exécute dans un pod sans aucune connexion sortante
  (`NetworkPolicy` `egress: []`, appliquée si votre réseau de cluster gère les règles réseau).
- Exemple pédagogique : avant une mise en production, isolez Blender dans un conteneur distinct,
  stockez les fichiers dans un stockage objet et gérez le jeton dans votre coffre de secrets.

## Suite de la série

- Module suivant : **Passer le studio à l'échelle** (file Redis partagée, plusieurs workers
  Blender, mise à l'échelle automatique par KEDA) — dépôt
  `clic-par-clic-studio3d-02-…` (à venir).

## Licence

MIT, voir [`LICENSE`](LICENSE). Blender est distribué sous licence GPL par la Blender Foundation ;
cette série est indépendante des éditeurs des logiciels présentés.
