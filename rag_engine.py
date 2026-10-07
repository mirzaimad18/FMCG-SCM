"""
RAG (Retrieval-Augmented Generation) Engine for Parle-G Supply Chain.
=====================================================================
Uses vector similarity search over historical supply chain incidents to
diagnose unfulfilled orders, pipeline bottlenecks, and operational errors,
and generates grounded remediation solutions using Gemini AI on Vertex AI.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("rag_engine")

PROJECT = os.getenv("GCP_PROJECT", "velochain-509610")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-004")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INCIDENTS_FILE = DATA_DIR / "historical_incidents.json"

# In-memory caches for fast vector search
_CACHED_INCIDENTS: Optional[List[Dict[str, Any]]] = None
_CACHED_EMBEDDINGS: Optional[np.ndarray] = None
_EMBEDDING_SOURCE: str = "uninitialized"


def load_historical_incidents() -> List[Dict[str, Any]]:
    """Loads historical incidents database from JSON file."""
    global _CACHED_INCIDENTS
    if _CACHED_INCIDENTS is not None:
        return _CACHED_INCIDENTS

    if not INCIDENTS_FILE.exists():
        logger.warning(f"Incidents file not found at {INCIDENTS_FILE}")
        return []

    with open(INCIDENTS_FILE, "r", encoding="utf-8") as f:
        _CACHED_INCIDENTS = json.load(f)
    return _CACHED_INCIDENTS


def _format_incident_text(inc: Dict[str, Any]) -> str:
    """Combines incident fields into a rich textual representation for embedding."""
    return (
        f"Incident: {inc.get('title', '')}. "
        f"Category: {inc.get('category', '')}. "
        f"Symptoms: {inc.get('symptoms', '')}. "
        f"Root Cause: {inc.get('root_cause', '')}. "
        f"Resolution: {inc.get('resolution', '')} "
        f"Preventative: {inc.get('preventative_actions', '')}"
    )


CACHE_FILE = DATA_DIR / "incident_embeddings.json"


def _get_vertex_embeddings(texts: List[str]) -> Optional[np.ndarray]:
    """Generates dense vector embeddings using Vertex AI text-embedding-004 with file caching."""
    # Check if cached on disk
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if len(data.get("embeddings", [])) == len(texts):
                return np.array(data["embeddings"], dtype=np.float32)
        except Exception as e:
            logger.warning(f"Failed to read disk cache {CACHE_FILE}: {e}")

    try:
        from google import genai
        client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
        # Single batch call for all texts to avoid hitting per-minute rate limits
        resp = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
        )
        if hasattr(resp, "embeddings") and resp.embeddings:
            vectors = [e.values for e in resp.embeddings]
            arr = np.array(vectors, dtype=np.float32)
            # Save to disk cache for fast future reloads
            try:
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"embeddings": arr.tolist()}, f)
            except Exception as e:
                logger.warning(f"Failed to write disk cache {CACHE_FILE}: {e}")
            return arr
    except Exception as e:
        logger.warning(f"Vertex AI embedding unavailable: {e}. Falling back to TF-IDF vectorizer.")
    return None


def _get_tfidf_embeddings(texts: List[str]) -> Tuple[np.ndarray, Any]:
    """Fallback vectorizer using TF-IDF when Vertex AI is offline."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    matrix = vectorizer.fit_transform(texts).toarray()
    return np.array(matrix, dtype=np.float32), vectorizer


_TFIDF_VECTORIZER = None


def init_vector_index(force_refresh: bool = False) -> Tuple[List[Dict[str, Any]], np.ndarray, str]:
    """
    Initializes and caches the vector embeddings for all historical incidents.
    Returns (incidents, embeddings_matrix, source_description).
    """
    global _CACHED_INCIDENTS, _CACHED_EMBEDDINGS, _EMBEDDING_SOURCE, _TFIDF_VECTORIZER
    if not force_refresh and _CACHED_EMBEDDINGS is not None and _CACHED_INCIDENTS is not None:
        return _CACHED_INCIDENTS, _CACHED_EMBEDDINGS, _EMBEDDING_SOURCE

    incidents = load_historical_incidents()
    if not incidents:
        return [], np.empty((0, 0)), "empty"

    texts = [_format_incident_text(inc) for inc in incidents]

    # Try Vertex AI dense embeddings
    embeddings = _get_vertex_embeddings(texts)
    if embeddings is not None:
        _CACHED_EMBEDDINGS = embeddings
        _EMBEDDING_SOURCE = f"Vertex AI ({EMBEDDING_MODEL}, 768-dim)"
    else:
        # Fallback to TF-IDF
        matrix, vectorizer = _get_tfidf_embeddings(texts)
        _CACHED_EMBEDDINGS = matrix
        _TFIDF_VECTORIZER = vectorizer
        _EMBEDDING_SOURCE = "TF-IDF Vector Similarity (Local Fallback)"

    _CACHED_INCIDENTS = incidents
    return _CACHED_INCIDENTS, _CACHED_EMBEDDINGS, _EMBEDDING_SOURCE


