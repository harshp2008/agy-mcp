import os
import shutil
import tempfile
from pathlib import Path

store_dir = Path(os.path.expanduser("~/agy-review/gemini4/store_dup_key_fixture"))
if store_dir.exists():
    shutil.rmtree(store_dir)
jobs_dir = store_dir / "jobs"
jobs_dir.mkdir(parents=True, exist_ok=True)

cand_id = "11111111-1111-1111-1111-111111111111"
kept_id = "22222222-2222-2222-2222-222222222222"

(jobs_dir / cand_id).mkdir(parents=True, exist_ok=True)
(jobs_dir / kept_id).mkdir(parents=True, exist_ok=True)

temp_dir = tempfile.gettempdir()
pytest_repo = f"{temp_dir}/pytest-of-testuser/pytest-1/test_foo0/repo"

cand_content = f"""{{
  "job_id": "{cand_id}",
  "state": "done",
  "repo_path": "{pytest_repo}",
  "created_at": "2020-01-01T00:00:00+00:00"
}}"""
(jobs_dir / cand_id / "job.json").write_text(cand_content, encoding="utf-8")

# Kept job with duplicate key that shadows a reference to cand_id
kept_content = f"""{{
  "job_id": "{kept_id}",
  "state": "running",
  "repo_path": "/some/real/repo",
  "created_at": "2020-01-01T00:00:00+00:00",
  "related_job": "{cand_id}",
  "related_job": "unrelated"
}}"""
(jobs_dir / kept_id / "job.json").write_text(kept_content, encoding="utf-8")

print(f"Store prepared at {store_dir}")
