# FMCG Supply Chain Management System

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
