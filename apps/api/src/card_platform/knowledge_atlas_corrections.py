"""Audit-backed atlas proposals. Only publication materializes approved corrections.

No source attachment, collection card, ledger, or valuation is mutated here.
"""

from __future__ import annotations

from hashlib import sha256
import json
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import zlib

from card_platform.knowledge_repository import KnowledgeConflictError, KnowledgeRepository

FIELDS = (
    "player",
    "card_number",
    "section",
    "parallel",
    "serial_total",
    "season_start",
    "season_end",
    "is_autograph",
)
IDENTITY = ("product_id", "manufacturer", *FIELDS)
LOCK = "SELECT pg_advisory_xact_lock(hashtext('card-platform-knowledge-publish'))"


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def fingerprint(card: dict) -> str:
    return sha256(
        _json(
            {
                key: card.get(key)
                for key in (
                    *IDENTITY,
                    "sources",
                    "source_key",
                    "source_checksum",
                    "source_variant_label",
                    "official_artifact_sha256",
                )
            }
        ).encode()
    ).hexdigest()


def validate_proposal(payload: dict, card: dict) -> dict:
    changes = payload.get("changes")
    if not isinstance(changes, dict) or not changes or set(changes) - set(FIELDS):
        raise ValueError("请选择可纠错的身份字段；图片请通过来源证据说明，暂不直接修改")
    for key, value in changes.items():
        if value is None:
            continue
        if key == "is_autograph":
            valid = type(value) is bool
        elif key in {"season_start", "season_end", "serial_total"}:
            valid = type(value) is int and (
                1900 <= value <= 2200 if key.startswith("season") else 1 <= value <= 1000000
            )
        else:
            valid = (
                isinstance(value, str)
                and bool(value.strip())
                and len(value) <= 200
                and value == value.strip()
            )
        if not valid:
            raise ValueError(f"字段 {key} 的值无效；未知请使用 null")
    merged = {**card, **changes}
    if (
        merged.get("season_start") is not None
        and merged.get("season_end") is not None
        and merged["season_end"] < merged["season_start"]
    ):
        raise ValueError("结束年份不能早于起始年份")
    if all(card.get(key) == value for key, value in changes.items()):
        raise ValueError("新值与当前值相同")
    if (
        not isinstance(payload.get("reason"), str)
        or not 3 <= len(payload["reason"].strip()) <= 2000
    ):
        raise ValueError("请填写至少3字的纠错理由")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 10:
        raise ValueError("请提供1至10条证据链接")
    for url in evidence:
        try:
            parts = urlsplit(url) if isinstance(url, str) and len(url) <= 2000 else None
            valid = (
                parts
                and parts.scheme in {"https", "http"}
                and parts.hostname
                and not parts.username
                and not parts.password
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("证据必须是有效的 HTTP(S) 链接")
    return {key: card.get(key) for key in changes}


class CorrectionRepository:
    async def list(self, connection, workspace_id, card_id=None, limit=100, offset=0):
        result = await connection.execute(
            """SELECT latest.after_value || jsonb_build_object('published_once', EXISTS(
                   SELECT 1 FROM knowledge_audit_log p WHERE p.workspace_id=%s
                     AND p.action='atlas_applied' AND p.object_id=latest.object_id)) AS after_value
               FROM (SELECT DISTINCT ON (object_id) object_id,after_value
                 FROM knowledge_audit_log WHERE workspace_id=%s AND object_type='atlas_proposal'
                   AND action IN ('atlas_submit','atlas_review')
                   AND (%s::text IS NULL OR after_value->>'card_id'=%s)
                 ORDER BY object_id,(after_value->>'reviewed_at')::timestamptz DESC NULLS LAST,
                          created_at DESC,id DESC) latest
               ORDER BY (after_value->>'submitted_at')::timestamptz DESC,object_id DESC
               LIMIT %s OFFSET %s""",
            (workspace_id, workspace_id, card_id, card_id, limit, offset),
        )
        from card_platform.knowledge_atlas_lifecycle import proposal_lifecycles

        return await proposal_lifecycles(
            connection, workspace_id, [row["after_value"] for row in await result.fetchall()]
        )

    async def _duplicate(self, connection, workspace_id, actor_id, key, action, payload):
        if not key or len(key) > 200:
            raise ValueError("必须提供稳定的 Idempotency-Key（不超过200字）")
        result = await connection.execute(
            "SELECT actor_id,action,after_value FROM knowledge_audit_log WHERE workspace_id=%s AND request_id=%s",
            (workspace_id, key),
        )
        row = await result.fetchone()
        if row:
            item = row["after_value"]
            if (
                str(row["actor_id"]) != str(actor_id)
                or row["action"] != action
                or item.get("request_digest") != sha256(_json(payload).encode()).hexdigest()
            ):
                raise KnowledgeConflictError("幂等键已用于不同请求，请刷新后重试")
            return item
        return None

    async def _current(self, connection, workspace_id, version_id, browser):
        result = await connection.execute(
            "SELECT published_version_id FROM knowledge_workspace WHERE id=%s FOR UPDATE",
            (workspace_id,),
        )
        row = await result.fetchone()
        if str(row["published_version_id"]) != str(version_id) or str(browser.version_id) != str(
            version_id
        ):
            raise KnowledgeConflictError("发布版本已改变，请刷新详情后重新提出纠错")

    async def submit(self, connection, workspace, actor_id, payload, key, browser):
        await connection.execute(LOCK)
        if duplicate := await self._duplicate(
            connection, workspace["id"], actor_id, key, "atlas_submit", payload
        ):
            return duplicate
        await self._current(connection, workspace["id"], payload["base_version_id"], browser)
        card = browser.card(payload["card_id"])["card"]
        if card.get("atlas", {}).get("identity_status") == "conflict":
            raise KnowledgeConflictError("该图鉴已有来源漂移冲突，需先核查来源")
        if fingerprint(card) != payload["base_fingerprint"]:
            raise KnowledgeConflictError("图鉴身份已改变，请刷新详情")
        before = validate_proposal(payload, card)
        item = {
            **payload,
            "id": str(uuid4()),
            "before": before,
            "status": "pending",
            "submitted_by": str(actor_id),
            "request_digest": sha256(_json(payload).encode()).hexdigest(),
        }
        now = await connection.execute("SELECT clock_timestamp() AS time")
        item["submitted_at"] = str((await now.fetchone())["time"])
        await KnowledgeRepository().audit(
            connection,
            workspace["id"],
            actor_id,
            "atlas_submit",
            "atlas_proposal",
            UUID(item["id"]),
            None,
            item,
            key,
        )
        return item

    async def review(
        self, connection, workspace, actor_id, proposal_id, decision, reason, key, browser
    ):
        await connection.execute(LOCK)
        request = {"proposal_id": str(proposal_id), "decision": decision, "reason": reason}
        if duplicate := await self._duplicate(
            connection, workspace["id"], actor_id, key, "atlas_review", request
        ):
            return duplicate
        if decision not in {"approved", "rejected"} or not 3 <= len(reason.strip()) <= 2000:
            raise ValueError("请填写至少3字的审核理由，并选择批准或驳回")
        result = await connection.execute(
            """SELECT after_value FROM knowledge_audit_log WHERE workspace_id=%s
               AND object_type='atlas_proposal' AND object_id=%s
                 AND action IN ('atlas_submit','atlas_review')
               ORDER BY (after_value->>'reviewed_at')::timestamptz DESC NULLS LAST,
                        created_at DESC,id DESC LIMIT 1""",
            (workspace["id"], UUID(str(proposal_id))),
        )
        row = await result.fetchone()
        if not row:
            raise KeyError("提案不存在")
        item = row["after_value"]
        if item["status"] != "pending":
            # An unpublished approval can be rejected after a rollback or an
            # automatic source version advance. Published review history is immutable.
            applied = await connection.execute(
                "SELECT 1 FROM knowledge_audit_log WHERE workspace_id=%s AND action='atlas_applied' AND object_id=%s LIMIT 1",
                (workspace["id"], UUID(item["id"])),
            )
            if item["status"] != "approved" or decision != "rejected" or await applied.fetchone():
                raise KnowledgeConflictError("提案已经审核或已发布，请刷新记录")
        if decision == "approved":
            await self._current(connection, workspace["id"], item["base_version_id"], browser)
            card = browser.card(item["card_id"])["card"]
            if card.get("correction_conflict") or fingerprint(card) != item["base_fingerprint"]:
                raise KnowledgeConflictError("图鉴身份已改变，请重新提出纠错")
            competing = await connection.execute(
                """SELECT 1 FROM (SELECT DISTINCT ON (object_id) object_id,after_value
                    FROM knowledge_audit_log WHERE workspace_id=%s AND action='atlas_review'
                    ORDER BY object_id,(after_value->>'reviewed_at')::timestamptz DESC,created_at DESC,id DESC) r
                   WHERE after_value->>'status'='approved' AND after_value->>'card_id'=%s
                   AND NOT EXISTS(SELECT 1 FROM knowledge_audit_log p WHERE p.workspace_id=%s
                       AND p.action='atlas_applied' AND p.object_id=r.object_id) LIMIT 1""",
                (workspace["id"], item["card_id"], workspace["id"]),
            )
            if await competing.fetchone():
                raise KnowledgeConflictError("同一图鉴已有已审草稿，请先发布并刷新后再处理")
        now = await connection.execute("SELECT clock_timestamp() AS time")
        reviewed = {
            **item,
            "status": decision,
            "reviewed_by": str(actor_id),
            "review_reason": reason,
            "reviewed_at": str((await now.fetchone())["time"]),
            "request_digest": sha256(_json(request).encode()).hexdigest(),
        }
        await KnowledgeRepository().audit(
            connection,
            workspace["id"],
            actor_id,
            "atlas_review",
            "atlas_proposal",
            UUID(item["id"]),
            item,
            reviewed,
            key,
        )
        await connection.execute(
            "UPDATE knowledge_workspace SET revision=revision+1,updated_at=now() WHERE id=%s",
            (workspace["id"],),
        )
        return reviewed


async def snapshot_corrections(
    connection, workspace_id, published_version_id, *, include_drafts=True
):
    """Carry published overlays, add approved drafts once; rollback never resurrects them."""
    previous = []
    if published_version_id:
        result = await connection.execute(
            "SELECT snapshot->'atlas_corrections' AS corrections FROM knowledge_version WHERE id=%s",
            (published_version_id,),
        )
        row = await result.fetchone()
        previous = row["corrections"] or [] if row else []
    if not include_drafts:
        return previous, []
    result = await connection.execute(
        """SELECT r.after_value FROM (
             SELECT DISTINCT ON (object_id) object_id,after_value,created_at FROM knowledge_audit_log
             WHERE workspace_id=%s AND action='atlas_review'
             ORDER BY object_id,(after_value->>'reviewed_at')::timestamptz DESC,created_at DESC,id DESC) r
           WHERE after_value->>'status'='approved'
           AND NOT EXISTS(SELECT 1 FROM knowledge_audit_log p WHERE p.workspace_id=%s
               AND p.action='atlas_applied' AND p.object_id=r.object_id)
           ORDER BY created_at,object_id""",
        (workspace_id, workspace_id),
    )
    drafts = [row["after_value"] for row in await result.fetchall()]
    if any(str(item["base_version_id"]) != str(published_version_id) for item in drafts):
        raise KnowledgeConflictError(
            "已审图鉴草稿基于旧发布版本，请先驳回未发布草稿，再基于当前版本重新提出纠错"
        )
    return previous + drafts, drafts


def apply_corrections(db, snapshot):
    """Apply guarded overlays to the disposable index, retaining the original evidence."""
    from card_platform.knowledge_browser import _key

    for item in snapshot.get("atlas_corrections", []):
        row = db.execute("SELECT rowid,data FROM cards WHERE id=?", (item["card_id"],)).fetchone()
        if not row:
            continue
        card = json.loads(zlib.decompress(row[1]))
        if card.get("correction_conflict") or fingerprint(card) != item["base_fingerprint"]:
            card["atlas"]["identity_status"] = "conflict"
            card["correction_conflict"] = {
                "proposal_id": item["id"],
                "reason": "来源身份已改变，旧纠错未应用",
            }
        else:
            card.setdefault("original_identity", {key: card.get(key) for key in FIELDS})
            card.setdefault(
                "source_atlas",
                {
                    "scope": "original_source_assertions",
                    **{
                        key: card["atlas"].get(key)
                        for key in (
                            "identity_status",
                            "identity_scope",
                            "serial_status",
                            "autograph_status",
                        )
                    },
                },
            )
            card.update(item["changes"])
            # Effective fields govern the reviewed catalogue identity. Original
            # manufacturer naming and unnumbered evidence remain source assertions;
            # they cannot certify the corrected identity or a physical specimen.
            card["atlas"].update(
                metadata_scope="effective_identity",
                serial_status="known"
                if type(card.get("serial_total")) is int and card["serial_total"] > 0
                else "unknown",
                autograph_status="attribute_true"
                if card.get("is_autograph") is True
                else "attribute_false"
                if card.get("is_autograph") is False
                else "unknown",
                physical_autograph_status=None,
            )
            card["correction_effects"] = {
                "catalogue_identity": "effective",
                "image_bindings": "requires_review",
                "pricing_groups": "not_recomputed",
                "physical_cards": "unchanged",
            }
            card["name"] = " · ".join(
                str(card[k])
                for k in ("player", "card_number", "section", "parallel")
                if card.get(k) is not None
            )
            card.setdefault("field_sources", {}).update(
                {
                    key: {
                        "proposal_id": item["id"],
                        "reason": item["reason"],
                        "evidence": item["evidence"],
                        "reviewed_by": item.get("reviewed_by"),
                        "reviewed_at": item.get("reviewed_at"),
                    }
                    for key in item["changes"]
                }
            )
            card["atlas"].update(
                identity_status="confirmed_by_review"
                if all(
                    card.get(k) is not None
                    for k in (
                        "player",
                        "card_number",
                        "section",
                        "parallel",
                        "season_start",
                        "season_end",
                        "serial_total",
                        "is_autograph",
                    )
                )
                else "needs_review",
                registered_pair=False,
            )
            # Images prove the source identity, not a changed identity. Keep the assets
            # at product scope until their existing binding is reviewed again.
            for media in db.execute(
                "SELECT id,data FROM media WHERE card_id=?", (item["card_id"],)
            ).fetchall():
                data = json.loads(media[1])
                data.update(card_id=None, pair_id=None)
                db.execute(
                    "UPDATE media SET card_id=NULL,pair_id=NULL,data=? WHERE id=?",
                    (_json(data), media[0]),
                )
            db.execute(
                "UPDATE cards SET image_count=0,front=NULL,back=NULL WHERE id=?", (item["card_id"],)
            )
            db.execute("DELETE FROM card_media WHERE card_id=?", (item["card_id"],))
        db.execute(
            "UPDATE cards SET season=?,season_end=?,player=?,card_number=?,section=?,parallel=?,data=? WHERE id=?",
            (
                card.get("season_start"),
                card.get("season_end"),
                _key(card.get("player")),
                _key(card.get("card_number")),
                card.get("section"),
                card.get("parallel"),
                zlib.compress(_json(card).encode(), level=1),
                item["card_id"],
            ),
        )
        search = " ".join(
            _key(card.get(k))
            for k in ("name", "player", "card_number", "product_name", "section", "parallel")
        )
        db.execute("UPDATE card_search SET search=? WHERE card_rowid=?", (search, row[0]))
