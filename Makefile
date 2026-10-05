install:
	python -m pip install -r requirements.txt

run:
	python -m app.main

test:
	python -m pytest -q

compile:
	python -m compileall -q app tests

lint:
	ruff check app tests
