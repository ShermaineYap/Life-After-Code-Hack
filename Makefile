.PHONY: install seeds build test review serve image anomaly clean

install:            ## install Python dependencies
	pip install -r requirements-dev.txt

seeds:              ## regenerate the deterministic demo data
	python scripts/generate_seed_data.py

build:              ## build + test the warehouse, copy it where the Dockerfile expects it
	dbt build --profiles-dir .
	mkdir -p build && cp target/warehouse.duckdb build/warehouse.duckdb

test:               ## unit tests for scripts, dashboard, flows and CI config
	pytest pytests -q

review:             ## the review the Duo agents run (offline: no GitLab state)
	bash scripts/downstream_review.sh

serve: build        ## dashboard on http://localhost:8080
	WAREHOUSE_PATH=build/warehouse.duckdb python -m dashboard.app

image: build        ## the Cloud Run image
	docker build -t downstream-dashboard .

anomaly:            ## build with 90% of orders silently missing, then run the monitor against a healthy baseline
	dbt build --profiles-dir . -q
	python scripts/monitor.py --warehouse target/warehouse.duckdb --write-baseline monitoring/baseline.json \
	  --json reports/monitor.json --markdown reports/monitor.md
	dbt build --profiles-dir . -q --vars '{inject_anomaly: true}'
	-python scripts/monitor.py --warehouse target/warehouse.duckdb --baseline monitoring/baseline.json \
	  --json reports/monitor.json --markdown reports/monitor.md

clean:
	rm -rf target build state reports monitoring logs
