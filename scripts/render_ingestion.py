"""Configure workflow URL and recipient placeholders locally; never inserts tokens."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--app-url", required=True)
parser.add_argument("--source-url", required=True)
args = parser.parse_args()
for value in [args.app_url, args.source_url]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        parser.error("URLs must be HTTP(S) and must not embed credentials.")
workflow = json.loads((root / "n8n/scheduled-ingestion.json").read_text())
for node in workflow["nodes"]:
    if node["name"] == "Fetch your review JSON":
        node["parameters"]["url"] = args.source_url
    elif node["name"] == "Submit analysis job":
        node["parameters"]["url"] = args.app_url.rstrip("/") + "/api/jobs"
out = root / "n8n/configured/scheduled-ingestion.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(workflow, indent=2) + "\n")
print("Wrote " + str(out) + ". Connect credentials in n8n before activation.")
