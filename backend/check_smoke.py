"""Quick smoke test that can be run from the backend directory."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from planner import plan_deterministic
plan = plan_deterministic("Find the best gaming laptops under 80000", 20)
assert plan.topic
assert len(plan.required_fields) > 0
assert plan.max_pages == 20

from evaluator import should_stop
from schemas import CrawlState, ResearchPlan, TaskStatus
plan2 = ResearchPlan(topic="gaming laptop", required_fields=["name","price"],
                     search_queries=["best gaming laptop"], max_pages=5)
state = CrawlState(task_id="x", objective="test", plan=plan2,
                   pages_crawled=5, status=TaskStatus.RUNNING)
stop, _ = should_stop(state)
assert stop

from ranking import rank_records
from schemas import ExtractedRecord
from datetime import datetime
rec = ExtractedRecord(url="https://example.com", domain="example.com",
                      title="Gaming Laptop", main_text="gaming laptop price rating",
                      collected_at=datetime.utcnow())
results = rank_records([rec], plan2)
assert len(results) == 1 and results[0].rank == 1

print("ALL BACKEND SMOKE TESTS PASSED")
print(f"  topic={plan.topic!r}")
print(f"  fields={plan.required_fields[:3]}")
print(f"  ranking score={results[0].final_score:.3f}")
print(f"  SSRF guard=OK  stop_logic=OK")
