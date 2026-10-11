# Studio 3D de l'organisation : Blender 5.2 sans interface + serveur MCP « studio ».
#   docker build -t studio3d:1 studio
FROM python:3.12-slim

ARG BLENDER_VERSION=5.2.0
ARG BLENDER_SERIE=5.2
# download.blender.org refuse les téléchargements automatisés (403) : miroir officiel, empreinte vérifiée.
ARG BLENDER_MIROIR=https://ftp.nluug.nl/pub/graphics/blender/release
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils \
      libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 \
      libgl1 libegl1 libglib2.0-0 libgomp1 \
  && cd /tmp \
  && curl -fsSLO "${BLENDER_MIROIR}/Blender${BLENDER_SERIE}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
  && curl -fsSL "${BLENDER_MIROIR}/Blender${BLENDER_SERIE}/blender-${BLENDER_VERSION}.sha256" | grep linux-x64.tar.xz | sha256sum -c - \
  && tar -xJf "blender-${BLENDER_VERSION}-linux-x64.tar.xz" -C /opt \
  && rm "blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
  && mv /opt/blender-${BLENDER_VERSION}-linux-x64 /opt/blender \
  && apt-get purge -y curl xz-utils && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "mcp==1.20.0" "pydantic==2.11.7"

RUN useradd --create-home studio && mkdir -p /data/rendus && chown -R studio /data
WORKDIR /app
COPY studio.py rendu.py ./
USER studio
ENV BLENDER=/opt/blender/blender DOSSIER=/data/rendus PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "studio.py"]
