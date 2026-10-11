"""Configuration centrale : chemins, constantes, référentiels (genres, langues)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
ML_DIR = RAW / "movielens"
TMDB_DIR = RAW / "tmdb"
POSTER_DIR = RAW / "posters"
LOG_DIR = ROOT / "logs"

for d in (ML_DIR, TMDB_DIR, POSTER_DIR, PROCESSED, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- MovieLens ---------------------------------------------------------
ML_VERSION = os.getenv("ML_VERSION", "ml-latest-small")  # puis "ml-25m"
ML_URL = f"https://files.grouplens.org/datasets/movielens/{ML_VERSION}.zip"

# --- TMDB --------------------------------------------------------------
TMDB_API = "https://api.themoviedb.org/3"
TMDB_TOKEN = os.getenv("TMDB_READ_TOKEN", "")  # "API Read Access Token" (v4)
POSTER_BASE = "https://image.tmdb.org/t/p/w342"
LANGUAGE = "en-US"
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "20"))

# --- Référentiel unique des genres (19 genres TMDB, films) -------------
CANONICAL_GENRES = [
    "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary",
    "Drama", "Family", "Fantasy", "History", "Horror", "Music", "Mystery",
    "Romance", "Science Fiction", "TV Movie", "Thriller", "War", "Western",
]

# Correspondance MovieLens -> référentiel. None = pas un genre / pas d'équivalent.
ML_TO_CANONICAL = {
    "Action": "Action",
    "Adventure": "Adventure",
    "Animation": "Animation",
    "Children": "Family",
    "Comedy": "Comedy",
    "Crime": "Crime",
    "Documentary": "Documentary",
    "Drama": "Drama",
    "Fantasy": "Fantasy",
    "Film-Noir": None,   # style, pas de genre TMDB équivalent
    "Horror": "Horror",
    "IMAX": None,        # format de projection, pas un genre
    "Musical": "Music",
    "Mystery": "Mystery",
    "Romance": "Romance",
    "Sci-Fi": "Science Fiction",
    "Thriller": "Thriller",
    "War": "War",
    "Western": "Western",
    "(no genres listed)": None,
}
