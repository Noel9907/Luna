"""
The audit trail.

Deliberately not an activity feed. It records administrative acts that somebody
might later dispute or need explained: accounts created, passwords reset,
retention changed, faces purged, events activated.

`actor_label` is stored as text alongside the foreign key so the record still
says who did it after that account is deleted. A trail that becomes anonymous
when someone leaves is not a trail.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    db: Session,
    *,
    actor_user_id: str | None,
    actor_label: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    studio_id: str | None = None,
    detail: dict | None = None,
) -> AuditLog:
    """
    Adds an audit row to the current transaction.

    Not committed here on purpose. The audit entry has to land in the same
    transaction as the thing it describes, or a crash between the two leaves
    either an unexplained change or a record of something that never happened.
    """
    entry = AuditLog(
        actor_user_id=actor_user_id,
        actor_label=actor_label[:120],
        action=action,
        target_type=target_type,
        target_id=target_id,
        studio_id=studio_id,
        detail=detail,
    )
    db.add(entry)
    return entry


def recent(db: Session, limit: int = 100, studio_id: str | None = None) -> list[AuditLog]:
    q = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if studio_id:
        q = q.where(AuditLog.studio_id == studio_id)
    return list(db.scalars(q).all())


def as_json(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "actor": entry.actor_label,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "studio_id": entry.studio_id,
        "detail": entry.detail,
        "created_at": entry.created_at.isoformat(),
    }
