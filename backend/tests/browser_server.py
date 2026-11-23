"""Cross-platform isolated real-browser backend. Never loads the project .env."""
import os
import runpy
from pathlib import Path
from uuid import uuid4

from app.core import config

root = Path(__file__).resolve().parents[3] / ".validation" / ("e2e-browser-" + uuid4().hex)
root.mkdir(parents=True)
database_url = "sqlite:///" + (root / "e2e-browser.db").as_posix()
storage = root / "storage"
os.environ["DATABASE_URL"] = database_url
os.environ["STORAGE_DIR"] = str(storage)
settings = config.Settings(_env_file=None, database_url=database_url, storage_dir=storage,
    public_url="http://127.0.0.1:5177", cors_origins=["http://127.0.0.1:5177"],
    wecom_corp_id="", wecom_secret="", wecom_directory_secret="", smtp_host="", smtp_password="",
    ai_api_key="", native_todo_enabled=False, native_todo_app_enabled=False,
    wecom_direct_notifications_enabled=False, backup_dir=root / "backups", monitor_state_dir=root / "monitor")
config.get_settings = lambda: settings
runpy.run_module("tests.seed_e2e", run_name="__main__")

if __name__ == "__main__":
    import uvicorn

    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=8017, access_log=False)
