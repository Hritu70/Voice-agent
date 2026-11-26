import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Load .env from the project root regardless of the current working directory.
load_dotenv(ROOT / ".env")

PROMPTS = ROOT / "prompts"
STATIC = ROOT / "static"
KB_DIR = ROOT / "data" / "kb"
CHROMA_DIR = ROOT / "data" / "chroma"
DB_PATH = ROOT / "data" / "sales_agent.db"

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

TOP_K = 3
MAX_DIST = 0.65
LEAD_FIELDS = {
    "name": "caller name", "need": "customer need", "budget": "budget range",
    "timeline": "purchase timeline", "authority": "decision-making authority",
    "company_size": "company size or number of users",
}
