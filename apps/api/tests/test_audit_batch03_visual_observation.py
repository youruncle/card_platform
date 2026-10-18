"""Image references must identify actual uploads; prior prompts cannot supply fresh proof."""
import base64
import re
from types import SimpleNamespace

import pytest

from card_platform.comparable_image_verifier import (
    ComparableImageVerifier, VisualCandidate, VisualDecision,
    normalize_model_visual_decision,
)
from card_platform.config import Settings
from card_platform.domain.finish_evidence import build_visual_match_facts
from test_audit_batch02_admission import fixture, observed


@pytest.mark.asyncio
async def test_primary_image_references_identify_each_uploaded_asset_before_observation():
    service = ComparableImageVerifier(Settings(vision_api_key='test-not-a-provider-key'))
    request = {}

    async def provider(**kwargs):
        request.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"decisions": []}'))])

    service.client.chat.completions.create = provider
    candidates = [VisualCandidate('one', 'ignored title', 'https://images.cardhobby.com/one.jpg'),
                  VisualCandidate('two', 'ignored title', 'https://images.cardhobby.com/two.jpg')]
    await service._compare_batch(b'target-front', 'image/jpeg',
        [(candidates[0], b'one-front', 'image/jpeg'),
         (candidates[1], b'two-front', 'image/jpeg')],
        low_resolution_target=False, target_back=(b'target-back', 'image/jpeg'),
        candidate_support={'one': [('https://images.cardhobby.com/one-back.jpg', b'one-back', 'image/jpeg')]})
    refs = []
    previous = ''
    owner = 'target'
    for block in request['messages'][1]['content']:
        if block['type'] == 'text':
            previous = block['text']
            candidate = re.search(r'候选编号 (candidate_\d+)', previous)
            if candidate:
                owner = candidate[1]
        elif block['type'] == 'image_url':
            ref = re.search(r'\bimage_ref=(\w+)\b', previous)
            content = base64.b64decode(block['image_url']['url'].split(',', 1)[1])
            refs.append((owner, ref[1] if ref else None, content))
    await service.client.close()
    assert refs == [
        ('target', 'target_front', b'target-front'),
        ('target', 'target_back', b'target-back'),
        ('candidate_1', 'candidate_front', b'one-front'),
        ('candidate_1', 'candidate_support_1', b'one-back'),
        ('candidate_2', 'candidate_front', b'two-front'),
    ]


@pytest.mark.asyncio
async def test_independent_observation_labels_only_the_two_actual_front_images():
    service = ComparableImageVerifier(Settings(vision_api_key='test-not-a-provider-key'))
    request = {}

    async def provider(**kwargs):
        request.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])

    service.client.chat.completions.create = provider
    service._parse_visual_response = lambda *args: SimpleNamespace(model_dump=lambda **kwargs: {})
    candidate = VisualCandidate('private-row-id', 'private-listing-title', 'https://example.com/private.jpg')
    await service._confirm_unknown_finish(b'target-front', 'image/jpeg',
        (candidate, b'candidate-front', 'image/jpeg'), {'private-binding': 'never-send-to-model'})
    refs = []
    previous = ''
    for block in request['messages'][1]['content']:
        if block['type'] == 'text':
            previous = block['text']
        elif block['type'] == 'image_url':
            ref = re.search(r'\bimage_ref=(\w+)\b', previous)
            content = base64.b64decode(block['image_url']['url'].split(',', 1)[1])
            refs.append((ref[1] if ref else None, content))
    await service.client.close()
    assert refs == [('candidate_front', b'candidate-front'), ('target_front', b'target-front')]
    assert all(secret not in str(request) for secret in
               ('private-row-id', 'private-listing-title', 'private.jpg', 'never-send-to-model'))


def test_pre_observation_prompt_cache_cannot_be_reused_as_current_same_card_evidence():
    identity, sale = fixture()
    raw = observed(identity, sale)
    raw['visual_evidence_binding']['model_observation_contract'] = 'paired-image-only-evidence-v7'
    assert build_visual_match_facts(identity, sale.source_url, sale.title, sale.image_url, raw) is None


@pytest.mark.parametrize(('kind', 'target', 'candidate'), [
    ('card_text', 'Back collector number AB1', 'Back collector number A1'),
    ('card_text', 'English and French printed paragraphs', 'English-only printed paragraph'),
    ('serial_total', '12/99', '7/25'),
    ('fixed_pattern', 'Small square grid on the silver frame', 'Large circular Scope rings on the silver frame'),
])
def test_a_same_summary_cannot_override_concrete_version_or_back_conflicts(kind, target, candidate):
    identity, sale = fixture()
    raw = observed(identity, sale)
    decision = VisualDecision(candidate_id=sale.source_url, verdict='same', confidence='.99',
        design_match='same', finish_match='same', candidate_card_count=1,
        match_evidence=raw['visual_match_evidence'],
        evidence_binding=raw['visual_evidence_binding'],
        conflict_evidence=[dict(kind=kind, target=target, candidate=candidate,
                               source='images', both_visible=True)])
    assert normalize_model_visual_decision(decision).verdict != 'same'
