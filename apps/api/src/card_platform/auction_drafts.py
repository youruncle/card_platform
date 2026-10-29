"""Private single-card auction preparation. This module never publishes listings."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError
from psycopg.types.json import Jsonb

from card_platform.image_variants import image_revision
from card_platform.repository import NotFoundError, PortfolioRepository

MAX_PHOTOS = 12
EXTERNAL = dict(supported=False, status='unavailable', reason='已发现卡淘独立草稿接口，但图片上传与草稿回读尚未验证；当前仅保存平台草稿，未向卡淘提交。')
IDENTITY_FIELDS = (
    'player_name', 'season_start', 'season_end', 'brand_name', 'product_name',
    'set_name', 'card_number', 'insert_name', 'parallel_name', 'is_rookie',
    'is_autograph', 'autograph_type', 'is_memorabilia', 'memorabilia_types',
    'memorabilia_usage', 'is_patch', 'serial_number', 'serial_total',
    'grade_company', 'grade_value', 'is_redemption', 'redemption_status',
)


class DraftConflict(ValueError):
    pass


def validate_selection(ids, photos, *, allow_empty=False):
    available = {str(photo['id']) for photo in photos}
    if (not isinstance(ids, list) or len(ids) > MAX_PHOTOS or (not ids and not allow_empty)
            or any(not isinstance(id, str) or id not in available for id in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('请选择本草稿的1至12张照片，不能重复选择或引用其他卡片的照片')
    return list(ids)


def apply_edit(row, revision, changes):
    if revision != row['revision']:
        raise DraftConflict('草稿已在其他窗口更新，请重新读取后再保存；本次未覆盖原内容')
    result = deepcopy(row)
    manual_fields = set(result.get('generation_basis', {}).get('manual_fields', []))
    for key, value in changes.items():
        if key not in {'title', 'description', 'condition_notes', 'selected_photo_ids'}:
            raise ValueError('无效的草稿字段')
        if key == 'selected_photo_ids':
            value = validate_selection(value, row['photos'], allow_empty=True)
        if key in {'title', 'description'} and value != row[key]:
            manual_fields.add(key)
        result[key] = value
    if manual_fields:
        result.setdefault('generation_basis', {})['manual_fields'] = sorted(manual_fields)
    result['revision'] += 1
    return result


def fact(value):
    if value is None or isinstance(value, bool):
        return ''
    text = ' '.join(str(value).split())
    return '' if text.casefold() in {'unknown', 'none', 'null', '未知', '待确认', '未识别'} else text


def generate_copy(identity, photo_count, condition_notes, examples, examples_status):
    if not 1 <= photo_count <= MAX_PHOTOS:
        raise ValueError('请先选择1至12张拍卖照片')
    season = fact(identity.get('season_start'))
    end = fact(identity.get('season_end'))
    if season and end and end != season:
        season += '-' + end[-2:]
    player = fact(identity.get('player_name'))
    brand = fact(identity.get('brand_name'))
    product = fact(identity.get('product_name'))
    parts = [season, brand, product, player]
    labels = [('球员', player), ('赛季', season), ('品牌 / 产品', ' '.join(filter(None, (brand, product))))]
    for key, label in [('set_name', '系列'), ('card_number', '卡号'), ('insert_name', '特卡'), ('parallel_name', '平行')]:
        value = fact(identity.get(key))
        if value:
            labels.append((label, value))
            parts.append('#' + value if key == 'card_number' else value)
    if identity.get('is_rookie') is True:
        parts.append('RC 新秀')
        labels.append(('新秀卡', '是'))
    if identity.get('is_autograph') is True:
        autograph = {'on_card': '卡签', 'sticker': '贴签', 'cut': '切割签'}.get(identity.get('autograph_type'), '签字')
        parts.append(autograph)
        labels.append(('签名', autograph))
    serial, total = fact(identity.get('serial_number')), fact(identity.get('serial_total'))
    if total:
        serial_label = f'{serial}/{total}' if serial else f'/{total}编'
        parts.append(serial_label)
        labels.append(('限编', serial_label))
    company, grade = fact(identity.get('grade_company')), fact(identity.get('grade_value'))
    if company and grade:
        parts.append(f'{company} {grade}')
        labels.append(('评级', f'{company} {grade}'))
    if identity.get('is_memorabilia') is True:
        material = 'Patch 实物卡' if identity.get('is_patch') is True else '实物卡'
        parts.append(material)
        labels.append(('实物', material))
    if identity.get('is_redemption') is True:
        parts.append('交换卡')
        labels.append(('交换卡状态', {'unredeemed': '未兑换', 'redeemed': '已兑换', 'expired': '已过期'}.get(identity.get('redemption_status'), '待确认')))
    notes = condition_notes.strip()
    title = ' '.join(filter(None, parts))[:200].strip() or '球星卡（身份待补充）'
    description = '\n'.join([
        '单张球星卡拍卖信息草稿',
        *[f'{label}：{value}' for label, value in labels if value],
        '', f'拍卖图片：已选 {photo_count} 张。',
        f'品相备注：{notes}' if notes else '品相备注：待卖家检查并补充，请查看所选实拍图。',
        '起拍价、运费及截标时间：待卖家设置。',
    ])
    return dict(title=title, description=description, generation_basis={
        'template': 'cardhobby-style-v1', 'method': 'confirmed_identity_template',
        'examples_status': examples_status, 'examples': examples[:3],
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'message': '参考本人订单标题的字段组织，未照搬其他卖家的品相或交易承诺。' if examples_status == 'used' else '本次未取得本人订单样例，使用基础卡淘风格模板。',
    })


class AuctionMediaStore:
    """Photos are immutable private snapshots, never public portfolio uploads."""

    def __init__(self, root):
        self.root = Path(root)

    def directory(self, owner, draft):
        path = self.root / str(UUID(str(owner))) / str(UUID(str(draft)))
        for check in (self.root, path.parent, path):
            if check.is_symlink():
                raise FileNotFoundError('拍卖图片不存在')
        return path

    def save(self, owner, draft, content, side, source):
        if side not in {'front', 'back', 'detail'} or source not in {'existing', 'upload', 'camera', 'redmi'}:
            raise ValueError('无效的照片来源或位置')
        try:
            with Image.open(BytesIO(content)) as image:
                extension = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}.get(image.format)
                if not extension or image.width * image.height > 40_000_000:
                    raise ValueError('拍卖图片格式或尺寸不支持')
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError('无法读取照片，请选择有效的JPEG、PNG或WebP图片') from error
        photo_id = str(uuid4())
        directory = self.directory(owner, draft)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / (photo_id + extension)
        with path.open('xb') as file:
            os.chmod(path, 0o600)
            file.write(content)
        return dict(id=photo_id, filename=path.name, side=side, source=source, sha256=sha256(content).hexdigest())

    def resource(self, owner, draft, photo):
        filename = photo.get('filename', '')
        if not re.fullmatch(r'[0-9a-f-]{36}\.(jpg|png|webp)', filename):
            raise FileNotFoundError('拍卖图片不存在')
        path = self.directory(owner, draft) / filename
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError('拍卖图片不存在')
        return path


async def owned_item(connection, owner, item_id):
    row = await (await connection.execute(
        'SELECT id FROM collection_item WHERE id=%s AND owner_id=%s AND deleted_at IS NULL FOR SHARE',
        (item_id, owner),
    )).fetchone()
    if row is None:
        raise NotFoundError('卡片不存在')
    items = await PortfolioRepository().list_items(connection, owner, item_ids=[item_id])
    if not items:
        raise NotFoundError('卡片不存在')
    return items[0].model_dump(mode='json')


async def load_draft(connection, owner, draft_id, *, lock=False):
    row = await (await connection.execute(
        '''SELECT d.* FROM auction_draft d JOIN collection_item c ON c.id=d.item_id
           WHERE d.id=%s AND d.owner_id=%s AND c.owner_id=%s AND c.deleted_at IS NULL'''
        + (' FOR UPDATE OF d FOR SHARE OF c' if lock else ' FOR SHARE OF c'),
        (draft_id, owner, owner),
    )).fetchone()
    if row is None:
        raise NotFoundError('拍卖草稿不存在')
    return row


async def create_draft(connection, owner, item_id, upload_root, media):
    item = await owned_item(connection, owner, item_id)
    await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'auction:{owner}:{item_id}',))
    row = await (await connection.execute('SELECT * FROM auction_draft WHERE owner_id=%s AND item_id=%s', (owner, item_id))).fetchone()
    if row:
        return row
    draft_id = uuid4()
    photos, warnings = [], []
    root = Path(upload_root)
    for side in ('front', 'back'):
        filename = image_revision(item.get(f'image_{side}_url'))
        if not filename:
            continue
        path = root / filename
        if path.is_symlink() or path.resolve().parent != root.resolve() or not path.is_file():
            warnings.append(f'原卡{side}图片暂不可读取，请重新上传拍卖照片')
            continue
        photos.append(media.save(owner, draft_id, path.read_bytes(), side, 'existing'))
    identity = {key: item.get(key) for key in IDENTITY_FIELDS}
    return await (await connection.execute(
        '''INSERT INTO auction_draft(id,owner_id,item_id,identity,photos,selected_photo_ids,generation_basis)
           VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (draft_id, owner, item_id, Jsonb(identity), Jsonb(photos), Jsonb([p['id'] for p in photos]), Jsonb({'warnings': warnings})),
    )).fetchone()


async def persist(connection, row):
    return await (await connection.execute(
        '''UPDATE auction_draft SET title=%s,description=%s,condition_notes=%s,selected_photo_ids=%s,
           photos=%s,identity=%s,generation_basis=%s,revision=%s,updated_at=clock_timestamp()
           WHERE id=%s AND owner_id=%s RETURNING *''',
        (row['title'], row['description'], row['condition_notes'], Jsonb(row['selected_photo_ids']),
         Jsonb(row['photos']), Jsonb(row['identity']), Jsonb(row['generation_basis']),
         row['revision'], row['id'], row['owner_id']),
    )).fetchone()


def public_draft(row):
    value = {key: row[key] for key in ('id', 'item_id', 'revision', 'status', 'title', 'description',
                                     'condition_notes', 'selected_photo_ids', 'identity', 'generation_basis', 'updated_at')}
    value['photos'] = [{**{key: photo[key] for key in ('id', 'side', 'source', 'sha256')},
                        'url': f'/v1/auction/drafts/{row["id"]}/images/{photo["id"]}'} for photo in row['photos']]
    value['external'] = dict(EXTERNAL)
    return value
