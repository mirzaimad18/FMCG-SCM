import json
from pathlib import Path
from google.cloud import firestore

# Step 1: Initialize Firestore client with project ID using Google Application Default Credentials
# By default, this connects to the '(default)' Firestore database without requiring a key file.
PROJECT_ID = "velochain-509610"
db = firestore.Client(database="velochaindb001")

# Locate data directory relative to this script
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Step 2: Read warehouses from JSON file
warehouses_file = DATA_DIR / "warehouses.json"
with open(warehouses_file, "r", encoding="utf-8") as f:
    warehouses_data = json.load(f)

# Step 3: Upload warehouses to the "warehouses" collection
# Each warehouse document uses its 'warehouse_id' as the document ID
for warehouse in warehouses_data:
    warehouse_id = warehouse["warehouse_id"]
    db.collection("warehouses").document(warehouse_id).set(warehouse)

print(f"Uploaded {len(warehouses_data)} documents to 'warehouses' collection.")

# Step 4: Read orders from JSON file
orders_file = DATA_DIR / "orders.json"
with open(orders_file, "r", encoding="utf-8") as f:
    orders_data = json.load(f)

# Step 5: Upload orders to the "orders" collection
# Using .add() automatically generates a unique ID for each order document
for order in orders_data:
    db.collection("orders").add(order)

print(f"Uploaded {len(orders_data)} documents to 'orders' collection.")
