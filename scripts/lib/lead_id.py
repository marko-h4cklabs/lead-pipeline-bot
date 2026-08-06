"""Generira kratke, jedinstvene, URL-sigurne lead_id vrijednosti."""
import uuid


def new_lead_id() -> str:
    """Vraća 8-znakoven hex prefiks UUID-a, npr. 'a3f8c120'."""
    return uuid.uuid4().hex[:8]
