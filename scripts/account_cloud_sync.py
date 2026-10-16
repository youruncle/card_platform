#!/usr/bin/env python3
"""Export/import one owner's card library without accounts or capture projects.

Examples (credentials are read by libpq, never printed):
  python scripts/account_cloud_sync.py export --dsn 'dbname=card_platform' \
      --owner-id UUID --bundle /tmp/private-card-snapshot --uploads apps/api/uploads
  python scripts/account_cloud_sync.py import --dsn 'dbname=card_platform' \
      --owner-id UUID --bundle /tmp/private-card-snapshot --uploads /app/uploads
  # Review that report first, then repeat import with --apply.

Import defaults to a database/filesystem dry run. Plans use temporary typed copies
with the real CHECK/NOT NULL/unique indexes and validate all actual foreign keys.
An apply uses one transaction; cloud-only rows and newer cloud cards are retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from uuid import UUID

import psycopg
from psycopg import sql

FORMAT_VERSION = 2
SCANNER_PATHS = (
    'front_image_path', 'back_image_path', 'front_original_image_path',
    'back_original_image_path', 'front_raw_image_path', 'back_raw_image_path',
)
# Order follows PostgreSQL FKs; card_identity_version's self FK is ordered by version.
TABLES = (
    'brand', 'player', 'card_set', 'card_definition', 'data_source', 'scoring_model',
    'collection_item', 'card_transaction', 'card_identity_version',
    'card_catalog_snapshot', 'card_identity_correction',
    'card_identity_change_audit', 'card_image_orientation_correction',
    'valuation_job', 'valuation_snapshot', 'valuation_shadow_result',
    'card_analysis_job', 'card_analysis_snapshot', 'analysis_request', 'card_score',
    'recommendation', 'identity_research_case', 'identity_research_candidate',
    'cardhobby_purchase_order', 'cardhobby_purchase_unit', 'cardhobby_order_evidence',
    'cardhobby_cost_assignment', 'cardhobby_cost_refresh_history',
    'cardhobby_cost_visual_inference', 'market_identity_cache', 'market_evidence',
    'market_comp', 'market_evidence_discovery', 'market_identity_binding',
    'market_evidence_identity_decision', 'market_identity_rebind_audit',
)
SHARED_MUTABLE = {'brand', 'player', 'card_set', 'card_definition'}
# Append-only evidence/results must never bypass their immutable triggers.
MUTABLE = SHARED_MUTABLE | {
    'collection_item', 'card_catalog_snapshot',
    'card_identity_correction', 'valuation_job', 'card_analysis_job',
    'identity_research_case', 'cardhobby_purchase_order', 'market_identity_binding',
    'market_evidence_identity_decision',
}
DANGEROUS_KEYS = {
    'password', 'password_hash', 'credentials_enc', 'session_enc', 'pending_enc',
    'token_hash', 'api_key', 'authorization', 'cookie', 'set-cookie',
    'access_token', 'refresh_token', 'client_secret',
}
TERMINAL = {
    'scanner_recognition_job': {'succeeded', 'failed'},
    'valuation_job': {'succeeded', 'failed', 'insufficient_evidence'},
    'card_analysis_job': {'succeeded', 'failed'},
    'analysis_request': {'completed', 'failed'},
}
ARCHIVE_BOUNDARY = (
    'Card library only; flatbed projects, scanner source images, workers, accounts, '
    'credentials, sessions, secrets and knowledge workspaces are excluded. '
    'Scanner records are excluded; existing cloud capture records remain untouched. '
    'New cards do not include scanning history or scanner-derived confidence/status. '
    'Unavailable local evidence references are marked local-only://.'
)


def owner_uuid(value):
    return str(UUID(str(value)))


def dumps(value):
    # Preserve exact NUMERIC amounts and numeric JSON evidence without float conversion.
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('non-finite number is forbidden')
        return str(value)
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k, ensure_ascii=False) + ':' + dumps(v)
                              for k, v in value.items()) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(dumps(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _unsupported(value):
    raise TypeError(type(value).__name__)


def sha256_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def private_file(path, mode='wb'):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, mode)


def selection_rules(owner, prefix='public.'):
    """The same explicit relational closure is checked on source and imported rows."""
    owner = owner_uuid(owner)
    def t(name):
        return prefix + name
    cards = f"SELECT id FROM {t('collection_item')} WHERE owner_id='{owner}'"
    definitions = f"SELECT card_definition_id FROM {t('collection_item')} WHERE id IN({cards})"
    sets = f"SELECT card_set_id FROM {t('card_definition')} WHERE id IN({definitions})"
    assignments = f"SELECT unit_id FROM {t('cardhobby_cost_assignment')} WHERE collection_item_id IN({cards})"
    units = f"SELECT id FROM {t('cardhobby_purchase_unit')} WHERE id IN({assignments})"
    orders = f"SELECT order_id FROM {t('cardhobby_purchase_unit')} WHERE id IN({units})"
    fingerprints = f"SELECT identity_fingerprint FROM {t('market_identity_binding')} WHERE collection_item_id IN({cards})"
    comps = f"SELECT market_evidence_id FROM {t('market_comp')} WHERE card_definition_id IN({definitions})"
    decisions = f"SELECT market_evidence_id FROM {t('market_evidence_identity_decision')} WHERE collection_item_id IN({cards})"
    evidence = (f"SELECT id FROM {t('market_evidence')} WHERE id IN({comps}) "
                f"OR id IN({decisions}) OR identity_fingerprint IN({fingerprints})")
    evidence_fingerprints = f"SELECT identity_fingerprint FROM {t('market_evidence')} WHERE id IN({evidence})"
    rules = {name: f'collection_item_id IN({cards})' for name in TABLES}
    rules.update({
        'collection_item': f"owner_id='{owner}'",
        'card_definition': f'id IN({definitions})',
        'card_set': f'id IN({sets})',
        'brand': f"id IN(SELECT brand_id FROM {t('card_set')} WHERE id IN({sets}))",
        'player': f"id IN(SELECT player_id FROM {t('card_definition')} WHERE id IN({definitions}))",
        'cardhobby_purchase_unit': f'id IN({units})',
        'cardhobby_purchase_order': f'id IN({orders})',
        'cardhobby_order_evidence': f'order_id IN({orders})',
        'recommendation': f"analysis_request_id IN(SELECT id FROM {t('analysis_request')} WHERE collection_item_id IN({cards}))",
        'scoring_model': f"id IN(SELECT scoring_model_id FROM {t('card_score')} WHERE collection_item_id IN({cards}))",
        'identity_research_candidate': f"research_case_id IN(SELECT id FROM {t('identity_research_case')} WHERE collection_item_id IN({cards}))",
        'market_comp': f'card_definition_id IN({definitions})',
        'market_evidence': f'id IN({evidence})',
        'market_identity_cache': f'identity_fingerprint IN({fingerprints}) OR identity_fingerprint IN({evidence_fingerprints})',
        'market_evidence_discovery': f'identity_fingerprint IN({fingerprints}) AND market_evidence_id IN({evidence})',
        'data_source': (f"id IN(SELECT data_source_id FROM {t('market_comp')} WHERE card_definition_id IN({definitions})) "
                        f"OR code IN(SELECT provider_code FROM {t('market_evidence')} WHERE id IN({evidence}))"),
    })
    return rules


def metadata(connection):
    columns = {}
    keys = {}
    jsonb_columns = {}
    for name in TABLES:
        attributes = connection.execute(
            "SELECT attname,atttypid='jsonb'::regtype,attnotnull FROM pg_attribute "
            "WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum",
            ('public.' + name,)).fetchall()
        columns[name] = [r[0] for r in attributes]
        jsonb_columns[name] = {r[0]: r[2] for r in attributes if r[1]}
        keys[name] = [r[0] for r in connection.execute(
            "SELECT a.attname FROM pg_index i JOIN unnest(i.indkey) WITH ORDINALITY k(n,o) "
            "ON true JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.n "
            "WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.o", ('public.' + name,))]
        if not keys[name]:
            raise ValueError(f'missing primary key: {name}')
    return columns, keys, jsonb_columns


def inspect_value(value, *, upload_root=None, image_names=None, counts=None):
    if isinstance(value, dict):
        for key in value:
            if key.lower() in DANGEROUS_KEYS:
                raise ValueError('credential/session/secret field is forbidden')
        if upload_root is None:
            for child in value.values():
                inspect_value(child, image_names=image_names, counts=counts)
            return value
        return {k: inspect_value(v, upload_root=upload_root, image_names=image_names,
                                 counts=counts) for k, v in value.items()}
    if isinstance(value, list):
        if upload_root is None:
            for child in value:
                inspect_value(child, image_names=image_names, counts=counts)
            return value
        return [inspect_value(v, upload_root=upload_root, image_names=image_names,
                              counts=counts) for v in value]
    if not isinstance(value, str):
        return value
    if value.startswith('/uploads/'):
        name = value[len('/uploads/'):]
        check_image_name(name)
        if image_names is not None:
            image_names.add(name)
    if upload_root and value.startswith(str(upload_root) + '/'):
        name = value[len(str(upload_root)) + 1:]
        check_image_name(name)
        image_names.add(name)
        counts['rewritten_upload_paths'] += 1
        return '/app/uploads/' + name
    if upload_root and (value.startswith('/Users/') or value.startswith('http://localhost')
                        or value.startswith('http://127.0.0.1')):
        counts['unavailable_local_references'] += 1
        return 'local-only://' + value.rsplit('/', 1)[-1]
    return value


def check_image_name(name):
    if (not isinstance(name, str) or not name or name in {'.', '..'}
            or any(x in name for x in ('/', '\\', '\x00'))
            or Path(name).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}):
        raise ValueError('unsafe image filename')


def export_bundle(connection, owner_id, destination, upload_root, *, existing_image_manifest=None):
    owner_id = owner_uuid(owner_id)
    destination, upload_root = Path(destination), Path(upload_root).resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError('bundle destination must not exist')
    tables, image_names = {}, set()
    counts = {'rewritten_upload_paths': 0, 'unavailable_local_references': 0}
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        if not connection.execute('SELECT 1 FROM public.app_user WHERE id=%s', (owner_id,)).fetchone():
            raise ValueError('source owner does not exist')
        columns, _, jsonb_columns = metadata(connection)
        rules = selection_rules(owner_id)
        for name in TABLES:
            order = 'version' if name == 'card_identity_version' else ','.join(columns[name][:1])
            rows = []
            null_flags = sql.SQL('jsonb_build_object({})').format(sql.SQL(',').join(
                sql.SQL('{},t.{} IS NULL').format(sql.Literal(k), sql.Identifier(k))
                for k in jsonb_columns[name])) if jsonb_columns[name] else sql.SQL("'{}'::jsonb")
            query = sql.SQL('SELECT to_jsonb(t)::text,{} FROM public.{} t WHERE {} ORDER BY {}').format(
                null_flags, sql.Identifier(name), sql.SQL(rules[name]), sql.SQL(order))
            for raw, sql_nulls in connection.execute(query):
                row = json.loads(raw, parse_float=Decimal)
                if name in TERMINAL and row['status'] not in TERMINAL[name]:
                    raise ValueError(f'non-terminal job excluded; retry after completion: {name}')
                if name == 'scanner_recognition_job':
                    for column in SCANNER_PATHS:
                        row[column] = None
                row = inspect_value(row, upload_root=upload_root, image_names=image_names, counts=counts)
                # In format 2, an absent JSONB column means SQL NULL; a present
                # JSON null means the JSONB literal null. to_jsonb(row) alone
                # cannot tell these apart, so capture SQL nullness separately.
                for column, is_sql_null in sql_nulls.items():
                    if is_sql_null:
                        del row[column]
                rows.append(row)
            tables[name] = rows
        source_counter = connection.execute(
            'SELECT next_display_id FROM public.collection_item_display_counter WHERE owner_id=%s',
            (owner_id,)).fetchone()
    files = []
    for name in sorted(image_names):
        path = upload_root / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != upload_root:
            raise ValueError('missing/unsafe referenced upload image')
        files.append({'name': name, 'size': path.stat().st_size, 'sha256': sha256_file(path)})
    if isinstance(existing_image_manifest, (str, Path)):
        existing_image_manifest = json.loads(Path(existing_image_manifest).read_text())
    existing = (existing_image_manifest or {}).get('files', {})
    if not isinstance(existing, dict):
        raise ValueError('existing image manifest must contain a filename map')
    packed = [f for f in files if existing.get(f['name']) != {
        'size': f['size'], 'sha256': f['sha256']}]
    destination.mkdir(mode=0o700, parents=False)
    try:
        with private_file(destination / 'dataset.json.gz') as stream:
            with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as compressed:
                compressed.write(dumps({'owner_id': owner_id, 'tables': tables}).encode())
        with private_file(destination / 'images.tar') as stream:
            with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for entry in packed:
                    path = upload_root / entry['name']
                    # Verify again so image replacement during the DB snapshot cannot corrupt the bundle.
                    with path.open('rb') as image:
                        if hashlib.file_digest(image, 'sha256').hexdigest() != entry['sha256']:
                            raise ValueError('image changed while exporting')
                        image.seek(0)
                        info = tarfile.TarInfo(entry['name'])
                        info.size, info.mode = entry['size'], 0o600
                        archive.addfile(info, image)
        manifest = {
            'format_version': FORMAT_VERSION, 'owner_id': owner_id,
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'archive_boundary': ARCHIVE_BOUNDARY,
            'source_display_counter': source_counter[0] if source_counter else 0,
            'tables': {name: len(rows) for name, rows in tables.items()},
            'files': files, 'file_bytes': sum(f['size'] for f in files), **counts,
            'packed_files': [f['name'] for f in packed],
            'packed_file_bytes': sum(f['size'] for f in packed),
            'data_sha256': sha256_file(destination / 'dataset.json.gz'),
            'images_sha256': sha256_file(destination / 'images.tar'),
        }
        with private_file(destination / 'manifest.json', 'w') as stream:
            stream.write(dumps(manifest))
        return {**{k: v for k, v in manifest.items() if k not in {'files', 'packed_files'}},
                'file_count': len(files), 'packed_file_count': len(packed)}
    except BaseException:
        shutil.rmtree(destination)
        raise


def load_bundle(bundle, owner_id):
    bundle = Path(bundle)
    if bundle.is_symlink() or not bundle.is_dir() or bundle.stat().st_mode & 0o077:
        raise ValueError('bundle directory permissions must be 700')
    for name in ('manifest.json', 'dataset.json.gz', 'images.tar'):
        path = bundle / name
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError('bundle file permissions must be 600; symlinks forbidden')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if manifest.get('format_version') != FORMAT_VERSION:
        raise ValueError('unsupported bundle version')
    if manifest.get('owner_id') != owner_id:
        raise ValueError('bundle owner mismatch')
    for name, key in [('dataset.json.gz', 'data_sha256'), ('images.tar', 'images_sha256')]:
        if sha256_file(bundle / name) != manifest.get(key):
            raise ValueError('bundle checksum/hash mismatch')
    with gzip.open(bundle / 'dataset.json.gz', 'rt') as stream:
        payload = json.load(stream, parse_float=Decimal)
    if payload.get('owner_id') != owner_id:
        raise ValueError('dataset owner mismatch')
    tables = payload.get('tables')
    if not isinstance(tables, dict) or set(tables) != set(TABLES):
        raise ValueError('forbidden or missing table in bundle')
    for name, rows in tables.items():
        if not isinstance(rows, list) or manifest['tables'].get(name) != len(rows):
            raise ValueError('table manifest count mismatch')
        inspect_value(rows)
    files = manifest.get('files', [])
    expected = {}
    for entry in files:
        check_image_name(entry['name'])
        if entry['name'] in expected:
            raise ValueError('duplicate image filename')
        expected[entry['name']] = entry
    packed = manifest.get('packed_files', list(expected))
    if not isinstance(packed, list) or len(packed) != len(set(packed)) or not set(packed) <= set(expected):
        raise ValueError('invalid packed image manifest')
    with tarfile.open(bundle / 'images.tar', 'r:') as archive:
        seen = set()
        for member in archive:
            check_image_name(member.name)
            if not member.isfile() or member.name in seen or member.name not in packed:
                raise ValueError('unsafe/unexpected image archive member')
            entry = expected[member.name]
            stream = archive.extractfile(member)
            if member.size != entry['size'] or hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                raise ValueError('image checksum/hash mismatch')
            seen.add(member.name)
        if seen != set(packed):
            raise ValueError('missing image archive member')
    referenced = set()
    inspect_value(tables, image_names=referenced)
    # Absolute /app/uploads paths occur in archived analysis inputs too.
    def extra_paths(value):
        if isinstance(value, dict):
            for v in value.values():
                extra_paths(v)
        elif isinstance(value, list):
            for v in value:
                extra_paths(v)
        elif isinstance(value, str) and value.startswith('/app/uploads/'):
            name = value[len('/app/uploads/'):]
            check_image_name(name)
            referenced.add(name)
    extra_paths(tables)
    if referenced != set(expected):
        raise ValueError('referenced images do not match file manifest')
    return manifest, tables


def relation(prefix, name):
    return sql.Identifier(prefix, name)


def join_keys(keys, left='t', right='s'):
    return sql.SQL(' AND ').join(sql.SQL('{}.{}={}.{}').format(
        sql.Identifier(left), sql.Identifier(k), sql.Identifier(right), sql.Identifier(k))
        for k in keys)


def validate_ownership(connection, owner_id, columns, keys):
    cards = 'SELECT id FROM pg_temp.sync_in_collection_item'
    for name in TABLES:
        source = relation('pg_temp', 'sync_in_' + name)
        if 'owner_id' in columns[name]:
            if connection.execute(sql.SQL('SELECT 1 FROM {} WHERE owner_id<>%s LIMIT 1').format(source), (owner_id,)).fetchone():
                raise ValueError(f'foreign owner in {name}')
        if 'collection_item_id' in columns[name]:
            if connection.execute(sql.SQL(f'SELECT 1 FROM {{}} WHERE collection_item_id IS NOT NULL '
                                          f'AND collection_item_id NOT IN({cards}) LIMIT 1').format(source)).fetchone():
                raise ValueError(f'foreign collection item in {name}')
        for actor in ('deleted_by', 'confirmed_by', 'actor_id', 'applied_by'):
            if actor in columns[name] and connection.execute(sql.SQL(
                    'SELECT 1 FROM {} WHERE {} IS NOT NULL AND {}<>%s LIMIT 1').format(
                        source, sql.Identifier(actor), sql.Identifier(actor)), (owner_id,)).fetchone():
                raise ValueError(f'foreign owner actor in {name}')
        if 'owner_id' in columns[name] or 'collection_item_id' in columns[name]:
            condition = (sql.SQL('t.owner_id<>%s') if 'owner_id' in columns[name] else
                         sql.SQL('EXISTS(SELECT 1 FROM public.collection_item ci '
                                 'WHERE ci.id=t.collection_item_id AND ci.owner_id<>%s)'))
            if connection.execute(sql.SQL('SELECT 1 FROM public.{} t JOIN {} s ON {} '
                                           'WHERE {} LIMIT 1').format(sql.Identifier(name), source,
                                            join_keys(keys[name]), condition), (owner_id,)).fetchone():
                raise ValueError(f'cloud ownership collision: {name}')
    rules = selection_rules(owner_id, 'pg_temp.sync_in_')
    for name, rule in rules.items():
        if connection.execute(f'SELECT 1 FROM pg_temp.sync_in_{name} WHERE NOT({rule}) LIMIT 1').fetchone():
            raise ValueError(f'row outside account FK closure: {name}')
    # A private parent must be in this package, rather than merely existing in
    # the cloud's complete table. PostgreSQL FKs alone do not enforce ownership.
    private = {name for name in TABLES if 'owner_id' in columns[name]
               or 'collection_item_id' in columns[name]} | {
                   'identity_research_candidate', 'recommendation', 'cardhobby_order_evidence'}
    for name in TABLES:
        for target, source_cols, target_cols in foreign_keys(connection, name):
            if target not in private:
                continue
            not_null = sql.SQL(' AND ').join(sql.SQL('s.{} IS NOT NULL').format(
                sql.Identifier(x)) for x in source_cols)
            matches = sql.SQL(' AND ').join(sql.SQL('s.{}=r.{}').format(
                sql.Identifier(a), sql.Identifier(b)) for a, b in zip(source_cols, target_cols))
            if 'collection_item_id' in columns[name] and 'collection_item_id' in columns[target]:
                matches += sql.SQL(' AND s.collection_item_id IS NOT DISTINCT FROM r.collection_item_id')
            if connection.execute(sql.SQL('SELECT 1 FROM {} s WHERE {} AND NOT EXISTS '
                                           '(SELECT 1 FROM {} r WHERE {}) LIMIT 1').format(
                                               relation('pg_temp', 'sync_in_' + name), not_null,
                                               relation('pg_temp', 'sync_in_' + target), matches)).fetchone():
                raise ValueError(f'private same-card FK outside account package: {name} -> {target}')
    if connection.execute(
            'SELECT 1 FROM pg_temp.sync_in_recommendation r '
            'JOIN pg_temp.sync_in_analysis_request a ON a.id=r.analysis_request_id '
            'JOIN pg_temp.sync_in_card_score s ON s.id=r.card_score_id '
            'WHERE a.collection_item_id IS DISTINCT FROM s.collection_item_id LIMIT 1').fetchone():
        raise ValueError('private same-card FK mismatch: recommendation score/request')


def shared_protection(name, owner_id):
    """Protect other owners and whole-card conflicts, including ancestors of definitions."""
    protected = (f"(ci.owner_id<>'{owner_id}' OR ci.id IN(SELECT id FROM pg_temp.sync_blocked_cards))")
    if name == 'card_definition':
        return f'EXISTS(SELECT 1 FROM public.collection_item ci WHERE ci.card_definition_id=t.id AND {protected})'
    if name == 'player':
        return f'EXISTS(SELECT 1 FROM public.collection_item ci JOIN public.card_definition cd ON cd.id=ci.card_definition_id WHERE cd.player_id=t.id AND {protected})'
    if name == 'card_set':
        return f'EXISTS(SELECT 1 FROM public.collection_item ci JOIN public.card_definition cd ON cd.id=ci.card_definition_id WHERE cd.card_set_id=t.id AND {protected})'
    return f'EXISTS(SELECT 1 FROM public.collection_item ci JOIN public.card_definition cd ON cd.id=ci.card_definition_id JOIN public.card_set cs ON cs.id=cd.card_set_id WHERE cs.brand_id=t.id AND {protected})'


def foreign_keys(connection, name):
    constraints = connection.execute(
            "SELECT c.confrelid::regclass::text, "
            "ARRAY(SELECT attname FROM unnest(c.conkey) WITH ORDINALITY k(n,o) "
            "JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.o), "
            "ARRAY(SELECT attname FROM unnest(c.confkey) WITH ORDINALITY k(n,o) "
            "JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.n ORDER BY k.o) "
            "FROM pg_constraint c WHERE c.contype='f' AND c.conrelid=%s::regclass",
            ('public.' + name,)).fetchall()
    return [(target.removeprefix('public.'), source_cols, target_cols)
            for target, source_cols, target_cols in constraints]


def validate_foreign_keys(connection):
    for name in TABLES:
        constraints = foreign_keys(connection, name)
        for target, source_cols, target_cols in constraints:
            target = target.removeprefix('public.')
            target_relation = relation('pg_temp', 'sync_final_' + target) if target in TABLES else relation('public', target)
            not_null = sql.SQL(' AND ').join(sql.SQL('s.{} IS NOT NULL').format(sql.Identifier(x)) for x in source_cols)
            matches = sql.SQL(' AND ').join(sql.SQL('s.{}=r.{}').format(sql.Identifier(a), sql.Identifier(b)) for a, b in zip(source_cols, target_cols))
            if connection.execute(sql.SQL('SELECT 1 FROM {} s WHERE {} AND NOT EXISTS '
                                           '(SELECT 1 FROM {} r WHERE {}) LIMIT 1').format(
                                               relation('pg_temp', 'sync_final_' + name), not_null,
                                               target_relation, matches)).fetchone():
                raise ValueError(f'missing foreign key closure: {name} -> {target}')


def reconcile_open_correction_keys(connection):
    """Reuse a surviving cloud open suggestion, preserving every closed history row.

    When the source explicitly supersedes the old cloud row, its newer update
    will free the partial unique key before the replacement is inserted. Only
    genuinely competing open records need a cloud-PK alias. The normal merge's
    updated_at policy then chooses the latest content, keeping equal-time cloud
    edits. No persistent row is removed.
    """
    collisions = connection.execute(
        'SELECT s.id,t.id,COALESCE(h.status<>\'open\' AND h.updated_at>t.updated_at,false),'
        'COALESCE(p.updated_at>=s.updated_at,false) '
        'FROM pg_temp.sync_in_card_identity_correction s '
        'JOIN pg_temp.sync_final_card_identity_correction t '
        'ON t.collection_item_id=s.collection_item_id AND t.field_name=s.field_name '
        "AND t.status='open' AND t.id<>s.id "
        'LEFT JOIN pg_temp.sync_in_card_identity_correction h ON h.id=t.id '
        'LEFT JOIN pg_temp.sync_final_card_identity_correction p ON p.id=s.id '
        "WHERE s.status='open' AND s.collection_item_id NOT IN("
        'SELECT id FROM pg_temp.sync_blocked_cards)').fetchall()
    aliases = 0
    if any(not closes and not newer_pk for _, _, closes, newer_pk in collisions) and connection.execute(
            "SELECT 1 FROM pg_constraint WHERE contype='f' "
            "AND confrelid='public.card_identity_correction'::regclass LIMIT 1").fetchone():
        raise ValueError('cannot alias correction IDs with inbound foreign keys')
    for source_id, cloud_id, closes, newer_pk in collisions:
        # Resolve same-ID cloud decisions before considering an alias. A newer
        # dismissal must not be bypassed by remapping the old open snapshot.
        if closes or newer_pk:
            continue
        # A stale source version of the surviving cloud PK may also be in the
        # package. It cannot update the newer cloud row; replace only that
        # temporary input with the competing open version, retaining the PK.
        connection.execute('DELETE FROM pg_temp.sync_in_card_identity_correction WHERE id=%s', (cloud_id,))
        connection.execute('UPDATE pg_temp.sync_in_card_identity_correction SET id=%s WHERE id=%s',
                           (cloud_id, source_id))
        aliases += 1
    return aliases


def make_plan(connection, owner_id, tables):
    columns, keys, jsonb_columns = metadata(connection)
    if not connection.execute('SELECT 1 FROM public.app_user WHERE id=%s', (owner_id,)).fetchone():
        raise ValueError('target owner does not exist')
    for name in TABLES:
        for kind in ('in', 'final'):
            target = relation('pg_temp', f'sync_{kind}_' + name)
            connection.execute(sql.SQL('CREATE TEMP TABLE {} (LIKE public.{} INCLUDING DEFAULTS '
                                       'INCLUDING CONSTRAINTS INCLUDING INDEXES) ON COMMIT DROP').format(
                                           target, sql.Identifier(name)))
        rows = tables[name]
        for row in rows:
            optional = {k for k, not_null in jsonb_columns[name].items() if not not_null}
            if (not isinstance(row, dict) or not set(row) <= set(columns[name])
                    or not (set(columns[name]) - set(row)) <= optional):
                raise ValueError(f'unknown/missing columns: {name}')
            if name in TERMINAL and row['status'] not in TERMINAL[name]:
                raise ValueError(f'pending/active job is forbidden: {name}')
            if name == 'scanner_recognition_job' and any(row[p] is not None for p in SCANNER_PATHS):
                raise ValueError('scanner source paths must be archived as NULL')
        if rows:
            # Direct -> extraction preserves JSONB literal null before NOT NULL
            # constraints run; absent optional keys produce genuine SQL NULL.
            projection = sql.SQL(',').join(
                sql.SQL('j.raw->{}').format(sql.Literal(k)) if k in jsonb_columns[name]
                else sql.SQL('p.{}').format(sql.Identifier(k)) for k in columns[name])
            connection.execute(sql.SQL('INSERT INTO {} SELECT {} FROM jsonb_array_elements(%s::jsonb) '
                                       'j(raw) CROSS JOIN LATERAL jsonb_populate_record('
                                       'NULL::public.{},j.raw) p').format(
                                           relation('pg_temp', 'sync_in_' + name), projection, sql.Identifier(name)),
                               (dumps(rows),))
        connection.execute(sql.SQL('INSERT INTO {} SELECT * FROM public.{}').format(
            relation('pg_temp', 'sync_final_' + name), sql.Identifier(name)))
    validate_ownership(connection, owner_id, columns, keys)
    connection.execute('CREATE TEMP TABLE sync_blocked_cards(id uuid PRIMARY KEY) ON COMMIT DROP')
    connection.execute('INSERT INTO sync_blocked_cards SELECT s.id FROM pg_temp.sync_in_collection_item s '
                       'JOIN public.collection_item t USING(id) WHERE t.updated_at>s.updated_at')
    correction_aliases = reconcile_open_correction_keys(connection)
    report = {'apply': False, 'tables': {}, 'archive_boundary': ARCHIVE_BOUNDARY,
              'natural_key_aliases': {'card_identity_correction': correction_aliases}}
    for name in TABLES:
        source = relation('pg_temp', 'sync_in_' + name)
        final = relation('pg_temp', 'sync_final_' + name)
        join = join_keys(keys[name])
        eligible = sql.SQL('true')
        if name == 'collection_item':
            eligible = sql.SQL('s.id NOT IN(SELECT id FROM pg_temp.sync_blocked_cards)')
        elif 'collection_item_id' in columns[name]:
            eligible = sql.SQL('s.collection_item_id IS NULL OR s.collection_item_id NOT IN(SELECT id FROM pg_temp.sync_blocked_cards)')
        elif name == 'recommendation':
            eligible = sql.SQL('EXISTS(SELECT 1 FROM pg_temp.sync_in_analysis_request p '
                               'WHERE p.id=s.analysis_request_id AND p.collection_item_id '
                               'NOT IN(SELECT id FROM pg_temp.sync_blocked_cards))')
        elif name == 'identity_research_candidate':
            eligible = sql.SQL('EXISTS(SELECT 1 FROM pg_temp.sync_in_identity_research_case p '
                               'WHERE p.id=s.research_case_id AND p.collection_item_id '
                               'NOT IN(SELECT id FROM pg_temp.sync_blocked_cards))')
        changed = sql.SQL('to_jsonb(t) IS DISTINCT FROM to_jsonb(s)')
        stats = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'skipped_newer': 0,
                 'skipped_shared': 0, 'skipped_immutable': 0, 'skipped_card_conflict': 0}
        if name == 'market_evidence_identity_decision':
            stats['skipped_inactive_identity'] = 0
        stats['skipped_card_conflict'] = connection.execute(sql.SQL(
            'SELECT count(*) FROM {} s WHERE NOT({})').format(source, eligible)).fetchone()[0]
        stats['unchanged'] = connection.execute(sql.SQL(
            'SELECT count(*) FROM {} s JOIN {} t ON {} WHERE ({}) AND NOT({})').format(
                source, final, join, eligible, changed)).fetchone()[0]
        allowed = sql.SQL('false')
        if name in MUTABLE:
            if name == 'market_evidence_identity_decision':
                # This owner-bound review table has no updated_at. The requested
                # local snapshot is authoritative only for the final current,
                # unrevoked confirmed identity. Whole-card cloud conflicts are
                # already excluded by eligible; older reviews remain history.
                allowed = sql.SQL(
                    'COALESCE(s.identity_version_id=(SELECT iv.id '
                    'FROM pg_temp.sync_final_card_identity_version iv '
                    "WHERE iv.collection_item_id=s.collection_item_id AND iv.review_status='confirmed' "
                    'AND NOT EXISTS(SELECT 1 FROM pg_temp.sync_final_card_identity_version revoked '
                    "WHERE revoked.supersedes_id=iv.id AND revoked.review_status='rejected') "
                    'ORDER BY iv.version DESC LIMIT 1),false)')
            elif 'updated_at' in columns[name]:
                allowed = sql.SQL('s.updated_at>t.updated_at')
            elif name in SHARED_MUTABLE:
                allowed = sql.SQL('true')
        protected = sql.SQL(shared_protection(name, owner_id)) if name in SHARED_MUTABLE else sql.SQL('false')
        stats['skipped_shared'] = connection.execute(sql.SQL(
            'SELECT count(*) FROM {} s JOIN {} t ON {} WHERE ({}) AND ({}) AND ({})').format(
                source, final, join, eligible, changed, protected)).fetchone()[0]
        skip_key = 'skipped_newer' if name in MUTABLE else 'skipped_immutable'
        if name == 'market_evidence_identity_decision':
            skip_key = 'skipped_inactive_identity'
        stats[skip_key] = connection.execute(sql.SQL(
            'SELECT count(*) FROM {} s JOIN {} t ON {} WHERE ({}) AND ({}) AND NOT({}) AND NOT({})').format(
                source, final, join, eligible, changed, protected, allowed)).fetchone()[0]
        if name == 'collection_item':
            stats['skipped_newer'] += stats['skipped_card_conflict']
        # Close old open suggestions before inserting their replacement, so the
        # partial (item, field) unique key is released in both plan and apply.
        if name in MUTABLE:
            updates = sql.SQL(',').join(sql.SQL('{}=s.{}').format(sql.Identifier(k), sql.Identifier(k))
                                       for k in columns[name] if k not in keys[name])
            stats['updated'] = connection.execute(sql.SQL(
                'UPDATE {} t SET {} FROM {} s WHERE {} AND ({}) AND ({}) AND ({}) AND NOT({})').format(
                    final, updates, source, join, eligible, changed, allowed, protected)).rowcount
        order = sql.SQL(' ORDER BY s.version') if name == 'card_identity_version' else sql.SQL('')
        stats['inserted'] = connection.execute(sql.SQL(
            'INSERT INTO {} SELECT s.* FROM {} s WHERE ({}) AND NOT EXISTS('
            'SELECT 1 FROM {} t WHERE {}){}').format(final, source, eligible, final, join, order)).rowcount
        report['tables'][name] = stats
    validate_foreign_keys(connection)
    report['cloud_only_cards_retained'] = connection.execute(
        'SELECT count(*) FROM public.collection_item ci WHERE owner_id=%s AND NOT EXISTS('
        'SELECT 1 FROM pg_temp.sync_in_collection_item s WHERE s.id=ci.id)', (owner_id,)).fetchone()[0]
    return report, columns, keys


def check_destination_images(upload_root, manifest):
    root = Path(upload_root)
    if root.is_symlink():
        raise ValueError('upload root must not be a symlink')
    for entry in manifest['files']:
        path = root / entry['name']
        if path.is_symlink() or (path.exists() and (not path.is_file() or sha256_file(path) != entry['sha256'])):
            raise ValueError('existing cloud image filename has different content')
        if not path.exists() and entry['name'] not in manifest.get('packed_files', []):
            raise ValueError('missing cloud image omitted from delta bundle')


def install_images(bundle, upload_root, manifest):
    root = Path(upload_root)
    if not root.exists():
        root.mkdir(mode=0o700, parents=True)
    check_destination_images(root, manifest)
    installed = []
    try:
        with tarfile.open(Path(bundle) / 'images.tar', 'r:') as archive:
            for member in archive:
                destination = root / member.name
                if destination.exists():
                    continue
                fd, temporary = tempfile.mkstemp(prefix='.account-sync-', dir=root)
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        shutil.copyfileobj(archive.extractfile(member), stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, destination)
                    installed.append(destination)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        return installed
    except BaseException:
        for path in installed:
            path.unlink(missing_ok=True)
        raise


def import_bundle(connection, owner_id, bundle, upload_root, *, apply=False):
    owner_id = owner_uuid(owner_id)
    manifest, tables = load_bundle(bundle, owner_id)
    check_destination_images(upload_root, manifest)
    installed = []
    try:
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            connection.execute("SET LOCAL TIME ZONE 'UTC'")
            connection.execute("SET LOCAL lock_timeout='15s'")
            if apply:
                connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('account-cloud-sync:' + owner_id,))
                # Prevent source ownership/unique-key races while planning and applying.
                connection.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(
                    sql.SQL(',').join(relation('public', t) for t in TABLES)))
            report, columns, keys = make_plan(connection, owner_id, tables)
            report['files'] = len(manifest['files'])
            report['file_bytes'] = manifest['file_bytes']
            if apply:
                installed = install_images(bundle, upload_root, manifest)
                # Active-item guards deliberately reject new historical children of
                # deleted cards. Keep only eligible own cards active inside this
                # uncommitted transaction, then restore the exact final tombstones.
                connection.execute('CREATE TEMP TABLE sync_changed_cards ON COMMIT DROP AS '
                                   'SELECT s.id FROM pg_temp.sync_final_collection_item s '
                                   'JOIN public.collection_item t USING(id) '
                                   'WHERE to_jsonb(t) IS DISTINCT FROM to_jsonb(s)')
                connection.execute('UPDATE public.collection_item SET deleted_at=NULL '
                                   'WHERE owner_id=%s AND deleted_at IS NOT NULL '
                                   'AND id IN(SELECT id FROM pg_temp.sync_in_collection_item) '
                                   'AND id NOT IN(SELECT id FROM pg_temp.sync_blocked_cards)', (owner_id,))
                for name in TABLES:
                    final = relation('pg_temp', 'sync_final_' + name)
                    source = relation('pg_temp', 'sync_in_' + name)
                    target = relation('public', name)
                    join = join_keys(keys[name])
                    order = sql.SQL(' ORDER BY s.version') if name == 'card_identity_version' else sql.SQL('')
                    projection = sql.SQL('s.*')
                    if name == 'collection_item':
                        projection = sql.SQL(',').join(sql.SQL('NULL') if k == 'deleted_at' else
                                                       sql.SQL('s.{}').format(sql.Identifier(k))
                                                       for k in columns[name])
                    updated = 0
                    if name in MUTABLE:
                        updates = sql.SQL(',').join(sql.SQL('{}=s.{}').format(sql.Identifier(k), sql.Identifier(k))
                                                  for k in columns[name] if k not in keys[name])
                        condition = sql.SQL('to_jsonb(t) IS DISTINCT FROM to_jsonb(s)')
                        if name == 'collection_item':
                            updates = sql.SQL(',').join(sql.SQL('deleted_at=NULL') if k == 'deleted_at' else
                                                       sql.SQL('{}=s.{}').format(sql.Identifier(k), sql.Identifier(k))
                                                       for k in columns[name] if k not in keys[name])
                            condition = sql.SQL('s.id IN(SELECT id FROM pg_temp.sync_changed_cards)')
                        updated = connection.execute(sql.SQL(
                            'UPDATE {} t SET {} FROM {} s WHERE {} AND {}').format(
                                target, updates, final, join, condition)).rowcount
                    inserted = connection.execute(sql.SQL(
                        'INSERT INTO {} SELECT {} FROM {} s WHERE EXISTS(SELECT 1 FROM {} t WHERE {}) '
                        'AND NOT EXISTS(SELECT 1 FROM {} t WHERE {}){}').format(
                            target, projection, final, source, join, target, join, order)).rowcount
                    if (inserted, updated) != (report['tables'][name]['inserted'], report['tables'][name]['updated']):
                        raise ValueError(f'plan/apply row count mismatch: {name}')
                connection.execute('UPDATE public.collection_item t SET deleted_at=s.deleted_at '
                                   'FROM pg_temp.sync_final_collection_item s WHERE t.id=s.id '
                                   'AND t.owner_id=%s AND t.id IN(SELECT id FROM pg_temp.sync_in_collection_item) '
                                   'AND t.id NOT IN(SELECT id FROM pg_temp.sync_blocked_cards)', (owner_id,))
                next_id = max(int(manifest.get('source_display_counter', 0)), connection.execute(
                    'SELECT COALESCE(max(display_id)+1,0) FROM public.collection_item WHERE owner_id=%s',
                    (owner_id,)).fetchone()[0])
                connection.execute('INSERT INTO public.collection_item_display_counter(owner_id,next_display_id) '
                                   'VALUES(%s,%s) ON CONFLICT(owner_id) DO UPDATE SET next_display_id='
                                   'GREATEST(collection_item_display_counter.next_display_id,EXCLUDED.next_display_id)',
                                   (owner_id, next_id))
                report['apply'] = True
                report['installed_new_files'] = len(installed)
            return report
    except BaseException:
        for path in installed:
            path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('operation', choices=['export', 'import'])
    parser.add_argument('--dsn', default='', help='libpq DSN; alternatively use PG* environment variables')
    parser.add_argument('--owner-id', required=True)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--uploads', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='explicitly apply an import; default is dry run')
    parser.add_argument('--existing-image-manifest', type=Path,
                        help='export only image content absent from this trusted cloud filename/hash map')
    args = parser.parse_args()
    try:
        with psycopg.connect(args.dsn, autocommit=True) as connection:
            if args.operation == 'export':
                if args.apply:
                    raise ValueError('--apply is only supported for import')
                result = export_bundle(connection, args.owner_id, args.bundle, args.uploads,
                                       existing_image_manifest=args.existing_image_manifest)
            else:
                result = import_bundle(connection, args.owner_id, args.bundle, args.uploads, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        # Database DETAIL may contain private source rows or amounts. Report only the class/table.
        table = getattr(getattr(error, 'diag', None), 'table_name', None)
        message = str(error) if isinstance(error, ValueError) else type(error).__name__ + (f' ({table})' if table else '')
        parser.exit(1, 'account sync failed: ' + message + '\n')


if __name__ == '__main__':
    main()
