from test_knowledge_atlas_corrections import database, payload, run
from test_knowledge_atlas_browser import source_data, external_image, browser
from card_platform.knowledge_repository import KnowledgeRepository, KnowledgeConflictError
from card_platform.knowledge_atlas_corrections import CorrectionRepository
import pytest


def test_review_rollback_strands_approved_draft(database):
    async def scenario():
        connect, make_browser, actor, original = database
        repo, service = KnowledgeRepository(), CorrectionRepository()
        async with await connect() as c:
            ws = await repo.workspace(c)
            newer = await repo.publish(c, ws, actor, ws['revision'], 'new baseline', 'p0', official_source_keys=['unrelated'])
            b = make_browser(newer['snapshot'], newer['id'])
            ws = await repo.workspace(c)
            p = payload(b, newer['id'])
            item = await service.submit(c, ws, actor, p, 's1', b)
            await service.review(c, ws, actor, item['id'], 'approved', 'review valid', 'r1', b)
            ws = await repo.workspace(c)
            await repo.rollback(c, ws, actor, original, ws['revision'], 'rb1')
            ws = await repo.workspace(c)
            with pytest.raises(KnowledgeConflictError, match='旧发布版本'):
                await repo.publish(c, ws, actor, ws['revision'], 'after rollback', 'p1', official_source_keys=['unrelated'])
            with pytest.raises(KnowledgeConflictError, match='已经审核'):
                await service.review(c, ws, actor, item['id'], 'rejected', 'discard stale', 'r2', make_browser())
            replacement = await service.submit(c, ws, actor, payload(make_browser(), original), 's2', make_browser())
            with pytest.raises(KnowledgeConflictError, match='已有已审草稿'):
                await service.review(c, ws, actor, replacement['id'], 'approved', 'replace stale', 'r3', make_browser())
    run(scenario())


def test_review_automatic_publish_consumes_unrelated_draft(database):
    async def scenario():
        connect, make_browser, actor, version = database
        repo, service = KnowledgeRepository(), CorrectionRepository()
        async with await connect() as c:
            ws = await repo.workspace(c)
            b = make_browser()
            item = await service.submit(c, ws, actor, payload(b, version), 's1', b)
            await service.review(c, ws, actor, item['id'], 'approved', 'review valid', 'r1', b)
            ws = await repo.workspace(c)
            published = await repo.publish(c, ws, actor, ws['revision'], 'Automatic official ingestion', None, official_source_keys=['unrelated'])
            assert make_browser(published['snapshot'], published['id']).card(item['card_id'])['card']['serial_total'] == 25
    run(scenario())


def test_review_conflicting_images_are_still_reported_paired(tmp_path):
    data, card, ref = source_data(tmp_path)
    for name, side in [('front.jpg','front'), ('back.jpg','back')]:
        image = external_image(tmp_path, data, card, ref, name=name, side=side)
        image['serial_total'] = 99
    detail = browser(tmp_path, data).card(card['id'])
    external = [m for m in detail['source_mappings'] if m['role'] == 'reviewed_external_record']
    assert external[0]['mapping_status'] == 'needs_review'
    assert 'serial_total_conflict' in external[0]['reasons']
    assert detail['card']['atlas']['image_status'] == 'paired'


def test_review_source_checksum_drift_does_not_invalidate_correction(tmp_path):
    from test_knowledge_browser import artifact
    from card_platform.knowledge_atlas_corrections import fingerprint
    data, card, ref = source_data(tmp_path)
    original = browser(tmp_path, data).card(card['id'])['card']
    old_fingerprint = fingerprint(original)
    data['atlas_corrections'] = [{'id': 'review-proposal', 'card_id': card['id'], 'base_fingerprint': old_fingerprint,
                                 'changes': {'serial_total': 50}, 'reason': 'old source reviewed', 'evidence': ['https://example.org/old']}]
    card['attributes']['source_checksum'] = 'f' * 64
    card['attributes']['raw_text'] = 'updated source bytes without changed displayed identity'
    data['entities'][3]['attributes']['official_artifact'] = artifact(tmp_path, [card])
    current = browser(tmp_path, data).card(card['id'])['card']
    assert current['source_checksum'] != original['source_checksum']
    assert current['official_artifact_sha256'] != original['official_artifact_sha256']
    assert current['serial_total'] == 50
    assert 'correction_conflict' not in current
