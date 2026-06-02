import csv
from pathlib import Path

from app.config import BASE_DIR

DEFAULT_CSV = BASE_DIR / "FyersCredentials.csv"


def load_credentials(path: Path | None = None) -> dict[str, str]:
    """Load Title,Value rows from FyersCredentials.csv into a lowercase-key dict."""
    csv_path = path or DEFAULT_CSV
    if not csv_path.is_file():
        raise FileNotFoundError(f"Fyers credentials file not found: {csv_path}")

    store: dict[str, str] = {}
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("Title") or row.get("title") or "").strip()
            value = (row.get("Value") or row.get("value") or "").strip()
            if title:
                store[title.strip().lower()] = value

    aliases = {
        "client_id": store.get("client_id"),
        "secret_key": store.get("secret_key"),
        "redirect_uri": store.get("redirect_uri"),
        "fy_id": store.get("fy_id"),
        "pin": store.get("pin"),
        "totpkey": store.get("totpkey"),
    }
    return {k: v for k, v in aliases.items() if v}