def search_historical_incidents(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Performs cosine vector search over historical incidents for a query string.
    Returns a list of dicts with keys: 'incident', 'score', 'embedding_source'.
    """
    incidents, matrix, source = init_vector_index()
    if matrix.size == 0 or not incidents:
        return []

    # Embed query using matching model
    query_vec = None
    if "Vertex AI" in source:
        try:
            from google import genai
            client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
            resp = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=query,
            )
            if hasattr(resp, "embedding") and resp.embedding:
                query_vec = np.array(resp.embedding.values, dtype=np.float32)
            elif hasattr(resp, "embeddings") and resp.embeddings:
                query_vec = np.array(resp.embeddings[0].values, dtype=np.float32)
        except Exception as e:
            logger.warning(f"Query embedding with Vertex AI failed: {e}")

    if query_vec is None:
        # Fallback vectorizer
        global _TFIDF_VECTORIZER
        if _TFIDF_VECTORIZER is None:
            _, _TFIDF_VECTORIZER = _get_tfidf_embeddings([_format_incident_text(inc) for inc in incidents])
        query_vec = _TFIDF_VECTORIZER.transform([query]).toarray()[0].astype(np.float32)
        # If matrix was Vertex AI but query failed, re-index with TFIDF
        if matrix.shape[1] != query_vec.shape[0]:
            matrix, _ = _get_tfidf_embeddings([_format_incident_text(inc) for inc in incidents])

    # Compute Cosine Similarity: (A . B) / (||A|| * ||B||)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return []

    matrix_norms = np.linalg.norm(matrix, axis=1)
    # Avoid divide-by-zero
    matrix_norms[matrix_norms == 0] = 1e-10

    scores = np.dot(matrix, query_vec) / (matrix_norms * query_norm)

    top_indices = np.argsort(scores)[::-1][:top_k]
    results = []
    for idx in top_indices:
        results.append({
            "incident": incidents[idx],
            "score": round(float(scores[idx]), 4),
            "source": source,
        })
    return results


def diagnose_pipeline_issues(orders: List[Dict[str, Any]], transfers: List[Dict[str, Any]], stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Scans the pipeline run for specific bottlenecks, unfulfilled orders, and operational risks.
    Generates tailored search queries for the RAG vector engine.
    """
    issues = []

    # 1. Check unfulfilled orders
    unfulfilled_regions = {}
    unfulfilled_surplus = []
    for o in orders:
        outcome = o.get("final_routing_outcome")
        if outcome == "no warehouse for region":
            reg = o.get("region", "Unknown")
            unfulfilled_regions[reg] = unfulfilled_regions.get(reg, 0) + 1
        elif outcome == "no surplus available":
            unfulfilled_surplus.append(f"Store {o.get('store_id')} ({o.get('sku')}, {o.get('quantity_requested')} units)")

    for reg, count in unfulfilled_regions.items():
        issues.append({
            "type": "unserviced_region",
            "title": f"Unserviced Region: {reg} ({count} order(s) unfulfilled)",
            "query": f"Order rejected because no warehouse serves region {reg}. Territory coverage gap and unserviced store.",
            "context": f"{count} order(s) in region '{reg}' marked 'no warehouse for region'.",
        })

    if unfulfilled_surplus:
        issues.append({
            "type": "no_surplus",
            "title": f"Network-Wide Stock Shortage ({len(unfulfilled_surplus)} order(s))",
            "query": "No surplus stock available in any regional warehouse for rebalancing transfer. Out of stock.",
            "context": f"Orders affected: {', '.join(unfulfilled_surplus)}",
        })

    # 2. Check pending manager approvals (>= 50,000 units)
    pending_transfers = [t for t in transfers if t.get("status") == "pending_manager_approval"]
    if pending_transfers:
        total_pending_units = sum(t.get("quantity", 0) for t in pending_transfers)
        issues.append({
            "type": "pending_manager_approval",
            "title": f"Guardrail Hold: {len(pending_transfers)} transfer(s) pending manager approval",
            "query": f"Inter-warehouse transfer exceeds 50,000 units requiring manager approval. Guardrail bottleneck.",
            "context": f"{total_pending_units:,} total units held across {len(pending_transfers)} transfer request(s).",
        })

    # If no explicit failures found, provide a general health query
    if not issues:
        issues.append({
            "type": "operational_health",
            "title": "Healthy Run: Routine Network Maintenance",
            "query": "General supply chain inventory rebalancing, stock buffer optimization, and cycle count reconciliation.",
            "context": "All orders were fulfilled. Check standard preventative playbooks.",
        })

    return issues


def generate_rag_solution(
    issue_context: str,
    retrieved_incidents: List[Dict[str, Any]],
    pipeline_summary: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    Asks Gemini to synthesize a customized incident remediation plan based on
    the retrieved historical incidents and the current pipeline context.
    Returns (solution_markdown, source_label).
    """
    if not retrieved_incidents:
        return "No relevant historical incidents found to formulate a solution.", "RAG Engine"

    incidents_text = []
    for item in retrieved_incidents:
        inc = item["incident"]
        score = item["score"]
        incidents_text.append(
            f"### Incident {inc.get('incident_id')}: {inc.get('title')} (Relevance Match: {score:.1%})\n"
            f"- **Category**: {inc.get('category')}\n"
            f"- **Historical Root Cause**: {inc.get('root_cause')}\n"
            f"- **Verified Resolution**: {inc.get('resolution')}\n"
            f"- **Preventative Actions**: {inc.get('preventative_actions')}\n"
        )
    evidence_block = "\n".join(incidents_text)

    prompt = (
        "You are the Chief Supply Chain Incident Response Commander for Parle-G.\n"
        "A supply chain issue was detected during the latest fulfillment run. "
        "We searched our knowledge base of historical incidents and retrieved the most relevant verified incidents below.\n\n"
        f"CURRENT ISSUE CONTEXT:\n{issue_context}\n\n"
        f"PIPELINE RUN STATS:\n{json.dumps(pipeline_summary or {}, indent=2)}\n\n"
        f"RETRIEVED HISTORICAL INCIDENTS (VECTOR SEARCH EVIDENCE):\n{evidence_block}\n\n"
        "INSTRUCTIONS:\n"
        "Synthesize a clear, actionable Incident Resolution Plan tailored specifically to the current issue. "
        "Ground your advice strictly in the retrieved historical incident resolutions. Do not invent fictitious precedents.\n"
        "Include the following structured sections:\n"
        "1. 🔍 **Problem Diagnosis & Historical Precedent Match**: Explain which historical incident(s) best match this failure mode and why.\n"
        "2. ⚡ **Immediate Remediation Steps (Next 1-4 Hours)**: Step-by-step actions for dispatch/logistics to resolve the current unfulfilled orders or hold.\n"
        "3. 🛡️ **Medium-Term Safeguards & Policy Adjustments**: Concrete changes (e.g. 3PL partnerships, stock buffer quotas, approval escalation SLA).\n"
        "4. 📋 **Responsible Roles & Escalation Chain**: Specify which teams (Regional Sales, Warehouse Manager, Logistics Fleet) must act.\n\n"
        "Format cleanly in markdown with badges (🟢 / 🟡 / 🔴) and bold key actions."
    )

    try:
        from google import genai
        client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return resp.text, f"Gemini ({GEMINI_MODEL}) + RAG Vector Search ({retrieved_incidents[0]['source']})"
    except Exception as e:
        logger.warning(f"Gemini call failed: {e}. Generating deterministic RAG template solution.")
        # Deterministic fallback solution
        template = [
            "### 🧠 RAG Incident Resolution Plan (Grounded in Historical Precedents)",
            f"**Current Issue**: {issue_context}",
            "",
            "#### 1. 🔍 Top Historical Precedents Identified via Vector Search:",
        ]
        for item in retrieved_incidents:
            inc = item["incident"]
            template.append(
                f"- **{inc.get('incident_id')} - {inc.get('title')}** (Similarity: {item['score']:.1%})\n"
                f"  - *Historical Resolution*: {inc.get('resolution')}\n"
                f"  - *Preventative Fix*: {inc.get('preventative_actions')}"
            )
        template.extend([
            "",
            "#### 2. ⚡ Immediate Recommended Remediation Actions:",
            "- Execute the verified playbook from the highest matching incident above.",
            "- Notify regional supply chain operations leads of affected store IDs.",
            f"*(RAG synthesis generated using {retrieved_incidents[0]['source']}; Gemini offline: {e})*"
        ])
        return "\n".join(template), f"Template + RAG ({retrieved_incidents[0]['source']})"
