"""HTTP-level tests for the FastAPI app.

These exercise real HTTP, JWT validation, graph and DuckDB. Only model
responses and the external JWKS lookup are replaced for offline runs.
"""
from __future__ import annotations

import io
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from app.agent import graph, nodes, router
from app.config import settings
from app.session import InMemoryBackend, SessionStore
from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import validate_and_convert


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import main

    secret = "offline-test-only-secret"
    monkeypatch.setattr(settings, "app_mode", "cloud")
    monkeypatch.setattr(settings, "supabase_jwt_secret", secret)

    def no_external_jwks():
        raise ValueError("JWKS is external; this fixture uses signed HS256 tokens")

    monkeypatch.setattr("app.auth._get_jwks_client", no_external_jwks)
    store = SessionStore(backend=InMemoryBackend())
    manager = DatasetManager()
    monkeypatch.setattr(main, "get_session_store", lambda: store)
    monkeypatch.setattr(graph, "get_session_store", lambda: store)
    monkeypatch.setattr(main, "get_dataset_manager", lambda: manager)
    monkeypatch.setattr("app.tools.dataset_manager.get_dataset_manager", lambda: manager)
    monkeypatch.setattr(graph, "_graph", None)
    monkeypatch.setattr(router, "_get_router_llm", lambda: SimpleNamespace(
        invoke=lambda messages: router.RouteDecision(route="sql", reason="Count orders", analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]}),
    ))
    monkeypatch.setattr(nodes, "_get_llm", lambda: SimpleNamespace(
        invoke=lambda messages: SimpleNamespace(content="SELECT COUNT(DISTINCT order_id) AS n FROM orders"),
    ))

    session_id = "offline-api"
    store.get_or_create(session_id, user_id="offline-user")
    manager.get_or_create(session_id, tmp_path)
    manager.add_file(session_id, validate_and_convert(
        "orders.csv", io.BytesIO(b"order_id,price\norder1,100\norder2,200\n"), tmp_path,
    ))
    token = jwt.encode(
        {"sub": "offline-user", "aud": "authenticated", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )
    with TestClient(main.app, headers={"Authorization": f"Bearer {token}"}) as test_client:
        yield test_client
    for dataset in manager._sessions.values():
        dataset.close()


def test_health_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_schema_without_persistent_sessions_reports_no_upload(client: TestClient) -> None:
    r = client.get("/schema")
    assert r.status_code == 200
    body = r.json()
    assert "schema" in body
    assert "No data uploaded" in body["schema"]


def test_ask_accepts_json_body_and_returns_full_response(client: TestClient) -> None:
    """Regression: with slowapi's @limiter.limit decorator wrapping /ask,
    FastAPI used to mis-detect the AskRequest model as a query parameter
    and return 422. Body(...) annotation in main.py fixes it. This test
    locks the contract in so the bug can't sneak back."""
    r = client.post("/ask", json={
        "question": "How many orders are there?", "session_id": "offline-api",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Required envelope fields
    for key in ("question", "answer", "route", "row_count"):
        assert key in body
    # Question echoed back
    assert body["question"] == "How many orders are there?"
    # Should resolve to the SQL route for a simple count question
    assert body["route"] == "sql"
    assert body["rows"] == [{"n": 2}]


def test_ask_rejects_missing_question(client: TestClient) -> None:
    r = client.post("/ask", json={})
    assert r.status_code == 422  # Pydantic validation error
    assert "question" in r.text


def test_ask_rejects_overly_long_question(client: TestClient) -> None:
    huge = "x" * 3000
    r = client.post("/ask", json={"question": huge})
    assert r.status_code == 422


def test_schema_still_requires_authentication(client: TestClient) -> None:
    del client.headers["Authorization"]
    assert client.get("/schema").status_code == 401


def test_schema_rejects_expired_token(client: TestClient) -> None:
    token = jwt.encode(
        {"sub": "offline-user", "aud": "authenticated", "exp": 0},
        "offline-test-only-secret",
        algorithm="HS256",
    )
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get("/schema").status_code == 401
