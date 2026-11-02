import os
import shutil
from pathlib import Path

store_dir = Path(os.path.expanduser("~/agy-review/gemini4/store_unreadable_fixtures"))
if store_dir.exists():
    shutil.rmtree(store_dir)
jobs_dir = store_dir / "jobs"
jobs_dir.mkdir(parents=True, exist_ok=True)
jobs_dir.chmod(0o000)

print(f"Store prepared at {store_dir} with unreadable jobs/ directory (mode 0000)")
