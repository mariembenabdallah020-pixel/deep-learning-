# Projet 10 — Système de recommandation multimédia
## Tâche 1 (DataOps) : collecte des données

Sources : **MovieLens** (interactions, `links.csv` -> `tmdbId`) + **API TMDB** (synopsis, affiches, métadonnées).
Kaggle "The Movies Dataset" = plan B optionnel.

### Structure
```
config.py                  chemins, référentiel de genres (19 genres TMDB), mapping MovieLens
01_download_movielens.py   téléchargement + SHA-256 + manifest.json
02_collect_tmdb.py         collecte async (retry, checkpoint, affiches), modes links / cold_start
03_normalize.py            raw -> parquet, genres/langues harmonisés, quality_report.json
dvc.yaml                   versionnage des données et reproductibilité
Makefile                   raccourcis (make all)
.env.example               modèle de configuration (clé TMDB)
```

### Utilisation
```bash
make install
cp .env.example .env        # renseigner TMDB_READ_TOKEN
python 02_collect_tmdb.py --limit 100   # test rapide
make all
```
Sorties : `data/raw/` (bronze), `data/processed/` (movies.parquet, ratings.parquet, quality_report.json).

### Principes DataOps appliqués
- Traçabilité : manifest + checksum, jointure par identifiants (pas de matching flou)
- Idempotence et checkpoint : relance possible sans tout refaire
- Résilience : retry exponentiel (429/5xx), journalisation des 404
- Séparation raw (jamais modifié) / processed
- Cohérence : référentiel unique de genres, langues ISO 639-1, flags `has_poster` / `has_overview`
- Cold start : `--mode cold_start` + colonne `is_cold_start`

Attribution : "This product uses the TMDB API but is not endorsed or certified by TMDB."
