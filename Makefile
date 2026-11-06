PYTHON ?= python3
BASE_URL ?= http://localhost:8080
.PHONY: check validate test test-own research
check:
	$(PYTHON) -m compileall -q main.py run.py test.py track2_test.py student-tests tests
	$(PYTHON) -c "import json; from pathlib import Path; [json.loads(p.read_text()) for p in Path('contracts').glob('*.json')]"
validate:
	$(PYTHON) -m openapi_spec_validator contracts/openapi.json
test:
	$(PYTHON) tests/smoke.py --base-url $(BASE_URL)
test-own:
	$(PYTHON) test.py --base-url $(BASE_URL)
	$(PYTHON) -m pytest -q student-tests
research:
	$(PYTHON) track2_test.py
