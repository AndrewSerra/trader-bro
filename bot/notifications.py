import datetime
import os

import requests

TODOIST_API_URL = "https://api.todoist.com/rest/v2/tasks"


def notify_credit_error(service: str, error_msg: str) -> None:
    """Create a Todoist task for a credit/quota error. No-op if token not set."""
    token = os.getenv("TODOIST_API_TOKEN")
    if not token:
        return
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    tomorrow = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    payload = {
        "content": f"[trader-bro] Credit Error: {service} API",
        "description": f"{error_msg}\n\nTimestamp: {timestamp}",
        "priority": 4,  # p1 (urgent) in Todoist
        "labels": ["trader-bro"],
        "due_date": tomorrow,
    }
    try:
        resp = requests.post(
            TODOIST_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        pass  # Never let notification failure crash the main flow
