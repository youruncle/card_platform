"""Real PostgreSQL regression tests for scoped snapshot migration.

Uses disposable databases, never the development account's rows.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/account_cloud_sync.py'


def module():
    assert SCRIPT.is_file(), 'account snapshot export/import implementation is missing'
    spec = importlib.util.spec_from_file_location('account_cloud_sync', SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture(scope='module')
def schema_sql():
    return subprocess.run(['pg_dump', '--schema-only', '--no-owner', '--no-privileges',
                           '-d', os.getenv('CARD_SYNC_TEST_SCHEMA_DB', 'card_platform')],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def databases(schema_sql):
    admin = psycopg.connect('dbname=postgres', autocommit=True)
    names = ['card_sync_test_' + uuid4().hex for _ in range(2)]
    connections = []
    try:
        for name in names:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            subprocess.run(['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-q', '-d', name],
                           input=schema_sql, text=True, check=True, capture_output=True)
            connections.append(psycopg.connect(f'dbname={name}', autocommit=True))
        yield connections
    finally:
        for connection in connections:
            connection.close()
        for name in names:
            admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(
                sql.Identifier(name)))
        admin.close()


def seed(connection, ids, *, target=False):
    owner, other, brand, player, card_set, definition, card = ids
    for uid in [owner, other]:
        connection.execute('INSERT INTO app_user(id,email,display_name) VALUES(%s,%s,%s)',
                           (uid, f'{uid}@example.test', 'Synthetic'))
    connection.execute('INSERT INTO brand(id,name) VALUES(%s,%s)', (brand, 'Test brand'))
    connection.execute('INSERT INTO player(id,full_name,normalized_name) VALUES(%s,%s,%s)',
                       (player, 'Test Player', 'test player'))
    connection.execute('INSERT INTO card_set(id,brand_id,season_start,product_name) '
                       'VALUES(%s,%s,2024,%s)', (card_set, brand, 'Test product'))
    connection.execute('INSERT INTO card_definition(id,card_set_id,player_id,card_number) '
                       'VALUES(%s,%s,%s,%s)', (definition, card_set, player, '1'))
    if not target:
        connection.execute("INSERT INTO collection_item(id,owner_id,card_definition_id,"
                           "image_front_url,display_id,updated_at) VALUES(%s,%s,%s,%s,0,'2026-10-15')",
                           (card, owner, definition, '/uploads/front.jpg'))
        connection.execute("INSERT INTO card_transaction(collection_item_id,transaction_type,"
                           "transacted_at,unit_price,fee_amount,currency) "
                           "VALUES(%s,'buy','2026-01-01',12345.67,12.34,'CNY')", (card,))
        connection.execute("INSERT INTO scanner_recognition_job(owner_id,collection_item_id,"
                           "status,front_image_path,back_image_path,front_original_image_path,"
                           "back_original_image_path,front_raw_image_path,back_raw_image_path,"
                           "front_content_type,back_content_type,result) "
                           "VALUES(%s,%s,'succeeded','private/front.jpg','private/back.jpg',"
                           "'private/front.jpg','private/back.jpg','private/front.jpg','private/back.jpg',"
                           "'image/jpeg','image/jpeg',"
                           "'{\"confidence\":0.97}')", (owner, card))


def export_fixture(tmp_path, source, ids):
    uploads = tmp_path / 'source_uploads'
    uploads.mkdir()
    (uploads / 'front.jpg').write_bytes(b'private synthetic image')
    destination = tmp_path / 'package'
    module().export_bundle(source, str(ids[0]), destination, uploads)
    return destination


def test_snapshot_is_scoped_dry_run_and_repeated_apply_is_idempotent(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    # A source record belonging to somebody else must never enter the bundle.
    source.execute('INSERT INTO collection_item(owner_id,card_definition_id,display_id) '
                   'VALUES(%s,%s,0)', (ids[1], ids[5]))
    bundle = export_fixture(tmp_path, source, ids)
    assert (bundle.stat().st_mode & 0o777) == 0o700
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in bundle.iterdir())
    manifest = json.loads((bundle / 'manifest.json').read_text())
    assert manifest['tables']['collection_item'] == 1
    assert manifest['tables']['card_transaction'] == 1
    assert len(manifest['files']) == 1
    assert not ({'auth_session', 'user_credential', 'cardhobby_account'} & manifest['tables'].keys())
    cloud_uploads = tmp_path / 'cloud_uploads'
    sync.import_bundle(target, str(ids[0]), bundle, cloud_uploads)
    assert target.execute('SELECT count(*) FROM collection_item').fetchone()[0] == 0
    assert not cloud_uploads.exists()
    sync.import_bundle(target, str(ids[0]), bundle, cloud_uploads, apply=True)
    sync.import_bundle(target, str(ids[0]), bundle, cloud_uploads, apply=True)
    assert target.execute('SELECT count(*) FROM collection_item').fetchone()[0] == 1
    assert target.execute('SELECT unit_price,fee_amount FROM card_transaction').fetchone() == (
        __import__('decimal').Decimal('12345.67'), __import__('decimal').Decimal('12.34'))
    assert (cloud_uploads / 'front.jpg').read_bytes() == b'private synthetic image'
    assert target.execute('SELECT count(*) FROM scanner_recognition_job').fetchone()[0] == 0


def test_newer_cloud_card_and_shared_other_owner_values_survive(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    target.execute("INSERT INTO collection_item(id,owner_id,card_definition_id,display_id,notes,"
                   "updated_at) VALUES(%s,%s,%s,0,'new cloud edit','2026-10-16')", ids[6::] + ids[:1] + ids[5:6])
    target.execute('INSERT INTO collection_item(owner_id,card_definition_id,display_id) '
                   'VALUES(%s,%s,0)', (ids[1], ids[5]))
    source.execute("UPDATE card_definition SET is_autograph=true,updated_at='2026-10-16'")
    bundle = export_fixture(tmp_path, source, ids)
    report = sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute('SELECT notes FROM collection_item WHERE id=%s', (ids[6],)).fetchone()[0] == 'new cloud edit'
    assert target.execute('SELECT is_autograph FROM card_definition').fetchone()[0] is False
    assert target.execute('SELECT count(*) FROM collection_item').fetchone()[0] == 2
    assert target.execute('SELECT count(*) FROM card_transaction').fetchone()[0] == 0
    assert report['tables']['collection_item']['skipped_newer'] == 1


def test_foreign_owner_package_and_corrupt_images_are_rejected(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    bundle = export_fixture(tmp_path, source, ids)
    with pytest.raises(ValueError, match='owner'):
        sync.import_bundle(target, str(ids[1]), bundle, tmp_path / 'out', apply=True)
    image_tar = bundle / 'images.tar'
    with image_tar.open('ab') as handle:
        handle.write(b'corruption')
    with pytest.raises(ValueError, match='hash|checksum'):
        sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute('SELECT count(*) FROM collection_item').fetchone()[0] == 0


def test_nonprimary_unique_collision_rolls_back_everything(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    target.execute('INSERT INTO collection_item(owner_id,card_definition_id,display_id) '
                   'VALUES(%s,%s,0)', (ids[0], ids[5]))
    bundle = export_fixture(tmp_path, source, ids)
    with pytest.raises((ValueError, psycopg.errors.UniqueViolation)):
        sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute('SELECT count(*) FROM card_transaction').fetchone()[0] == 0
    assert target.execute('SELECT count(*) FROM collection_item').fetchone()[0] == 1


def test_delta_bundle_keeps_complete_manifest_and_requires_existing_images(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    uploads = tmp_path / 'source'
    uploads.mkdir()
    (uploads / 'front.jpg').write_bytes(b'private synthetic image')
    import hashlib
    existing = {'files': {'front.jpg': {'size': 23,
                'sha256': hashlib.sha256(b'private synthetic image').hexdigest()}}}
    bundle = tmp_path / 'delta'
    sync.export_bundle(source, str(ids[0]), bundle, uploads, existing_image_manifest=existing)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    assert len(manifest['files']) == 1
    assert manifest['packed_files'] == []
    with pytest.raises(ValueError, match='missing.*image|image.*missing'):
        sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'cloud', apply=True)
    assert target.execute('SELECT count(*) FROM collection_item').fetchone()[0] == 0


def test_rehashed_forbidden_table_is_rejected_before_database_changes(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    bundle = export_fixture(tmp_path, source, ids)
    import gzip
    import hashlib
    data = bundle / 'dataset.json.gz'
    with gzip.open(data, 'rt') as stream:
        payload = json.load(stream)
    payload['tables']['user_credential'] = []
    with gzip.open(data, 'wt') as stream:
        json.dump(payload, stream)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    manifest['data_sha256'] = hashlib.sha256(data.read_bytes()).hexdigest()
    (bundle / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='forbidden'):
        sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'cloud', apply=True)
    assert target.execute('SELECT count(*) FROM collection_item').fetchone()[0] == 0


def test_deleted_card_import_retains_ledger_and_valuation_history(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    source.execute("INSERT INTO valuation_snapshot(collection_item_id,method,currency,"
                   "low_value,fair_value,high_value,confidence,evidence) "
                   "VALUES(%s,'manual','CNY',90,100,110,0.8,'{}')", (ids[6],))
    source.execute("UPDATE collection_item SET deleted_at='2026-10-15',"
                   "deleted_reason='synthetic trash',deleted_by=%s WHERE id=%s", (ids[0], ids[6]))
    bundle = export_fixture(tmp_path, source, ids)
    sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute('SELECT deleted_at IS NOT NULL,deleted_reason FROM collection_item').fetchone() == (True, 'synthetic trash')
    assert target.execute('SELECT count(*) FROM card_transaction').fetchone()[0] == 1
    assert target.execute('SELECT fair_value FROM valuation_snapshot').fetchone()[0] == 100
    sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute('SELECT count(*) FROM valuation_snapshot').fetchone()[0] == 1


def test_money_and_numeric_json_keep_exact_values(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    source.execute('UPDATE card_transaction SET unit_price=9007199254740992.23')
    source.execute("INSERT INTO card_identity_version(collection_item_id,version,identity) "
                   "VALUES(%s,1,'{\"precision\":9007199254740992.23,\"confidence\":0.97}')", (ids[6],))
    bundle = export_fixture(tmp_path, source, ids)
    sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert str(target.execute('SELECT unit_price FROM card_transaction').fetchone()[0]) == '9007199254740992.23'
    assert target.execute("SELECT identity->>'precision',jsonb_typeof(identity->'confidence') "
                          "FROM card_identity_version").fetchone() == ('9007199254740992.23', 'number')


def tamper_payload(bundle, change):
    import gzip
    import hashlib
    data = bundle / 'dataset.json.gz'
    with gzip.open(data, 'rt') as stream:
        payload = json.load(stream)
    change(payload['tables'])
    with gzip.open(data, 'wt') as stream:
        json.dump(payload, stream)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    manifest['data_sha256'] = hashlib.sha256(data.read_bytes()).hexdigest()
    (bundle / 'manifest.json').write_text(json.dumps(manifest))


def test_foreign_transaction_cannot_satisfy_private_assignment_fk(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    foreign_card = target.execute('INSERT INTO collection_item(owner_id,card_definition_id,display_id) '
                                  'VALUES(%s,%s,0) RETURNING id', (ids[1], ids[5])).fetchone()[0]
    foreign_transaction = target.execute("INSERT INTO card_transaction(collection_item_id,transaction_type,"
                                        "transacted_at,currency) VALUES(%s,'buy',now(),'CNY') RETURNING id",
                                        (foreign_card,)).fetchone()[0]
    order = source.execute("INSERT INTO cardhobby_purchase_order(owner_id,external_order_id,capture,"
                           "evidence_hash,fetched_at) VALUES(%s,'test-order','{}','synthetic',now()) RETURNING id",
                           (ids[0],)).fetchone()[0]
    unit = source.execute("INSERT INTO cardhobby_purchase_unit(owner_id,order_id,item_id,unit_index,title,"
                          "unit_price,shipping) VALUES(%s,%s,'test-item',0,'synthetic',12,0) RETURNING id",
                          (ids[0], order)).fetchone()[0]
    transaction = source.execute('SELECT id FROM card_transaction').fetchone()[0]
    source.execute("INSERT INTO cardhobby_cost_assignment(owner_id,collection_item_id,unit_id,transaction_id,"
                   "evidence_hash,card_price,shipping,confidence,evidence,confirmation) "
                   "VALUES(%s,%s,%s,%s,'synthetic',12,0,0.9,'{}','user_confirmed')",
                   (ids[0], ids[6], unit, transaction))
    bundle = export_fixture(tmp_path, source, ids)
    tamper_payload(bundle, lambda tables: tables['cardhobby_cost_assignment'][0].update(
        transaction_id=str(foreign_transaction)))
    with pytest.raises(ValueError, match='private|same.card|owner|closure'):
        sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'cloud')


def test_identity_fk_must_reference_same_card_even_for_same_owner(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    second = source.execute('INSERT INTO collection_item(owner_id,card_definition_id,display_id) '
                            'VALUES(%s,%s,1) RETURNING id', (ids[0], ids[5])).fetchone()[0]
    identities = [source.execute("INSERT INTO card_identity_version(collection_item_id,version,identity) "
                                 "VALUES(%s,1,'{}') RETURNING id", (card,)).fetchone()[0]
                  for card in (ids[6], second)]
    source.execute("INSERT INTO card_identity_change_audit(collection_item_id,identity_version_id,reason,"
                   "before_state,after_state) VALUES(%s,%s,'synthetic','{}','{}')", (ids[6], identities[0]))
    bundle = export_fixture(tmp_path, source, ids)
    tamper_payload(bundle, lambda tables: tables['card_identity_change_audit'][0].update(
        identity_version_id=str(identities[1])))
    with pytest.raises(ValueError, match='same.card|private|owner'):
        sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'cloud')


def test_newer_cloud_card_skips_indirect_research_children(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    target.execute("INSERT INTO collection_item(id,owner_id,card_definition_id,display_id,updated_at) "
                   "VALUES(%s,%s,%s,0,'2026-10-16')", (ids[6], ids[0], ids[5]))
    research = source.execute("INSERT INTO identity_research_case(collection_item_id,status,source_version) "
                              "VALUES(%s,'awaiting_confirmation','synthetic') RETURNING id", (ids[6],)).fetchone()[0]
    source.execute("INSERT INTO identity_research_candidate(research_case_id,rank,identity,field_provenance,"
                   "confidence) VALUES(%s,1,'{}','{}',0.9)", (research,))
    request = source.execute("INSERT INTO analysis_request(owner_id,collection_item_id,status,input_payload) "
                             "VALUES(%s,%s,'completed','{}') RETURNING id", (ids[0], ids[6])).fetchone()[0]
    source.execute("INSERT INTO recommendation(analysis_request_id,rating,currency,fair_value_low,"
                   "fair_value_high,target_sell_low,target_sell_high,holding_period_min_months,"
                   "holding_period_max_months,confidence,thesis) "
                   "VALUES(%s,'hold','CNY',10,20,10,20,1,2,0.8,'synthetic')", (request,))
    bundle = export_fixture(tmp_path, source, ids)
    sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'cloud', apply=True)
    assert target.execute('SELECT count(*) FROM identity_research_case').fetchone()[0] == 0
    assert target.execute('SELECT count(*) FROM identity_research_candidate').fetchone()[0] == 0
    assert target.execute('SELECT count(*) FROM recommendation').fetchone()[0] == 0


def test_jsonb_literal_null_and_sql_null_remain_distinct(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    for field, current in [('card_number', "'null'::jsonb"), ('parallel_name', 'NULL')]:
        source.execute("INSERT INTO card_identity_correction(collection_item_id,field_name,"
                       "current_value,suggested_value,confidence,evidence,detector_version,status) "
                       f"VALUES(%s,%s,{current},'null'::jsonb,0.9,'[]','synthetic','open')", (ids[6], field))
    bundle = export_fixture(tmp_path, source, ids)
    sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute("SELECT field_name,current_value IS NULL,jsonb_typeof(current_value),"
                          "suggested_value IS NULL,jsonb_typeof(suggested_value) "
                          "FROM card_identity_correction ORDER BY field_name").fetchall() == [
        ('card_number', False, 'null', False, 'null'), ('parallel_name', True, None, False, 'null')]


def test_newer_local_update_restores_own_card_and_keeps_cloud_only_cards(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    source.execute("UPDATE collection_item SET notes='new local edit' WHERE id=%s", (ids[6],))
    target.execute("INSERT INTO collection_item(id,owner_id,card_definition_id,display_id,notes,"
                   "deleted_at,updated_at) VALUES(%s,%s,%s,0,'old edit','2026-01-02','2026-10-14')",
                   (ids[6], ids[0], ids[5]))
    cloud_only = target.execute("INSERT INTO collection_item(owner_id,card_definition_id,display_id,notes) "
                                "VALUES(%s,%s,99,'cloud-only') RETURNING id", (ids[0], ids[5])).fetchone()[0]
    bundle = export_fixture(tmp_path, source, ids)
    result = sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute('SELECT notes,deleted_at FROM collection_item WHERE id=%s', (ids[6],)).fetchone() == ('new local edit', None)
    assert target.execute('SELECT notes FROM collection_item WHERE id=%s', (cloud_only,)).fetchone()[0] == 'cloud-only'
    assert target.execute('SELECT next_display_id FROM collection_item_display_counter WHERE owner_id=%s', (ids[0],)).fetchone()[0] == 100
    assert result['tables']['collection_item']['updated'] == 1
    assert result['cloud_only_cards_retained'] == 1


def correction(connection, item, row_id, status, updated_at, value='synthetic'):
    connection.execute("INSERT INTO card_identity_correction(id,collection_item_id,field_name,"
                       "current_value,suggested_value,confidence,evidence,detector_version,status,updated_at) "
                       "VALUES(%s,%s,'card_number','null',to_jsonb(%s::text),0.9,'[]','synthetic',%s,%s)",
                       (row_id, item, value, status, updated_at))


def test_correction_closes_old_open_before_inserting_new_open(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    target.execute("INSERT INTO collection_item(id,owner_id,card_definition_id,display_id,updated_at) "
                   "VALUES(%s,%s,%s,0,'2026-10-14')", (ids[6], ids[0], ids[5]))
    old_id, new_id = uuid4(), uuid4()
    correction(source, ids[6], old_id, 'superseded', '2026-10-15')
    correction(source, ids[6], new_id, 'open', '2026-10-15')
    correction(target, ids[6], old_id, 'open', '2026-10-14')
    bundle = export_fixture(tmp_path, source, ids)
    sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert dict(target.execute('SELECT id,status FROM card_identity_correction')) == {
        old_id: 'superseded', new_id: 'open'}
    result = sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert result['tables']['card_identity_correction']['inserted'] == 0
    assert result['tables']['card_identity_correction']['updated'] == 0


@pytest.mark.parametrize('cloud_time,expected', [('2026-10-14', 'local'), ('2026-10-16', 'cloud')])
def test_open_correction_reuses_cloud_pk_and_preserves_confirmation_history(
        databases, tmp_path, cloud_time, expected):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    target.execute("INSERT INTO collection_item(id,owner_id,card_definition_id,display_id,updated_at) "
                   "VALUES(%s,%s,%s,0,'2026-10-14')", (ids[6], ids[0], ids[5]))
    local_id, cloud_id, history_id = uuid4(), uuid4(), uuid4()
    correction(source, ids[6], local_id, 'open', '2026-10-15', 'local')
    correction(target, ids[6], cloud_id, 'open', cloud_time, 'cloud')
    correction(target, ids[6], history_id, 'applied', '2026-10-13', 'confirmed history')
    bundle = export_fixture(tmp_path, source, ids)
    result = sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute("SELECT id,suggested_value#>>'{}' FROM card_identity_correction "
                          "WHERE status='open'").fetchone() == (cloud_id, expected)
    assert target.execute("SELECT id,suggested_value#>>'{}' FROM card_identity_correction "
                          "WHERE status='applied'").fetchone() == (history_id, 'confirmed history')
    assert result['natural_key_aliases']['card_identity_correction'] == 1
    repeat = sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert repeat['tables']['card_identity_correction']['inserted'] == 0
    assert repeat['tables']['card_identity_correction']['updated'] == 0


def test_open_alias_cannot_bypass_newer_same_pk_cloud_dismissal(databases, tmp_path):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    target.execute("INSERT INTO collection_item(id,owner_id,card_definition_id,display_id,updated_at) "
                   "VALUES(%s,%s,%s,0,'2026-10-14')", (ids[6], ids[0], ids[5]))
    source_id, other_id = uuid4(), uuid4()
    correction(source, ids[6], source_id, 'open', '2026-10-15', 'stale source')
    correction(target, ids[6], source_id, 'dismissed', '2026-10-16', 'new dismissal')
    correction(target, ids[6], other_id, 'open', '2026-10-14', 'separate open')
    bundle = export_fixture(tmp_path, source, ids)
    result = sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert dict(target.execute('SELECT id,status FROM card_identity_correction')) == {
        source_id: 'dismissed', other_id: 'open'}
    assert target.execute("SELECT suggested_value#>>'{}' FROM card_identity_correction WHERE id=%s",
                          (other_id,)).fetchone()[0] == 'separate open'
    assert result['natural_key_aliases']['card_identity_correction'] == 0


@pytest.mark.parametrize('scenario,expected', [
    ('current', 'accepted'), ('historical', 'needs_review'),
    ('blocked', 'needs_review'), ('revoked', 'needs_review'),
    ('newer_cloud_identity', 'needs_review'),
])
def test_only_current_confirmed_own_market_decision_is_source_authoritative(
        databases, tmp_path, scenario, expected):
    sync = module()
    source, target = databases
    ids = [uuid4() for _ in range(7)]
    seed(source, ids)
    seed(target, ids, target=True)
    cloud_date = '2026-10-16' if scenario == 'blocked' else '2026-10-14'
    target.execute("INSERT INTO collection_item(id,owner_id,card_definition_id,display_id,updated_at) "
                   "VALUES(%s,%s,%s,0,%s)", (ids[6], ids[0], ids[5], cloud_date))
    version, evidence, provider, decision = [uuid4() for _ in range(4)]
    for connection in (source, target):
        connection.execute("INSERT INTO card_identity_version(id,collection_item_id,version,identity,review_status) "
                           "VALUES(%s,%s,1,'{}','confirmed')", (version, ids[6]))
        connection.execute("INSERT INTO data_source(id,code,name,source_type) "
                           "VALUES(%s,'synthetic','Synthetic','synthetic')", (provider,))
        connection.execute("INSERT INTO market_identity_cache(identity_fingerprint,normalized_identity) "
                           "VALUES('synthetic-fingerprint','{}')")
        connection.execute("INSERT INTO market_evidence(id,identity_fingerprint,provider_code,external_sale_id,"
                           "raw_title,sale_price,currency,occurred_at,source_url,evidence_level) "
                           "VALUES(%s,'synthetic-fingerprint','synthetic','sale','Synthetic',25,'CNY',"
                           "'2026-01-01','https://example.test/sale','synthetic')", (evidence,))
    for connection, value in [(source, 'accepted'), (target, 'needs_review')]:
        connection.execute("INSERT INTO market_evidence_identity_decision(id,collection_item_id,identity_version_id,"
                           "market_evidence_id,decision,reason_codes,created_at) "
                           "VALUES(%s,%s,%s,%s,%s,'[]','2026-01-01')", (decision, ids[6], version, evidence, value))
    if scenario in {'historical', 'revoked', 'newer_cloud_identity'}:
        connection = target if scenario == 'newer_cloud_identity' else source
        review = 'rejected' if scenario == 'revoked' else 'confirmed'
        connection.execute("INSERT INTO card_identity_version(collection_item_id,version,identity,review_status,supersedes_id) "
                           "VALUES(%s,2,'{}',%s,%s)", (ids[6], review, version))
    bundle = export_fixture(tmp_path, source, ids)
    result = sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert target.execute('SELECT decision FROM market_evidence_identity_decision WHERE id=%s',
                          (decision,)).fetchone()[0] == expected
    assert result['tables']['market_evidence_identity_decision']['updated'] == (1 if scenario == 'current' else 0)
    repeat = sync.import_bundle(target, str(ids[0]), bundle, tmp_path / 'out', apply=True)
    assert repeat['tables']['market_evidence_identity_decision']['updated'] == 0
