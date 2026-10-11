"""Étape 2 : collecte TMDB (couche RAW/bronze).

Modes :
  --mode links       : films de MovieLens (via links.csv -> tmdbId)
  --mode cold_start  : nouveautés TMDB (now_playing, upcoming, popular) absentes de MovieLens

Propriétés DataOps :
  - idempotence : un film déjà collecté (fichier JSON présent) est ignoré
  - checkpoint : chaque film est écrit dès qu'il est reçu, relance possible après crash
  - résilience : retry exponentiel sur 429 / 5xx / erreurs réseau (respect de Retry-After)
  - traçabilité : les 404 et échecs sont journalisés, le JSON brut n'est jamais modifié
"""
import argparse
import asyncio
import json
import logging

import httpx
import pandas as pd
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from config import (LOG_DIR, LANGUAGE, MAX_CONCURRENCY, ML_DIR, ML_VERSION,
                    POSTER_BASE, POSTER_DIR, RAW, TMDB_API, TMDB_DIR, TMDB_TOKEN)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_DIR / "collect.log", encoding="utf-8")],
)
log = logging.getLogger("tmdb")
logging.getLogger("httpx").setLevel(logging.WARNING)  # pas une ligne par requête


class Retryable(Exception):
    """Erreur temporaire : on réessaie avec backoff."""


@retry(
    retry=retry_if_exception_type(Retryable),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(6),
    reraise=True,
)
async def request(client: httpx.AsyncClient, url: str, params=None):
    """GET avec retry. Renvoie None sur 404, lève Retryable sur 429/5xx/réseau."""
    try:
        r = await client.get(url, params=params)
    except httpx.TransportError as e:
        raise Retryable(str(e)) from e
    if r.status_code == 404:
        return None
    if r.status_code == 429 or r.status_code >= 500:
        retry_after = r.headers.get("Retry-After")
        if retry_after:
            await asyncio.sleep(float(retry_after))
        raise Retryable(f"HTTP {r.status_code} sur {url}")
    r.raise_for_status()
    return r


async def fetch_movie(api, imgs, sem, tmdb_id: int, stats: dict) -> None:
    async with sem:
        try:
            r = await request(
                api, f"{TMDB_API}/movie/{tmdb_id}",
                params={"append_to_response": "keywords,credits", "language": LANGUAGE},
            )
            if r is None:
                stats["not_found"] += 1
                log.warning("404 tmdb_id=%s", tmdb_id)
                with open(RAW / "tmdb_not_found.txt", "a", encoding="utf-8") as f:
                    f.write(f"{tmdb_id}\n")
                return
            data = r.json()
            # Écriture atomique en UTF-8 : fichier temporaire puis renommage
            # (jamais de fichier vide ou à moitié écrit en cas d'erreur)
            final = TMDB_DIR / f"{tmdb_id}.json"
            tmp = final.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(final)
            stats["ok"] += 1

            poster = data.get("poster_path")
            target = POSTER_DIR / f"{tmdb_id}.jpg"
            if poster and not target.exists():
                img = await request(imgs, f"{POSTER_BASE}{poster}")
                if img is not None:
                    target.write_bytes(img.content)
                    stats["posters"] += 1
        except Exception as e:  # échec définitif après retries : on trace et on continue
            stats["failed"] += 1
            log.error("Échec tmdb_id=%s : %s", tmdb_id, e)


async def discover_cold_start_ids(api, pages: int) -> list[int]:
    """Nouveautés TMDB : films récents qui n'ont (a priori) aucun historique de notes."""
    ids: set[int] = set()
    for endpoint in ("now_playing", "upcoming", "popular"):
        for page in range(1, pages + 1):
            r = await request(api, f"{TMDB_API}/movie/{endpoint}",
                              params={"page": page, "language": LANGUAGE})
            if r is None:
                break
            ids.update(m["id"] for m in r.json().get("results", []))
    return sorted(ids)


def movielens_tmdb_ids() -> list[int]:
    links = pd.read_csv(ML_DIR / ML_VERSION / "links.csv")
    return sorted(links["tmdbId"].dropna().astype(int).unique().tolist())


async def main(mode: str, limit: int | None, pages: int) -> None:
    if not TMDB_TOKEN:
        raise SystemExit("TMDB_READ_TOKEN manquant : renseignez-le dans le fichier .env")

    # Deux types de clés TMDB : Read Access Token (JWT, long) ou API Key v3 (32 caractères)
    token = TMDB_TOKEN.strip().strip('"').strip("'")
    if len(token) > 100:
        headers, default_params = {"Authorization": f"Bearer {token}", "accept": "application/json"}, {}
    else:
        headers, default_params = {"accept": "application/json"}, {"api_key": token}
    log.info("Type de clé détecté : %s (longueur %d)",
             "Read Access Token" if len(token) > 100 else "API Key v3", len(token))
    limits = httpx.Limits(max_connections=MAX_CONCURRENCY)
    async with httpx.AsyncClient(headers=headers, params=default_params, timeout=30, limits=limits) as api, \
               httpx.AsyncClient(timeout=30, limits=limits) as imgs:

        # Fail fast : on teste l'authentification AVANT de lancer des milliers de requêtes
        check = await api.get(f"{TMDB_API}/configuration")
        if check.status_code in (401, 403):
            raise SystemExit(
                f"Authentification refusée par TMDB (HTTP {check.status_code}). "
                "Vérifiez la clé dans .env : utilisez le 'API Read Access Token' (commence par eyJ) "
                "ou l'API Key, sans espaces ni guillemets.")
        check.raise_for_status()
        log.info("Authentification TMDB OK")

        if mode == "links":
            ids = movielens_tmdb_ids()
        else:
            known = set(movielens_tmdb_ids())
            ids = [i for i in await discover_cold_start_ids(api, pages) if i not in known]
            (RAW / "cold_start_ids.json").write_text(json.dumps(ids), encoding="utf-8")
            log.info("%d nouveautés hors MovieLens identifiées", len(ids))

        if limit:
            ids = ids[:limit]

        # Nettoyage : un fichier vide (écriture ratée d'une ancienne exécution) doit être recollecté
        # + fichiers illisibles (vides, JSON invalide, ou ancien encodage cp1252 non UTF-8)
        bad = 0
        for f in TMDB_DIR.glob("*.json"):
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                f.unlink()
                bad += 1
        if bad:
            log.warning("%d fichiers invalides supprimés, ils seront recollectés", bad)
        for f in TMDB_DIR.glob("*.tmp"):
            f.unlink()
        todo = [i for i in ids if not (TMDB_DIR / f"{i}.json").exists()]  # idempotence
        log.info("%d films au total, %d restent à collecter", len(ids), len(todo))

        stats = {"ok": 0, "not_found": 0, "failed": 0, "posters": 0}
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        CHUNK = 500  # progression visible + pas de création massive de coroutines
        for start in range(0, len(todo), CHUNK):
            chunk = todo[start:start + CHUNK]
            await asyncio.gather(*[fetch_movie(api, imgs, sem, i, stats) for i in chunk])
            log.info("Progression %d/%d | %s", min(start + CHUNK, len(todo)), len(todo), stats)

    log.info("Terminé : %s", stats)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["links", "cold_start"], default="links")
    p.add_argument("--limit", type=int, default=None, help="ex. 100 pour un test rapide")
    p.add_argument("--pages", type=int, default=5, help="pages par liste (mode cold_start)")
    a = p.parse_args()
    asyncio.run(main(a.mode, a.limit, a.pages))