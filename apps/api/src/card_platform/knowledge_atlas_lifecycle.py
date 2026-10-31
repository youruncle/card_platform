"""Read-only proposal history from immutable audit events and version membership.

Publication state is snapshot inclusion, not proof that an overlay survived a
source conflict or is the last correction to each field. No evidence is rewritten.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID


def _time(event):
    payload = event["after_value"] or {}
    value = (
        payload.get("reviewed_at")
        if event["action"] == "atlas_review"
        else payload.get("submitted_at")
        if event["action"] == "atlas_submit"
        else None
    )
    return datetime.fromisoformat(value) if value else event["created_at"]


async def proposal_lifecycles(connection, workspace_id, items):
    if not items:
        return items
    proposal_ids = [UUID(item["id"]) for item in items]
    result = await connection.execute(
        """WITH history_floor AS (
             SELECT min(v.version_number) AS number FROM knowledge_audit_log p
             JOIN knowledge_version v ON v.id=(p.after_value->>'version_id')::uuid
             WHERE p.workspace_id=%s AND p.action='atlas_applied' AND p.object_id=ANY(%s)
           )
           SELECT a.id,a.action,a.object_id,a.actor_id,a.created_at,
                  CASE WHEN a.object_type='atlas_proposal' THEN a.after_value
                       ELSE '{}'::jsonb END AS after_value,
                  CASE WHEN a.action='rollback' THEN a.before_value->>'version_id'
                       ELSE NULL END AS from_version_id,
                  w.published_version_id AS current_version_id
           FROM knowledge_audit_log a JOIN knowledge_workspace w ON w.id=a.workspace_id
           CROSS JOIN history_floor h
           LEFT JOIN knowledge_version target ON target.id=a.object_id AND a.object_type='version'
           WHERE a.workspace_id=%s AND (
             (a.object_type='atlas_proposal' AND a.object_id=ANY(%s))
             OR (a.object_type='version' AND a.action IN ('publish','rollback')
                 AND (target.version_number>=h.number OR (a.action='rollback' AND EXISTS(
                   SELECT 1 FROM knowledge_version old WHERE old.workspace_id=a.workspace_id
                   AND old.id=(a.before_value->>'version_id')::uuid AND old.version_number>=h.number)))))
           ORDER BY a.created_at,a.id""",
        (workspace_id, proposal_ids, workspace_id, proposal_ids),
    )
    events = [dict(row) for row in await result.fetchall()]
    if not events:
        return items
    events.sort(key=lambda event: (_time(event), str(event["id"])))
    current_id = str(events[0]["current_version_id"]) if events[0]["current_version_id"] else None
    version_ids = {current_id} if current_id else set()
    for event in events:
        if event["action"] in {"publish", "rollback"}:
            version_ids.add(str(event["object_id"]))
        if event["from_version_id"]:
            version_ids.add(event["from_version_id"])
        if event["action"] == "atlas_applied":
            version_ids.add(event["after_value"]["version_id"])
    versions_result = await connection.execute(
        """SELECT v.id,v.version_number,v.release_notes,
                  ARRAY(SELECT p->>'id' FROM jsonb_array_elements(
                      COALESCE(v.snapshot->'atlas_corrections','[]'::jsonb)) p) AS proposal_ids
           FROM knowledge_version v WHERE v.workspace_id=%s AND v.id=ANY(%s)""",
        (workspace_id, [UUID(value) for value in version_ids]),
    )
    versions = {str(row["id"]): dict(row) for row in await versions_result.fetchall()}

    def version_ref(value):
        if not value:
            return None
        key = str(value)
        return {"id": key, "number": versions.get(key, {}).get("version_number")}

    def includes(version_id, proposal_id):
        return proposal_id in versions.get(str(version_id), {}).get("proposal_ids", [])

    published_event_versions = {
        str(event["object_id"]) for event in events if event["action"] == "publish"
    }
    output = []
    for original in items:
        item = dict(original)
        pid = item["id"]
        own = [
            event
            for event in events
            if str(event["object_id"]) == pid
            and event["action"] in {"atlas_submit", "atlas_review", "atlas_applied"}
        ]
        for event in own:
            if event["action"] in {"atlas_submit", "atlas_review"}:
                item.update(event["after_value"])
        applied_ids = {
            event["after_value"]["version_id"]
            for event in own
            if event["action"] == "atlas_applied"
        }
        timeline = []
        approval_times = [
            _time(event)
            for event in own
            if event["action"] == "atlas_review"
            and event["after_value"].get("status") == "approved"
        ]
        prerequisite = min(approval_times) if approval_times else None
        last_removal = None
        for event in events:
            action = event["action"]
            own_event = str(event["object_id"]) == pid
            payload = event["after_value"] or {}
            entry = {
                "id": str(event["id"]),
                "at": _time(event).isoformat(),
                "actor_id": str(event["actor_id"]) if event["actor_id"] else None,
            }
            if own_event and action in {"atlas_submit", "atlas_review"}:
                entry.update(
                    kind="submitted" if action == "atlas_submit" else payload["status"],
                    reason=payload.get("reason")
                    if action == "atlas_submit"
                    else payload.get("review_reason"),
                )
            elif action == "publish" and includes(event["object_id"], pid):
                vid = str(event["object_id"])
                entry.update(
                    kind="published" if vid in applied_ids else "carried_forward",
                    version=version_ref(vid),
                    reason=versions[vid].get("release_notes"),
                )
            elif (
                own_event
                and action == "atlas_applied"
                and payload["version_id"] not in published_event_versions
            ):
                # Older imported audit history can have the applied marker without
                # its publication event. Preserve that evidence rather than invent one.
                entry.update(
                    kind="published", version=version_ref(payload["version_id"]), reason=None
                )
            elif action == "rollback":
                before = includes(event["from_version_id"], pid)
                after = includes(event["object_id"], pid)
                if not before and not after:
                    continue
                kind = (
                    "rollback_retained"
                    if before and after
                    else "rollback_restored"
                    if after
                    else "rollback_removed"
                )
                if kind == "rollback_removed":
                    last_removal = kind
                elif kind == "rollback_restored":
                    last_removal = None
                entry.update(
                    kind=kind,
                    from_version=version_ref(event["from_version_id"]),
                    version=version_ref(event["object_id"]),
                    reason=None,
                )
            else:
                continue
            if entry["kind"] in {"submitted", "approved", "rejected"}:
                entry["time_basis"] = "payload_recorded_time"
            else:
                entry["time_basis"] = (
                    "transaction_time_uncertain"
                    if prerequisite and _time(event) < prerequisite
                    else "audit_recorded_time"
                )
            timeline.append(entry)
        contained = bool(current_id and includes(current_id, pid))
        application_versions = sorted(
            (version_ref(vid) for vid in versions if includes(vid, pid)),
            key=lambda value: (value["number"] or 0, value["id"]),
        )
        published_once = bool(applied_ids or application_versions)
        if item["status"] == "approved":
            state = (
                "current"
                if contained
                else "rolled_back"
                if published_once and last_removal
                else "not_current"
                if published_once
                else "stale_draft"
                if str(item["base_version_id"]) != current_id
                else "approved_draft"
            )
        else:
            state = item["status"]
        item.update(
            published_once=published_once,
            lifecycle={
                "state": state,
                "current_version": version_ref(current_id),
                "current_version_contains": contained,
                "application_versions": application_versions,
                "timeline": timeline,
                "timeline_order_uncertain": any(
                    event["time_basis"] == "transaction_time_uncertain" for event in timeline
                ),
                "scope": "snapshot_inclusion_not_effective_field_proof",
            },
        )
        output.append(item)
    return output
