import datetime
import logging
import os

import requests

TODOIST_API_URL = "https://api.todoist.com/api/v1/tasks"


def create_task(content: str, description: str = "", priority: int = 1) -> bool:
    """Create a Todoist task. Returns True on success, False otherwise. No-op if token not set."""
    token = os.getenv("TODOIST_API_TOKEN")
    if not token:
        logging.warning("TODOIST_API_TOKEN not set — skipping task creation: %s", content)
        return False
    payload = {
        "content": content,
        "labels": ["trader-bro"],
        "priority": priority,
    }
    if description:
        payload["description"] = description
    try:
        resp = requests.post(
            TODOIST_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        logging.info("Todoist task created: %s", content)
        return True
    except Exception:
        logging.exception("Todoist notification failed for task: %s", content)
        return False


def notify_credit_error(service: str, error_msg: str) -> None:
    """Create an urgent Todoist task for a credit/quota error."""
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    tomorrow = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    description = f"{error_msg}\n\nTimestamp: {timestamp}"

    token = os.getenv("TODOIST_API_TOKEN")
    if not token:
        logging.warning("TODOIST_API_TOKEN not set — skipping credit error notification for %s", service)
        return
    payload = {
        "content": f"[trader-bro] Credit Error: {service} API",
        "description": description,
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
        logging.info("Todoist credit error task created for %s", service)
    except Exception:
        logging.exception("Todoist notification failed for credit error: %s", service)
