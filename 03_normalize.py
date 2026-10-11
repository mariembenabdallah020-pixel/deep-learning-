"""Étape 3 : RAW -> PROCESSED (couche silver).

Produit dans data/processed/ :
  - movies.parquet   : une ligne par film, schéma harmonisé (genres, langues, flags qualité)
  - ratings.parquet  : notations MovieLens avec tmdb_id
  - quality_report.json : contrôles de cohérence entre sources
"""
import json
import logging
import re

import pandas as pd

from config import (CANONICAL_GENRES, ML_DIR, ML_TO_CANONICAL, ML_VERSION,
                    POSTER_DIR, PROCESSED, RAW, TMDB_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("normalize")

YEAR_RE = re.compile(r"\((\d{4})\)\s*$")


# TMDB utilise quelques codes non conformes à l'ISO 639-1 : on les harmonise.
LANG_FIXES = {"cn": "zh",   # cantonais -> chinois (ISO 639-1 n'a pas de code pour le cantonais)
              "xx": None}   # "aucune langue" (films muets, musique...)


def normalize_lang(code):
    code = (code or "").strip().lower()
    return LANG_FIXES.get(code, code) or None


def parse_ml_title(title: str) -> tuple[str, float]:
    """'Toy Story (1995)' -> ('Toy Story', 1995)."""
    m = YEAR_RE.search(title)
    if m:
        return title[:m.start()].strip(), float(m.group(1))
    return title.strip(), float("nan")


def ml_genres_to_canonical(raw: str) -> list[str]:
    out = []
    for g in str(raw).split("|"):
        mapped = ML_TO_CANONICAL.get(g)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def load_tmdb_records() -> pd.DataFrame:
    rows = []
    files = list(TMDB_DIR.glob("*.json"))
    log.info("Lecture de %d fichiers JSON (peut prendre 1 à 3 minutes, patientez)...", len(files))
    for n, f in enumerate(files, 1):
        if n % 2000 == 0:
            log.info("  %d/%d fichiers lus", n, len(files))
        d = json.loads(f.read_text(encoding="utf-8"))
        overview = (d.get("overview") or "").strip()
        poster_file = POSTER_DIR / f"{d['id']}.jpg"
        genres = [g["name"] for g in d.get("genres", [])]
        rows.append({
            "tmdb_id": d["id"],
            "imdb_id": d.get("imdb_id"),
            "title": d.get("title"),
            "release_date": pd.to_datetime(d.get("release_date") or None, errors="coerce"),
            "overview": overview,
            "tagline": (d.get("tagline") or "").strip(),
            "poster_path": d.get("poster_path"),
            "runtime": d.get("runtime") or None,           # 0 -> manquant
            "popularity": d.get("popularity"),
            "vote_average": d.get("vote_average"),
            "vote_count": d.get("vote_count"),
            "original_language_raw": (d.get("original_language") or "").lower() or None,
            "original_language": normalize_lang(d.get("original_language")),  # ISO 639-1
            "genres_tmdb": genres,
            "keywords": [k["name"] for k in d.get("keywords", {}).get("keywords", [])],
            "has_overview": bool(overview),
            "has_poster": poster_file.exists(),
            "has_runtime": bool(d.get("runtime")),
        })
    return pd.DataFrame(rows)


def main() -> None:
    base = ML_DIR / ML_VERSION
    links = pd.read_csv(base / "links.csv")
    ml_movies = pd.read_csv(base / "movies.csv")
    ratings = pd.read_csv(base / "ratings.csv")

    tmdb = load_tmdb_records()
    log.info("%d films TMDB chargés", len(tmdb))

    # --- MovieLens : titre/année + genres harmonisés ---
    parsed = ml_movies["title"].apply(parse_ml_title)
    ml_movies["ml_title"] = parsed.str[0]
    ml_movies["ml_year"] = parsed.str[1]
    ml_movies["genres_ml"] = ml_movies["genres"].apply(ml_genres_to_canonical)
    ml = ml_movies.merge(links, on="movieId", how="left")
    ml = ml.dropna(subset=["tmdbId"])
    ml["tmdbId"] = ml["tmdbId"].astype(int)

    # Plusieurs movieId MovieLens peuvent pointer vers le même tmdbId (liens en double
    # dans links.csv). On les regroupe pour garder UNE ligne par film TMDB.
    n_shared_links = int(ml["tmdbId"].duplicated(keep=False).sum())
    ml_agg = ml.groupby("tmdbId").agg(
        movielens_id=("movieId", "min"),
        movielens_ids=("movieId", list),
        ml_title=("ml_title", "first"),
        ml_year=("ml_year", "first"),
        genres_ml=("genres_ml", lambda s: sorted({g for gs in s for g in gs})),
    ).reset_index()

    # --- Jointure sur l'identifiant (aucun matching flou sur les titres) ---
    movies = tmdb.merge(ml_agg, left_on="tmdb_id", right_on="tmdbId", how="left").drop(columns="tmdbId")

    # --- Ratings : stats pour détecter le cold start ---
    ratings = ratings.merge(links[["movieId", "tmdbId"]], on="movieId", how="left")
    ratings = ratings.rename(columns={"tmdbId": "tmdb_id"}).dropna(subset=["tmdb_id"])
    ratings["tmdb_id"] = ratings["tmdb_id"].astype(int)
    counts = ratings.groupby("tmdb_id").size().rename("n_ratings")
    movies = movies.merge(counts, on="tmdb_id", how="left")
    movies["n_ratings"] = movies["n_ratings"].fillna(0).astype(int)
    movies["is_cold_start"] = movies["n_ratings"] == 0

    # --- Genres : référentiel unique + multi-hot ---
    movies["genres"] = movies["genres_tmdb"].apply(
        lambda gs: [g for g in gs if g in CANONICAL_GENRES])
    for g in CANONICAL_GENRES:
        col = "genre_" + g.lower().replace(" ", "_")
        movies[col] = movies["genres"].apply(lambda gs, g=g: int(g in gs))

    movies["release_year"] = movies["release_date"].dt.year

    # --- Rapport qualité / cohérence inter-sources ---
    both = movies.dropna(subset=["genres_ml"])
    agree = both.apply(
        lambda r: len(set(r["genres"]) & set(r["genres_ml"])) / max(len(set(r["genres"]) | set(r["genres_ml"])), 1),
        axis=1)
    year_gap = (movies["release_year"] - movies["ml_year"]).abs()
    report = {
        "n_movies": int(len(movies)),
        "n_cold_start": int(movies["is_cold_start"].sum()),
        "pct_missing_overview": round(100 * (1 - movies["has_overview"].mean()), 2),
        "pct_missing_poster": round(100 * (1 - movies["has_poster"].mean()), 2),
        "pct_missing_runtime": round(100 * (1 - movies["has_runtime"].mean()), 2),
        "languages_top10": movies["original_language"].value_counts().head(10).to_dict(),
        "genre_jaccard_ml_vs_tmdb_mean": round(float(agree.mean()), 3) if len(agree) else None,
        "year_mismatch_gt1": int((year_gap > 1).sum()),
        "duplicate_tmdb_ids": int(movies["tmdb_id"].duplicated().sum()),
        "movielens_rows_sharing_a_tmdb_id": n_shared_links,
        "unknown_genres_tmdb": sorted({g for gs in movies["genres_tmdb"] for g in gs} - set(CANONICAL_GENRES)),
    }

    # Contrôles bloquants (échec = pipeline en erreur)
    assert report["duplicate_tmdb_ids"] == 0, "Doublons de tmdb_id"
    assert movies["original_language"].dropna().str.len().eq(2).all(), "Langue non ISO 639-1"

    movies.to_parquet(PROCESSED / "movies.parquet", index=False)
    ratings.to_parquet(PROCESSED / "ratings.parquet", index=False)
    (PROCESSED / "quality_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Rapport qualité : %s", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()