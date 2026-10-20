"""Enforcement + cleanup workflow, modelled on how Indian urban local bodies
handle littering under the Solid Waste Management Rules, 2016 and city bye-laws:

  camera detects -> sanitary inspector reviews evidence -> notice / spot-fine
  issued -> offender pays (or case is escalated) -> closed.

In parallel the litter itself is a cleanup job for the ward's sanitation staff:
  pending -> assigned -> cleaned.

Every action is recorded in audit_log with who did it.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import now_iso
from .settings import Settings

STATUSES = ["pending_review", "verified", "notice_issued", "paid", "escalated", "rejected"]
CLEANUP = ["pending", "assigned", "cleaned"]

# action -> (allowed current statuses, new status)
TRANSITIONS: dict[str, tuple[set[str], str]] = {
    "verify":       ({"pending_review"}, "verified"),
    "reject":       ({"pending_review", "verified"}, "rejected"),
    "issue_notice": ({"verified"}, "notice_issued"),
    "mark_paid":    ({"notice_issued", "escalated"}, "paid"),
    "escalate":     ({"notice_issued"}, "escalated"),
    "reopen":       ({"rejected"}, "pending_review"),
}
CLEANUP_ACTIONS = {"assign_cleanup": "assigned", "mark_cleaned": "cleaned"}
OTHER_ACTIONS = {"correct_plate", "add_note"}
ALL_ACTIONS = set(TRANSITIONS) | set(CLEANUP_ACTIONS) | OTHER_ACTIONS

PLATE_RE = re.compile(r"^([A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}|\d{2}BH\d{4}[A-Z]{1,2})$")


class WorkflowError(ValueError):
    pass


def fine_for(settings: Settings, incident: dict) -> int:
    if incident["offender_type"] == "vehicle":
        return settings.fines.get(incident.get("vehicle_category") or "car", settings.fines.get("car", 0))
    return settings.fines.get(incident["offender_type"], 0)


def _next_notice_number(conn: sqlite3.Connection, settings: Settings, incident: dict) -> str:
    year = datetime.now(timezone.utc).year
    ward = re.sub(r"[^A-Z0-9]", "", (incident.get("ward") or "W").upper()) or "W"
    prefix = f"{settings.notice_prefix}/{ward}/{year}/"
    row = conn.execute("SELECT COUNT(*) FROM incidents WHERE notice_number LIKE ?", (prefix + "%",)).fetchone()
    return f"{prefix}{row[0] + 1:05d}"


def apply_action(conn: sqlite3.Connection, settings: Settings, incident: dict, body: dict) -> dict:
    """Validate and apply one officer action. Returns the column updates made."""
    action = body.get("action")
    actor = (body.get("actor") or "").strip() or "Officer"
    note = (body.get("note") or "").strip() or None
    if action not in ALL_ACTIONS:
        raise WorkflowError(f"Unknown action '{action}'")
    updates: dict = {}
    log_from = log_to = None

    if action in TRANSITIONS:
        allowed, new_status = TRANSITIONS[action]
        if incident["status"] not in allowed:
            raise WorkflowError(f"'{action.replace('_', ' ')}' is not allowed while the case is "
                                f"{incident['status'].replace('_', ' ')}")
        if action == "issue_notice" and incident["offender_type"] == "unknown":
            raise WorkflowError("No offender was identified; only cleanup can be tracked")
        updates["status"] = new_status
        log_from, log_to = incident["status"], new_status
        if action == "reject":
            reason = (body.get("reason") or note or "").strip()
            if not reason:
                raise WorkflowError("A reason is required to reject an incident")
            updates["rejection_reason"] = reason
        if action == "issue_notice":
            if incident["offender_type"] == "vehicle" and not (incident.get("plate_number") and incident.get("plate_valid")):
                raise WorkflowError("Add or correct the number plate before issuing a notice")
            amount = body.get("fine_amount")
            amount = fine_for(settings, incident) if amount in (None, "") else int(amount)
            if amount < 0:
                raise WorkflowError("Fine amount cannot be negative")
            issued = datetime.now(timezone.utc).replace(microsecond=0)
            updates.update(fine_amount=amount, notice_number=_next_notice_number(conn, settings, incident),
                           notice_issued_at=issued.isoformat(),
                           due_date=(issued + timedelta(days=settings.notice_due_days)).isoformat())
        if action == "mark_paid":
            updates.update(paid_at=now_iso(), payment_ref=(body.get("payment_ref") or "").strip() or None)
        if action == "reopen":
            updates["rejection_reason"] = None

    elif action in CLEANUP_ACTIONS:
        new = CLEANUP_ACTIONS[action]
        if incident["cleanup_status"] == "cleaned":
            raise WorkflowError("Litter is already marked as cleaned")
        if new == incident["cleanup_status"]:
            raise WorkflowError(f"Cleanup is already {new}")
        updates["cleanup_status"] = new
        if action == "assign_cleanup":
            updates["cleanup_assignee"] = (body.get("assignee") or "").strip() or "Ward sanitation team"
        log_from, log_to = incident["cleanup_status"], new

    elif action == "correct_plate":
        plate = re.sub(r"[^A-Z0-9]", "", str(body.get("plate") or "").upper())
        if not PLATE_RE.match(plate):
            raise WorkflowError("Plate must look like KA01AB1234 or 22BH1234AA")
        if incident["offender_type"] == "unknown":
            raise WorkflowError("Mark the offender before adding a plate")
        if incident["status"] in ("notice_issued", "paid", "escalated"):
            raise WorkflowError("The plate cannot change after a notice was issued")
        updates.update(plate_number=plate, plate_display=pretty_plate(plate), plate_valid=1, plate_corrected=1)
        log_from, log_to = incident.get("plate_number"), plate

    elif action == "add_note":
        if not note:
            raise WorkflowError("Note is empty")

    if updates:
        updates["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE incidents SET {cols} WHERE id = ?", (*updates.values(), incident["id"]))
    conn.execute("INSERT INTO audit_log (incident_id, at, actor, action, from_value, to_value, note) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (incident["id"], now_iso(), actor, action, log_from, log_to, note))
    return updates


def pretty_plate(plate: str | None) -> str | None:
    if not plate:
        return plate
    m = re.fullmatch(r"([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})", plate)
    if m:
        return " ".join(p for p in m.groups() if p)
    m = re.fullmatch(r"(\d{2})(BH)(\d{4})([A-Z]{1,2})", plate)
    return " ".join(m.groups()) if m else plate
