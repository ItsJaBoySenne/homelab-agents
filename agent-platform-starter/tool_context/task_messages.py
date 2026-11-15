"""Message enrichment kept outside the FastAPI route module."""
from datetime import datetime, timezone
from typing import Any, Callable

from .proxmox import build_context


def enrich_messages(messages: list[dict[str, Any]], task_text: str,
                    workspace: str, fetch_inventory: Callable[[], Any]
                    ) -> list[dict[str, Any]]:
    """Add trusted runtime date and sanitized untrusted inventory before user msg."""
    result = list(messages)
    additions: list[dict[str, Any]] = [{
        "role": "system",
        "content": ("Execution date (UTC): " +
                    datetime.now(timezone.utc).date().isoformat() +
                    ". Use this date for new reports unless specified otherwise."),
    }]
    context = build_context(task_text, workspace, fetch_inventory)
    if context:
        additions.append({"role": "system", "content": context})
    index = next((idx for idx in range(len(result) - 1, -1, -1)
                  if result[idx].get("role") == "user"), len(result))
    result[index:index] = additions
    return result
