"""Bind a reviewed mapping to immutable sheet revisions and actual image bytes."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from uuid import UUID


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError) as error:
        raise ValueError('扫描图片已不可用，请重新扫描并确认配对') from error


async def pair_source_snapshot(connection, owner_id: UUID, session_id: UUID, resolve_path: Callable[[str], Path]) -> dict:
    result = await connection.execute(
        """SELECT p.front_region_id,p.back_region_id,
                  f.crop_path front_path,b.crop_path back_path,
                  fs.id front_sheet_id,fs.revision front_revision,fs.sha256 front_source_sha256,
                  fs.original_path front_original_path,
                  bs.id back_sheet_id,bs.revision back_revision,bs.sha256 back_source_sha256,
                  bs.original_path back_original_path,
                  fs.revision=(SELECT max(revision) FROM flatbed_scan_sheet WHERE session_id=%s AND side='front') front_latest,
                  bs.revision=(SELECT max(revision) FROM flatbed_scan_sheet WHERE session_id=%s AND side='back') back_latest
           FROM flatbed_pair_proposal p
           JOIN flatbed_scan_region f ON f.id=p.front_region_id AND f.owner_id=p.owner_id
           JOIN flatbed_scan_region b ON b.id=p.back_region_id AND b.owner_id=p.owner_id
           JOIN flatbed_scan_sheet fs ON fs.id=f.sheet_id AND fs.owner_id=p.owner_id AND fs.session_id=p.session_id AND fs.side='front'
           JOIN flatbed_scan_sheet bs ON bs.id=b.sheet_id AND bs.owner_id=p.owner_id AND bs.session_id=p.session_id AND bs.side='back'
           WHERE p.session_id=%s AND p.owner_id=%s AND p.selected
           ORDER BY p.front_region_id,p.back_region_id""",
        (session_id, session_id, session_id, owner_id),
    )
    rows = await result.fetchall()
    if not rows or any(not row['front_latest'] or not row['back_latest'] for row in rows):
        raise ValueError('扫描版本已改变，请重新生成并确认配对')

    def build() -> dict:
        sheets: dict[str, dict] = {}
        pairs = []
        for row in rows:
            pair = {}
            for side in ('front', 'back'):
                sheet = {'id': str(row[f'{side}_sheet_id']), 'side': side, 'revision': row[f'{side}_revision'], 'sha256': row[f'{side}_source_sha256']}
                if side in sheets and sheets[side] != sheet:
                    raise ValueError('配对包含不同扫描版本，请重新确认')
                if side not in sheets:
                    if _digest(resolve_path(row[f'{side}_original_path'])) != sheet['sha256']:
                        raise ValueError('扫描原图已改变，请重新扫描并确认配对')
                    sheets[side] = sheet
                if not row[f'{side}_path']:
                    raise ValueError('配对缺少裁图，请先校正布局')
                pair[f'{side}_region_id'] = str(row[f'{side}_region_id'])
                pair[f'{side}_sha256'] = _digest(resolve_path(row[f'{side}_path']))
            pairs.append(pair)
        return {'version': 1, 'sheets': [sheets[side] for side in ('front', 'back')], 'pairs': pairs}

    return await asyncio.to_thread(build)


def confirmation_token(snapshot: dict) -> str:
    """A client must submit the exact mapping it confirmed, even across tabs."""
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
