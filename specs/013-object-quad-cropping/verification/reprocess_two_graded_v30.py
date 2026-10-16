"""Save a verified new revision only while this reported capture is current."""
import asyncio
import hashlib
import json
from pathlib import Path
import runpy
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from card_platform.config import get_settings
from card_platform.flatbed_api import service
from card_platform.flatbed_detection import Detection, DETECTOR_VERSION

ROOT = Path(__file__).parent
OWNER = UUID('c6e6987e-eec0-40a0-a9de-92ce5d51a7cc')
SESSION = UUID('5b0caf99-c50f-4062-881b-d2aac30ebffc')
SOURCE = UUID('916c9c36-5059-4d40-b6e7-f0927aae47fe')


async def main():
    manifest = json.loads(Path('data/flatbed-replay-manifest.json').read_text())
    replay = runpy.run_path('scripts/replay_flatbed_layouts.py')
    async with await AsyncConnection.connect(get_settings().database_url, row_factory=dict_row) as db:
        session = await (await db.execute(
            'SELECT * FROM flatbed_scan_session WHERE id=%s AND owner_id=%s FOR UPDATE',
            (SESSION, OWNER),
        )).fetchone()
        if not session or session['submitted_at']:
            raise RuntimeError('Session submitted; preserve it and do not reprocess')
        sheet = await (await db.execute(
            "SELECT * FROM flatbed_scan_sheet WHERE session_id=%s AND side='front' ORDER BY revision DESC LIMIT 1",
            (SESSION,),
        )).fetchone()
        if not sheet or sheet['id'] != SOURCE or sheet['revision'] != 1:
            raise RuntimeError('New capture or correction exists; preserve it')
        override = await (await db.execute(
            'SELECT id FROM flatbed_layout_override WHERE sheet_id=%s LIMIT 1', (SOURCE,),
        )).fetchone()
        if override:
            raise RuntimeError('Manual correction exists; preserve it')
        manual_pair = await (await db.execute(
            'SELECT id FROM flatbed_pair_proposal WHERE session_id=%s AND manual_override LIMIT 1',
            (SESSION,),
        )).fetchone()
        if manual_pair:
            raise RuntimeError('Manual pairing exists; preserve it')
        content = (service().root / sheet['original_path']).read_bytes()
        assert hashlib.sha256(content).hexdigest() == manifest['source_sha256_by_sheet'][str(SOURCE)]
        out = await service().store_sheet(db, OWNER, SESSION, 'front', content, sheet['content_type'])
        assert out['layout_hypothesis']['detector_version'] == DETECTOR_VERSION == 'object-quad-layout-v30'
        assert len(out['regions']) == 2 and all(region['crop_quality']['usable'] for region in out['regions'])
        boxes = [Detection(
            bounds['x'], bounds['y'], bounds['width'], bounds['height'], bounds['angle'], region['confidence'],
            points=tuple((point['x'], point['y']) for point in bounds['points']),
        ) for region in out['regions'] for bounds in [region['bounds']]]
        errors = replay['_geometry_failures'](
            boxes, 0, [manifest['geometry_rules_by_sheet'][str(SOURCE)]], out['layout_hypothesis'],
            source_crop_points=[region['bounds']['source_crop_points'] for region in out['regions']],
        )
        assert not errors, errors
        stored = await (await db.execute(
            'SELECT ordinal,crop_path,signals FROM flatbed_scan_region WHERE sheet_id=%s ORDER BY ordinal',
            (UUID(str(out['id'])),),
        )).fetchall()
        assert len(stored) == 2 and all(row['crop_path'] and (service().root / row['crop_path']).is_file() for row in stored)
        out['verification'] = {
            'source_sheet_id': str(SOURCE), 'original_source_preserved': True,
            'actual_final_source_crop_points_checked': True, 'gold_geometry_errors': errors,
            'stored_crop_files': [row['crop_path'] for row in stored],
            'auxiliary_vision': [{key: row['signals'].get(key) for key in (
                'orientation_method', 'orientation_failure', 'ocr_failure',
            )} for row in stored],
        }
    # The successful connection exit above commits the new revision.
    (ROOT / 'two-graded-v30-deployed.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print('Stored', out['id'], 'revision', out['revision'], 'two usable crops; actual gold PASS')


asyncio.run(main())
