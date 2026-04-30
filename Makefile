.PHONY: help install setup test eval demo cli clean

help:  ## Show this help.
	@echo "x1025 maritime AI prototype"
	@echo ""
	@echo "Common targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Free-tier path (recommended):"
	@echo "  export LLM_PROVIDER=groq"
	@echo "  export GROQ_API_KEY=<your key from https://console.groq.com>"
	@echo "  make setup demo"

install:  ## Install Python dependencies.
	pip install -r requirements.txt

setup:  ## Build the SQLite mock and ingest the seed ISM corpus.
	python setup_data.py

test:  ## Run the pytest suite (49 tests, no LLM required).
	python -m pytest tests/ -v

eval:  ## Run the full evaluation harness; writes docs/eval_report.md.
	python evaluate.py

eval-fast:  ## Run eval without judge or verifier (components only).
	python evaluate.py --skip-judge --skip-verify

demo:  ## Launch the Gradio demo UI on http://localhost:7860.
	python app.py

cli:  ## Launch the REPL CLI.
	python cli.py

cost:  ## Regenerate the cost model report.
	python cost_model.py

clean:  ## Remove build artifacts and caches.
	rm -rf __pycache__ x1025/__pycache__ tests/__pycache__ .pytest_cache
	rm -rf data/chroma data/x1025.db logs/
