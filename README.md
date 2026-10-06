# Parle-G Agentic AI Supply Chain Engine

## Project Overview
An agentic AI system that automates order prioritization and warehouse routing for Parle-G's retail orders. The engine takes incoming retail demand, ranks orders by customer priority, routes each order to the right warehouse, rebalances stock between warehouses when one runs short, and asks for manager approval on very large transfers.

## The 4 Agents
1. **Order Intake Agent** - Gives each order a priority tier by customer type (Government > NGO > Loyal > Regular) and sorts the orders. Regular orders are ranked by how low their current stock is.
2. **Spatial Routing Agent** - Matches each order to a warehouse by region. It first looks for an exact region match (`regions_served`), then falls back to a warehouse in the same zone (for example, "Kanpur Central" goes to a Kanpur warehouse). It then checks stock, deducts it, and counts the warehouse's incoming orders.
3. **Rebalancing Agent** - If a warehouse has too little stock, it finds the other warehouse with the most stock of that SKU and creates a transfer. It also counts each warehouse's outgoing orders.
4. **Guardrail Agent** - Checks every transfer. Transfers under 50,000 units are auto-approved. Transfers of 50,000 units or more wait for manager approval.

All four agents are connected in `pipeline.py`.

## Tech Stack
- Python
- Firestore (database `velochaindb001`)
- Streamlit (dashboard)
- pytest (testing)
- Google Antigravity (development environment)

## Project Structure
```
agents/      the 4 agents
data/        sample orders.json and warehouses.json
scripts/     seed_firestore.py (loads sample data into Firestore)
tests/       unit tests for every agent and the pipeline
pipeline.py  runs all 4 agents end to end
app.py       Streamlit dashboard
```

## How to Run

### 1. Install the requirements
```
pip install -r requirements.txt
```

### 2. Log in to Google Cloud
```
gcloud auth application-default login
```

### 3. Load the sample data into Firestore
```
python scripts/seed_firestore.py
```
This loads 4 warehouses and 10 orders.

### 4. Run the pipeline in the terminal
```
python pipeline.py
```

### 5. Run the dashboard
```
streamlit run app.py
```
Open http://localhost:8501 and click **Run pipeline** in the sidebar. The page shows summary numbers, the prioritized orders, the transfers with their Guardrail status, and live warehouse stock with incoming and outgoing order counts.

> Note: each run moves real stock in Firestore, so results can change between runs. To reset, delete the `orders`, `warehouses` and `transfers` collections in the Firestore console and run the seed script again.

## How to Run Tests
Agent tests (safe, they do not touch Firestore):
```
pytest tests/test_order_intake_agent.py tests/test_spatial_routing_agent.py tests/test_rebalancing_agent.py tests/test_guardrail_agent.py -v
```

Full pipeline tests (these use the real Firestore database and change stock):
```
pytest tests/test_pipeline.py -v
```