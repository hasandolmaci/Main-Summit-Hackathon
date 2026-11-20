PY := .venv/bin/python

.PHONY: setup test labels cordis idmap pipeline orgs gharchive gate validate snapshots backtest report all

setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test:
	$(PY) -m pytest -q tests

labels:          ## YC directory + HTGF portfolio -> data/labels/companies.csv
	$(PY) -m scripts.collect_labels

cordis:          ## CORDIS bulk zips (download by hand in a browser: robots.txt) -> processed tables
	$(PY) -m scripts.sources.cordis --skip-download

idmap:           ## GitHub account-id -> creation-date map (recycled-login guard), streamed from GH Archive
	$(PY) -m scripts.sources.gh_id_dates

pipeline:        ## steps 1-4 in order: org search, pool sample, controls, GH Archive features, CORDIS links, gate
	$(PY) -m scripts.pipeline

orgs:            ## (part of pipeline) stem-only org search for labelled companies
	$(PY) -m scripts.run_org_search --labelled-only

gharchive:       ## (part of pipeline) GH Archive features for all matched orgs (ClickHouse mirror, quota-aware)
	$(PY) -m scripts.sources.gh_features

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

all: labels cordis idmap pipeline snapshots backtest report
