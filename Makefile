install:
	python -m pip install -r requirements.txt

lint:
	ruff check app tests

test:
	pytest -q

check:
	python -m compileall -q app tests
	ruff check app tests
	pytest -q
