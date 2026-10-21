"""Create local secrets. Never overwrites an existing configuration."""
from pathlib import Path
import os
import secrets

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / ".env"
if path.exists():
    raise SystemExit(".env already exists. Edit it directly; no secrets were overwritten.")
text = (ROOT / ".env.example").read_text()
token = secrets.token_urlsafe(32)
text = text.replace("INGEST_API_TOKEN=\n", "INGEST_API_TOKEN=" + token + "\n")
text = text.replace("N8N_MCP_TOKEN=\n", "N8N_MCP_TOKEN=" + secrets.token_urlsafe(32) + "\n")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as file:
    file.write(text)
print("Created .env with unique secrets and local-only defaults.")
print("Keep .env private. Do not commit it or paste its contents into a public issue.")
