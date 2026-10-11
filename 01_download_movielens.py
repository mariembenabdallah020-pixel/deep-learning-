"""Étape 1 : télécharge MovieLens, calcule le checksum, extrait l'archive.

Idempotent : si l'archive existe déjà, elle n'est pas retéléchargée.
Un manifeste (JSON) trace la source, la date et le SHA-256 pour la traçabilité.
"""
import hashlib
import json
import logging
import zipfile
from datetime import datetime, timezone

import httpx

from config import ML_DIR, ML_URL, ML_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("movielens")


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    archive = ML_DIR / f"{ML_VERSION}.zip"
    if archive.exists():
        log.info("Archive déjà présente, téléchargement ignoré : %s", archive)
    else:
        log.info("Téléchargement %s", ML_URL)
        with httpx.stream("GET", ML_URL, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with open(archive, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)

    manifest = {
        "source": ML_URL,
        "version": ML_VERSION,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "sha256": sha256(archive),
        "size_bytes": archive.stat().st_size,
    }
    (ML_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with zipfile.ZipFile(archive) as z:
        z.extractall(ML_DIR)
    log.info("Extraction terminée. SHA-256 = %s", manifest["sha256"])

    # Vérification minimale des fichiers attendus
    base = ML_DIR / ML_VERSION
    for name in ("ratings.csv", "movies.csv", "links.csv"):
        assert (base / name).exists(), f"Fichier manquant : {name}"


if __name__ == "__main__":
    main()
