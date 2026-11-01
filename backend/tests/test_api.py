"""Integration tests for FastAPI endpoints."""
import os
import pytest

# Use in-memory SQLite for tests
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from httpx import AsyncClient, ASGITransport
from main import app
import database


@pytest.fixture(autouse=True)
async def setup_db():
    await database.init_db()
    yield


async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_config_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["crawler"] == "Crawl4AI local Python API"
    assert "crawl4ai_configured" in data
    assert "default_max_pages" in data


async def test_create_task_returns_201():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/tasks", json={
            "objective": "Find the best gaming laptops under 80000",
            "max_pages": 5,
            "crawl_depth": 1,
        })
    assert resp.status_code == 201
    data = resp.json()
    assert "task_id" in data
    assert data["status"] in ("pending", "planning", "running")


async def test_create_task_too_short_objective():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/tasks", json={
            "objective": "hi",
            "max_pages": 5,
        })
    assert resp.status_code == 422


async def test_get_task_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/tasks/nonexistent-id")
    assert resp.status_code == 404


async def test_list_tasks_returns_list():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert "total" in data
    assert isinstance(data["tasks"], list)


async def test_create_and_retrieve_task():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/api/tasks", json={
            "objective": "Research electric vehicles in India 2024",
            "max_pages": 5,
            "crawl_depth": 1,
        })
        assert create_resp.status_code == 201
        task_id = create_resp.json()["task_id"]

        get_resp = await client.get(f"/api/tasks/{task_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["task_id"] == task_id


async def test_progress_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/api/tasks", json={
            "objective": "Find best Python programming books for students",
            "max_pages": 3,
        })
        task_id = create_resp.json()["task_id"]

        progress_resp = await client.get(f"/api/tasks/{task_id}/progress")
        assert progress_resp.status_code == 200
        data = progress_resp.json()
        assert "progress_percent" in data
        assert "pages_crawled" in data


async def test_report_endpoint_before_completion():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/api/tasks", json={
            "objective": "Compare the top 5 cloud providers for startups",
            "max_pages": 3,
        })
        task_id = create_resp.json()["task_id"]

        report_resp = await client.get(f"/api/tasks/{task_id}/report")
        assert report_resp.status_code == 200
        data = report_resp.json()
        assert "task_id" in data
        assert "ranked_results" in data
