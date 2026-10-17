# Parle-G Agentic AI Supply Chain Engine

A deterministic, four-agent supply-chain pipeline that prioritizes retail orders, routes them to warehouses, creates stock-transfer requests for shortages, and applies approval guardrails. Gemini is used for explanations and reports, not for fulfillment decisions.

## How the pipeline works

1. **Order Intake** assigns customer priority tiers: Government (1), NGO (2), Loyal (3), Regular (4), and unrecognized types (5). Regular orders are further ranked by low-stock urgency.
2. **Spatial Routing** matches orders to a warehouse serving the exact region, then falls back to a warehouse in the same zone. It fulfills orders from available stock where possible.
3. **Rebalancing** looks for stock at other warehouses to cover shortages and creates transfer requests.
4. **Guardrail** auto-approves transfers below 50,000 units. Transfers of 50,000 units or more require manager approval.

The pipeline sorts orders by priority before allocating stock. It returns each order's final outcome, the evaluated transfers, and aggregate statistics. Core decisions are deterministic and rule-based.

## Dashboards

- `dashboard_ai.py` — AI-enabled dashboard with pipeline validation and execution logs, Gemini audits of orders/transfers/warehouses, an executive report, a QA review, and a historical-incident RAG playbook. Reports can be downloaded as Markdown or PDF.

The AI dashboard tries Vertex AI for generated reports and audits; reporting helpers provide deterministic template fallbacks when Gemini is unavailable. Historical incident search uses Vertex AI embeddings when available and local TF-IDF similarity as a fallback.

## Data and persistence

Sample data is in `data/orders.json` (10 orders), `data/warehouses.json` (4 warehouses), and `data/historical_incidents.json` (10 incidents). `scripts/seed_firestore.py` uploads the sample orders and warehouses to Firestore.

By default, `run_pipeline()` reads orders and warehouses from the Firestore database `velochaindb001`; the JSON files are seed/test data, not an automatic offline fallback. A live pipeline run updates warehouse stock and transfer records in Firestore. **Do not run it against production data unless you intend to change that data.**

The seed script overwrites warehouse documents but appends orders with generated Firestore document IDs. Running it repeatedly will create duplicate order documents. Seed only when appropriate for your database.

## Requirements

- Python 3.10 or newer
- Google Cloud CLI (`gcloud`) for authentication
- A Google Cloud project with the Firestore database `velochaindb001`; the account used for Application Default Credentials must have access
- For Gemini reporting and Vertex AI embeddings: Vertex AI API enabled and access to the configured models

Install the dependencies declared in `requirements.txt`, then install the application dependencies used by the dashboards and AI features:

```powershell
python -m pip install -r requirements.txt
python -m pip install streamlit pandas numpy scikit-learn google-genai
```

`requirements.txt` includes Firestore, PDF export, pytest, and other project dependencies. Streamlit, pandas, NumPy, scikit-learn, and the Google Gen AI SDK are currently needed by the dashboard and/or RAG features but are not listed there.

## Configure Google Cloud

The Firestore agents use the `velochaindb001` database. Reporting and RAG default to project `velochain-509610`, location `us-central1`, and Gemini model `gemini-2.5-flash`. These AI settings can be overridden with `GCP_PROJECT`, `GCP_LOCATION`, and `GEMINI_MODEL`; RAG's embedding model can be changed with `EMBEDDING_MODEL` (default `text-embedding-004`).

Authenticate and select the project used by Google Cloud clients:

```powershell
gcloud auth application-default login
gcloud config set project velochain-509610
```

Ensure the Firestore database and required APIs are enabled and accessible in your project. Gemini and embedding features require Vertex AI access. AI generation uses Vertex AI credentials; it does not require an API key in the source code.

## Run

To seed the Firestore database with the bundled sample records:

```powershell
python scripts/seed_firestore.py
```

Run the pipeline in the terminal:

```powershell
python pipeline.py
```

Start the AI-enabled dashboard:

```powershell
streamlit run dashboard_ai.py
```

Open the URL printed by Streamlit (typically `http://localhost:8501`). The AI dashboard runs the pipeline, validations, AI audits, RAG issue diagnosis, and executive report as part of its **Run pipeline** action. Each run uses the live Firestore data and can change warehouse stock.

## Test guardrail approval locally

Run the guardrail agent's interactive test with a transfer ID and quantity you enter:

```powershell
python agents/guardrail_agent.py
```

The entry point first checks the threshold boundaries automatically: 49,999 units must be auto-approved, while 50,000 and 50,001 units must require manager approval. It then prompts for an optional transfer ID and quantity to exercise the manual approval flow. These checks keep transfer data in memory and do not write to Firestore.

## Tests

Most agent and pipeline tests use in-memory data. To run the offline tests without running the default-data integration test that connects to Firestore:

```powershell
python -m pytest tests/test_order_intake_agent.py tests/test_spatial_routing_agent.py tests/test_rebalancing_agent.py tests/test_guardrail_agent.py tests/test_reporting.py tests/test_rag_engine.py tests/test_pipeline.py -k "not default_data_files" -v
```

The `test_pipeline_with_default_data_files` test in `tests/test_pipeline.py` currently calls `run_pipeline()` without supplying data, so it reads from and writes to the configured Firestore database. Run it only when you intentionally want to exercise the live Firestore integration.

## Project structure

```text
agents/                    The four rule-based supply-chain agents
data/                      Sample orders, warehouses, and historical incidents
scripts/seed_firestore.py  Loads sample orders and warehouses into Firestore
tests/                     Agent, pipeline, reporting, and RAG tests
dashboard_ai.py            Gemini reporting, audits, validation, and RAG dashboard
pipeline.py                End-to-end agent orchestration and summary metrics
reporting.py               Validation, Gemini prompts, and report/audit fallbacks
rag_engine.py              Historical incident retrieval and remediation generation
pdf_export.py              PDF report export
requirements.txt           Declared Python dependencies
```
