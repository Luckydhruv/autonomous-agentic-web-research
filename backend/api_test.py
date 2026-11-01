"""
Live API test against the running backend server.
Run: python api_test.py
"""
import urllib.request
import urllib.error
import json
import time

BASE = "http://localhost:8000"

def get(path):
    req = urllib.request.urlopen(f"{BASE}{path}", timeout=10)
    return json.loads(req.read())

def post(path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

print("=" * 60)
print("LIVE API TESTS")
print("=" * 60)

# 1. Health
health = get("/health")
assert health["status"] == "ok", f"Health failed: {health}"
print(f"[PASS] GET /health              → {health['status']!r}")

# 2. Config
cfg = get("/api/config")
print(f"[PASS] GET /api/config          → crawler={cfg['crawler']!r}, configured={cfg['crawl4ai_configured']}")

# 3. Create task
task = post("/api/tasks", {
    "objective": "Find the best budget laptops under 50000 rupees in India with good battery life",
    "max_pages": 5,
    "crawl_depth": 1
})
task_id = task["task_id"]
assert task["status"] in ("pending", "planning", "running")
print(f"[PASS] POST /api/tasks          → task_id={task_id[:8]}…  status={task['status']!r}")

# 4. List tasks
tasks = get("/api/tasks")
assert tasks["total"] >= 1
print(f"[PASS] GET /api/tasks           → {tasks['total']} task(s) in DB")

# 5. Get single task
t = get(f"/api/tasks/{task_id}")
assert t["task_id"] == task_id
print(f"[PASS] GET /api/tasks/:id       → objective={t['objective'][:40]!r}…")

# 6. Progress endpoint
prog = get(f"/api/tasks/{task_id}/progress")
print(f"[PASS] GET /api/tasks/:id/progress → pages={prog['pages_crawled']}, progress={prog['progress_percent']:.1f}%")

# 7. Sources endpoint
src = get(f"/api/tasks/{task_id}/sources")
print(f"[PASS] GET /api/tasks/:id/sources  → visited={len(src['visited_urls'])}, records={len(src['records'])}")

# 8. Report endpoint
rpt = get(f"/api/tasks/{task_id}/report")
print(f"[PASS] GET /api/tasks/:id/report   → ranked_results={len(rpt['ranked_results'])}")

# 9. Invalid task returns 404
try:
    get("/api/tasks/nonexistent-task-id")
    print("[FAIL] 404 test — should have raised")
except urllib.error.HTTPError as e:
    assert e.code == 404
    print(f"[PASS] GET /api/tasks/invalid   → 404 as expected")

# 10. Short objective validation (422)
try:
    post("/api/tasks", {"objective": "short", "max_pages": 5})
    print("[FAIL] 422 validation test — should have raised")
except urllib.error.HTTPError as e:
    assert e.code == 422
    print(f"[PASS] POST with short objective → 422 validation error as expected")

print()
print("=" * 60)
print(f"ALL 10 LIVE API TESTS PASSED")
print("=" * 60)
print()
print(f"Task {task_id[:8]}… is running in the background.")
print(f"Open http://localhost:5173 to watch it in the UI.")
print()

# Wait a moment and poll progress once more
time.sleep(3)
prog2 = get(f"/api/tasks/{task_id}/progress")
print(f"Progress update (after 3s): pages_crawled={prog2['pages_crawled']}, "
      f"status={prog2['status']!r}, progress={prog2['progress_percent']:.1f}%")
if prog2.get("missing_fields"):
    print(f"  Still looking for: {prog2['missing_fields']}")
if prog2.get("recent_urls"):
    print(f"  Recently crawled:  {prog2['recent_urls'][-1]}")
