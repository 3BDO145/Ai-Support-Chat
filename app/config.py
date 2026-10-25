import os

from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:1.5b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
SAMPLE_SIZE = int(os.getenv("SAMPLE_SIZE", "2000"))

KB_PATH = "data/knowledge_base.csv"
CHROMA_PATH = "data/chroma_db"
COLLECTION_NAME = "support_kb"

TICKETS_DB_PATH = os.getenv("TICKETS_DB_PATH", "data/tickets.db")
ORDERS_DB_PATH = os.getenv("ORDERS_DB_PATH", "data/orders.db")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"