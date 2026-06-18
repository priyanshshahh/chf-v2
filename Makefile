# CHF — Crypto Hedge Fund Portfolio System
# Makefile for local development and execution

PYTHON := python3
VENV   := .venv
PIP    := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: help setup bootstrap demo full universe market onchain features labels \
        models portfolio backtest ablation serve dashboard mlflow smoke test clean

help:
	@echo ""
	@echo "CHF — Crypto Hedge Fund Portfolio System"
	@echo "========================================="
	@echo ""
	@echo "Setup:"
	@echo "  make setup       Create venv and install all dependencies"
	@echo "  make bootstrap   Run bootstrap script (create dirs, copy .env)"
	@echo ""
	@echo "Pipeline stages (run in order):"
	@echo "  make universe    Fetch universe from CoinGecko"
	@echo "  make market      Fetch OHLCV from Binance"
	@echo "  make onchain     Fetch on-chain data"
	@echo "  make features    Build feature store"
	@echo "  make labels      Generate forward-return labels"
	@echo "  make models      Train RF + LightGBM models"
	@echo "  make portfolio   Build portfolio allocations"
	@echo "  make backtest    Run vectorbt backtest"
	@echo "  make ablation    Run ablation study"
	@echo "  make full        Run entire pipeline end-to-end"
	@echo ""
	@echo "Demo & Serving:"
	@echo "  make demo        Generate synthetic demo data"
	@echo "  make dashboard   Launch Streamlit dashboard (:8501)"
	@echo "  make serve       Launch FastAPI server (:8000)"
	@echo "  make mlflow      Launch MLflow UI (:5000)"
	@echo ""
	@echo "Testing:"
	@echo "  make smoke       Run smoke test"
	@echo "  make test        Run pytest suite"
	@echo "  make clean       Remove generated data and artifacts"
	@echo ""

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "Setup complete. Activate with: source $(VENV)/bin/activate"

bootstrap:
	$(PYTHON) scripts/bootstrap.py

demo:
	$(PYTHON) main.py demo

universe:
	$(PYTHON) main.py universe

market:
	$(PYTHON) main.py market

onchain:
	$(PYTHON) main.py onchain

features:
	$(PYTHON) main.py features

labels:
	$(PYTHON) main.py labels

models:
	$(PYTHON) main.py models

portfolio:
	$(PYTHON) main.py portfolio

backtest:
	$(PYTHON) main.py backtest

ablation:
	$(PYTHON) main.py ablation

full:
	$(PYTHON) main.py full

serve:
	$(PYTHON) main.py serve

dashboard:
	streamlit run app/dashboard.py --server.port 8501

mlflow:
	mlflow ui --backend-store-uri mlruns --port 5000

smoke:
	$(PYTHON) scripts/smoke_test.py

test:
	$(PYTHON) -m pytest tests/ -v --tb=short

clean:
	rm -rf data/raw data/staged data/cleaned data/features data/labels \
	       data/predictions data/allocations data/backtests data/reports \
	       artifacts mlruns metadata __pycache__ .pytest_cache
	@echo "Cleaned generated data."
