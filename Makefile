.PHONY: install demo serve test eval

install:
	pip install -r requirements-dev.txt

demo:
	python -m exops.cli --n 25

serve:
	uvicorn exops.api.app:app --reload --port 8000

test:
	pytest -q

eval:
	python -m exops.eval.harness --n 300 --seed 11 --out reports
