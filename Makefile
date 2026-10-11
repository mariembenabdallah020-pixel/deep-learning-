install:
	pip install -r requirements.txt
download:
	python 01_download_movielens.py
collect:
	python 02_collect_tmdb.py
cold_start:
	python 02_collect_tmdb.py --mode cold_start
normalize:
	python 03_normalize.py
all: download collect cold_start normalize
