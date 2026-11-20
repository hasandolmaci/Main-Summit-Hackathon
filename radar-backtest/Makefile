PY := .venv/bin/python

.PHONY: setup test labels cordis orgs gharchive gate validate snapshots backtest report all

setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test:
	$(PY) -m pytest -q tests

labels:          ## YC directory + HTGF portfolio -> data/labels/companies.csv
	$(PY) -m scripts.collect_labels

cordis:          ## CORDIS bulk CSVs (FP7, H2020, Horizon Europe) -> raw + processed
	$(PY) -m scripts.sources.cordis

orgs:            ## GitHub org sample (pool a) + org lookups for labelled companies
	$(PY) -m scripts.sources.github

gharchive:       ## GH Archive events for pool + positives (ClickHouse mirror by default)
	$(PY) -m scripts.sources.gharchive

gate:            ## covered positives per year + metric coverage, before full collection
	$(PY) -m scripts.gate

validate:
	$(PY) -m scripts.validate_data

snapshots:
	$(PY) -m scripts.build_snapshots

backtest:
	$(PY) -m scripts.backtest

report:
	$(PY) -m scripts.report

all: labels cordis orgs gharchive validate snapshots backtest report
