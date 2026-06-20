import os

APP_ROOT = os.environ.get("APP_ROOT", "/app").strip().rstrip("/") or "/app"
CONFIG_DIR = os.path.join(APP_ROOT, "config")
LOG_DIR = os.path.join(APP_ROOT, "logs")
STRM_ROOT = os.path.join(APP_ROOT, "strm")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")
TREE_DIR = os.path.join(CONFIG_DIR, "trees")
DB_PATH = os.path.join(CONFIG_DIR, "data.db")
SESSION_SECRET_PATH = os.path.join(CONFIG_DIR, ".session_secret")
