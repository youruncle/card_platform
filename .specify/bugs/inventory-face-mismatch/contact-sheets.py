"""Read-only inventory contact sheets; no image or database changes."""
import json
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'.runtime/inventory-face-mismatch'
rows=json.loads((OUT/'inventory.json').read_text())
font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',16)
small=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',13)
(OUT/'sheets').mkdir(exist_ok=True)
for n in range(0,len(rows),16):
 group=rows[n:n+16];canvas=Image.new('RGB',(1840,1480),'#e1e4e8');d=ImageDraw.Draw(canvas)
 for j,r in enumerate(group):
  x=(j%4)*460;y=(j//4)*370
  d.text((x+6,y+3),f"ID {r['display_id']} | {r['player_name']}",font=font,fill='black')
  label=f"{r['season_start']} {r['product_name']} / {r['insert_name'] or ''} #{r['card_number'] or ''}"
  d.text((x+6,y+24),label[:62],font=small,fill='black')
  for k,side in enumerate(['front','back']):
   p=ROOT/'apps/api/uploads'/r[f'image_{side}_url'].split('/')[-1]
   if p.exists():
    with Image.open(p) as im:
     im=ImageOps.exif_transpose(im);im=ImageOps.contain(im.convert('RGB'),(224,317))
     canvas.paste(im,(x+k*230+3+(224-im.width)//2,y+48+(317-im.height)//2))
   else:d.text((x+k*230+4,y+100),'MISSING',font=font,fill='red')
 path=OUT/'sheets'/f"{n//16:03d}-{group[0]['display_id']}-{group[-1]['display_id']}.jpg"
 canvas.save(path,quality=87)
print('sheets',len(list((OUT/'sheets').glob('*.jpg'))))
