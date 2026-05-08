"""Quick check: does .env load correctly?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
print(f".env path: {env_path}")
print(f".env exists: {env_path.exists()}")

load_dotenv(env_path, override=True)

keys_to_check = [
    "DATABASE_URL",
    "REDIS_URL",
    "LOG_LEVEL",
    "ANTHROPIC_API_KEY",
    "DISCORD_WEBHOOK_URL",
]

for key in keys_to_check:
    val = os.getenv(key, "")
    print(f"  {key}: {'SET' if val else 'EMPTY'} (len={len(val)})")
