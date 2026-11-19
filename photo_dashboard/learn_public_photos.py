"""Build a free visual reference gallery from Wikimedia Commons.

Only images with accepted reuse permissions are saved, and attribution is
recorded. Uses the Commons API, respects rate limits, and can be interrupted.
This is a SMALL, targeted reference library, not full network-model retraining.
"""
from __future__ import annotations
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DEST=ROOT/'reference_bank'
PHOTOS=DEST/'images'
META=DEST/'credits.json'
API='https://commons.wikimedia.org/w/api.php'
AGENT='MonseyTrailsLocalPhotoReferenceTrial/1.0 (personal, non-automated local training; https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia)'
# One careful source category per label, plus search terms as fallbacks.
SEARCHES={
 'Phone':['Category:Flip phones','Category:Mobile phones with clamshell form factor','Category:Motorola flip phones','Category:Nokia 6131'],
 'Tefillin':['Category:Tefillin'],
 'Tefillin bag':['Category:Tefillin bags'],
 'Shtreimel':['Category:Shtreimels','Category:Shtreimel'],
 'Kippah':['Category:Kippahs'],
 'Mezuzah':['Category:Mezuzahs'],
 'Tallis':['Category:Tallit'],
 'Siddur':['Category:Individual images of siddurim','Category:Siddurim','Category:Jewish prayer books'],
 'Wallet':['Category:Wallets'],
 'Car key':['Category:Car keys'],
 'AirPods':['Category:Apple AirPods','Category:AirPods','Category:Wireless earbuds'],
 'Carriage':['Category:Baby strollers'],
 'Backpack':['Category:Backpacks'],
 'Watch':['Category:Wristwatches'],
 'Coat':['Category:Coats'],
 'Umbrella':['Category:Umbrellas'],
 'Glasses':['Category:Eyeglasses'],
 'Sefer':['Category:Jewish religious books'],
 'Chumash':['Category:Chumashim'],
 'Phone case':['Category:Mobile phone cases'],
 'Earbuds':['Category:Earbuds'],
 'Socks':['Category:Socks'],
 'Suitcase':['Category:Suitcases'],
 'Wallet':['Category:Wallets'],
}

def get_json(params):
    url=API+'?'+urllib.parse.urlencode({**params,'format':'json','formatversion':2})
    req=urllib.request.Request(url,headers={'User-Agent':AGENT})
    with urllib.request.urlopen(req,timeout=25) as r:return json.load(r)

def get_image(url):
    req=urllib.request.Request(url,headers={'User-Agent':AGENT})
    with urllib.request.urlopen(req,timeout=25) as r:
        data=r.read(2_200_000)
    if len(data)>2_000_000:raise ValueError('image too big')
    return data

def stripped(x):
    return html.unescape(re.sub(r'<[^>]+>','',str(x or ''))).strip()[:500]

def accepted(info):
    meta=info.get('extmetadata') or {}
    def v(k):return stripped((meta.get(k) or {}).get('value',''))
    lic=v('LicenseShortName').lower()
    reuse=('cc0','public domain','cc by','cc-by','pd-', 'cc-by-sa')
    if not any(tok in lic for tok in reuse):return None
    if 'nc' in lic or 'nd' in lic:return None
    return {'license':v('LicenseShortName'),'license_url':v('LicenseUrl'),
            'author':v('Artist'),'attribution':v('AttributionRequired'),'source':stripped(info.get('descriptionurl'))}

def collect(max_photos_per_label=12):
    PHOTOS.mkdir(parents=True,exist_ok=True)
    existing=json.loads(META.read_text(encoding='utf-8')) if META.exists() else []
    used={r['source'] for r in existing}
    counts={}
    for row in existing:counts[row['label']]=counts.get(row['label'],0)+1
    for label,cats in SEARCHES.items():
        for category in cats:
            if counts.get(label,0)>=max_photos_per_label:break
            print('Looking for licensed photographs:',label, category, flush=True)
            try:
                result=get_json({'action':'query','generator':'categorymembers','gcmtitle':category,
                                'gcmtype':'file','gcmlimit':'50','prop':'imageinfo','iiprop':'url|extmetadata|mime',
                                'iiurlwidth':'512'})
            except Exception as ex:
                print('Source unavailable, moving on:',str(ex)[:140]);continue
            pages=(result.get('query') or {}).get('pages') or []
            for page in pages:
                if counts.get(label,0)>=max_photos_per_label:break
                imglist=page.get('imageinfo') or []
                if not imglist:continue
                info=imglist[0]
                if info.get('mime') not in ('image/jpeg','image/png','image/webp'):continue
                credit=accepted(info)
                if not credit or not credit['source'] or credit['source'] in used:continue
                thumb=info.get('thumburl') or info.get('url')
                if not thumb or not thumb.startswith('https://'):continue
                try:
                    content=get_image(thumb)
                    from PIL import Image
                    from io import BytesIO
                    with Image.open(BytesIO(content)) as im:
                        if im.width<80 or im.height<80:continue
                        im=im.convert('RGB');im.thumbnail((512,512))
                        key=hashlib.sha256(credit['source'].encode('utf-8')).hexdigest()[:20]
                        file=PHOTOS/(key+'.jpg')
                        im.save(file,quality=84)
                    existing.append({'label':label,'file':file.name,**credit})
                    used.add(credit['source'])
                    counts[label]=counts.get(label,0)+1
                    print('Saved',label,counts[label],flush=True)
                except Exception as ex:
                    print('Skipped one image:',str(ex)[:110])
                time.sleep(.4)
            time.sleep(.8)
    META.write_text(json.dumps(existing,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Downloaded',len(existing),'total permitted reference images. Credits saved in',META)
    return existing

def index(references):
    import numpy as np
    import torch
    from PIL import Image
    from auto_vision import _model
    model,processor=_model()
    vectors=[];labels=[]
    print('Building local visual reference index...')
    for n in range(0,len(references),8):
        batch=[];labs=[]
        for ref in references[n:n+8]:
            try:
                with Image.open(PHOTOS/ref['file']) as im:batch.append(im.convert('RGB'))
                labs.append(ref['label'])
            except Exception:continue
        if not batch:continue
        inputs=processor(images=batch,return_tensors='pt')
        with torch.inference_mode():f=model.get_image_features(pixel_values=inputs['pixel_values'])
        if not isinstance(f,torch.Tensor):
            candidate=getattr(f,'pooler_output',None)
            if candidate is None:candidate=getattr(f,'image_embeds',None)
            f=candidate
        if f is None:raise RuntimeError('CLIP could not produce image embeddings')
        vs=f.detach().cpu().numpy().astype('float32')
        vs/=np.maximum(np.linalg.norm(vs,axis=1,keepdims=True),1e-12)
        vectors.extend(vs)
        labels.extend(labs)
        print('Indexed',len(labels),'photos',flush=True)
    if vectors:
        np.savez_compressed(DEST/'embeddings.npz',vectors=np.asarray(vectors,dtype='float32'),labels=np.asarray(labels,dtype='U50'))
        print('SUCCESS: Visual reference index saved. Restart the photo trial to use it.')
    else:print('No usable images were downloaded; no index was made.')

if __name__=='__main__':
    print('MONSEY TRAILS - Free Commons Visual Practice Library')
    print('This downloads CC or public-domain image thumbnails only; no customer photos leave this PC.')
    refs=collect()
    index(refs)
