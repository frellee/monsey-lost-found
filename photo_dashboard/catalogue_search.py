"""Photo-to-public-reference comparison with Wikimedia Commons.

Free / no paid API / no Ollama / no user photo uploads.
The media search API receives search *terms*, not the user's photograph.
This is NOT an internet-wide reverse image engine, and image similarity is
never proof of a specific brand, product, or model.
"""
from __future__ import annotations

from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import json
import re
import time


API = 'https://commons.wikimedia.org/w/api.php'
UA = 'MonseyTrailsPhotoReferenceTrial/1.0 (research prototype; Wikimedia Commons API)'  # read-only
ALLOWED_LICENSE = ('cc0', 'public domain', 'cc by', 'cc-by', 'pdm', 'pd-')
CATEGORIES = {
    'Phone': ['closed flip mobile phone', 'folded cell phone'],
    'Car key': ['car key fob', 'remote car key'],
    'AirPods': ['AirPods charging case', 'wireless earbuds case'],
    'Earbuds case': ['wireless earbuds charging case', 'AirPods case'],
    'Siddur': ['Jewish siddur prayer book', 'Hebrew prayer book'],
    'Book': ['Hebrew prayer book siddur', 'hardcover book'],
    'Religious book': ['Hebrew siddur prayer book', 'religious Jewish book'],
    'Tefillin': ['tefillin Jewish leather prayer boxes', 'tefillin'],
    'Tallis': ['Jewish tallit prayer shawl', 'tallit'],
    'Wallet': ['wallet leather folding', 'wallet'],
    'Remote control': ['handheld remote control', 'remote control device'],
    'Hand sanitizer': ['hand sanitizer bottle', 'disinfectant bottle'],
    'Phone case': ['mobile phone case', 'empty flip phone case'],
    'iPad': ['tablet Apple iPad', 'tablet computer'],
    'Hairbrush': ['hairbrush', 'hair brush'],
    'Keys': ['keyring keys', 'keys'],
    'Badge holder': ['badge holder lanyard', 'badge holder'],
    'AirPods case': ['Apple AirPods charging case', 'wireless earbuds charging case'],
    'Tallis bag': ['tallit bag velvet', 'Jewish tallis bag'],
    'Tefillin bag': ['tefillin velvet bag', 'Jewish tefillin storage bag'],
    'Chumash': ['Hebrew chumash book', 'Jewish Torah study book'],
    'Shtreimel': ['shtreimel Jewish fur hat', 'shtreimel'],
}


def queries_for(suggestion: dict) -> list[str]:
    """Only public, non-personal search terms. Never include voicemail or customer data."""
    name = str(suggestion.get('item') or '').strip()
    description = str(suggestion.get('caption') or suggestion.get('description') or '')
    candidate_names = [name]
    for row in suggestion.get('suggestions', [])[:3]:
        if isinstance(row, dict):
            candidate_names.append(str(row.get('item') or ''))
    if re.search(r'\b(earbud|airpod|charging case)\b', description, flags=re.I):
        candidate_names.insert(0, 'AirPods')
    if re.search(r'\b(flip phone|mobile phone|cell phone|clamshell phone)\b', description, flags=re.I):
        candidate_names.insert(0, 'Phone')
    out = []
    for key in candidate_names:
        if key in CATEGORIES:
            out.extend(CATEGORIES[key])
        elif re.fullmatch(r'[a-zA-Z][a-zA-Z \-]{2,54}', key) and key.lower() not in {
            'item needs review', 'analyzing photo', 'unknown', 'item', 'object', 'small box'}:
            out.append(key + ' object')
        if len(out) >= 4:
            break
    unique = []
    for s in out:
        s = re.sub(r'[^\w\-\s]', '', s).strip()[:72]
        if s and s.lower() not in {v.lower() for v in unique}:
            unique.append(s)
    return unique[:3]


def _text(s: str) -> str:
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]*>', ' ', str(s or '')))).strip()


def _download_json(url: str, timeout: int = 16):
    req = Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise OSError('Public image search service unavailable')
        payload = resp.read(2_000_001)
        if len(payload) > 2_000_000:
            raise OSError('Unexpected large API response')
        return json.loads(payload.decode('utf-8'))


def fetch_public_references(terms: list[str], max_total: int = 18, fetch_json=_download_json) -> list[dict]:
    """Query official Commons file search; retain source attribution and license."""
    refs, seen = [], set()
    for term in terms[:3]:
        params = {'action':'query','format':'json','formatversion':'2',
                  'generator':'search','gsrsearch':term,'gsrnamespace':'6','gsrlimit':'9',
                  'prop':'imageinfo','iiprop':'url|extmetadata','iiurlwidth':'320',
                  'iiextmetadatafilter':'LicenseShortName|LicenseUrl|Artist|Credit'}
        url = API + '?' + urlencode(params)
        try:
            results = fetch_json(url)
        except Exception as exc:
            if not refs:
                raise RuntimeError('Could not reach the free public image catalog: '+str(exc)[:160]) from exc
            break
        for page in results.get('query', {}).get('pages', []):
            title = page.get('title') or ''
            info = (page.get('imageinfo') or [{}])[0]
            thumb = info.get('thumburl') or ''
            pageurl = info.get('descriptionurl') or ''
            license_meta = info.get('extmetadata') or {}
            lic = _text((license_meta.get('LicenseShortName') or {}).get('value', ''))
            if not any(tag in lic.lower() for tag in ALLOWED_LICENSE):
                continue
            thost = urlparse(thumb)
            sourcehost = urlparse(pageurl)
            if thost.scheme != 'https' or thost.hostname != 'upload.wikimedia.org':
                continue
            if sourcehost.scheme != 'https' or sourcehost.hostname != 'commons.wikimedia.org':
                continue
            if title in seen:
                continue
            seen.add(title)
            refs.append({'title':title.removeprefix('File:').rsplit('.',1)[0][:140],
                         'thumbnail':thumb, 'source':pageurl,
                         'license':lic[:90],
                         'license_url':(license_meta.get('LicenseUrl') or {}).get('value','')[:300],
                         'credit':_text((license_meta.get('Artist') or license_meta.get('Credit') or {}).get('value',''))[:120],
                         'search_term':term})
            if len(refs) >= max_total:
                break
        if len(refs) >= max_total:
            break
    return refs


def _download_public_image(url: str):
    """Download a small Wikimedia-generated thumbnail, with bounds."""
    from PIL import Image, ImageOps
    u = urlparse(url)
    if u.scheme != 'https' or u.hostname != 'upload.wikimedia.org':
        raise ValueError('Only Wikimedia thumbnails permitted')
    req = Request(url, headers={'User-Agent': UA})
    with urlopen(req, timeout=14) as response:
        if not (response.headers.get('Content-Type') or '').lower().startswith('image/'):
            raise ValueError('Not an image')
        data = response.read(2_500_001)
    if len(data) > 2_500_000:
        raise ValueError('Thumbnail too large')
    with Image.open(BytesIO(data)) as original:
        if original.width * original.height > 9_000_000:
            raise ValueError('Thumbnail dimensions too large')
        out = ImageOps.exif_transpose(original).convert('RGB')
        out.thumbnail((400,400))
        return out.copy()


def compare_to_catalog(photo: Path, suggestion: dict,
                       fetch_refs=fetch_public_references,
                       download_image=_download_public_image,
                       visual_ranker=None) -> dict:
    """Read-only catalogue comparison. Photos do NOT leave the office computer.

    An existing local CLIP embedding compares visually to a small set of Commons
    reference thumbnails. It does not identify every consumer object.
    """
    from PIL import Image, ImageOps
    terms = queries_for(suggestion)
    if not terms:
        return {'catalogue_status':'unavailable','catalogue_note':'Could not generate safe object search terms.',
                'catalogue_matches':[], 'catalogue_queries':[]}
    try:
        refs = fetch_refs(terms)
        if not refs:
            return {'catalogue_status':'no_results','catalogue_note':'No reusable public reference pictures found.',
                    'catalogue_matches':[], 'catalogue_queries':terms}
        with Image.open(photo) as original:
            query = ImageOps.exif_transpose(original).convert('RGB')
            query.thumbnail((900,900))
        images, valid = [], []
        for ref in refs[:18]:
            try:
                im = download_image(ref['thumbnail'])
                images.append(im)
                valid.append(ref)
            except Exception:
                continue
        if not valid:
            return {'catalogue_status':'no_results','catalogue_note':'Reference thumbnails were temporarily unavailable.',
                    'catalogue_matches':[], 'catalogue_queries':terms}
        if visual_ranker is None:
            from auto_vision import _model
            import torch
            model, processor = _model()  # reuses the existing model; nothing new installed
            batch = processor(images=[query]+images, return_tensors='pt')
            with torch.inference_mode():
                embeddings = model.get_image_features(pixel_values=batch['pixel_values'])
            if not isinstance(embeddings, torch.Tensor):
                pooled = getattr(embeddings,'pooler_output',None)
                embeddings = pooled if pooled is not None else getattr(embeddings,'image_embeds',None)
            if embeddings is None:
                raise RuntimeError('Local image comparison unavailable')
            embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True).clamp_min(1e-8)
            values = (embeddings[1:] @ embeddings[0]).tolist()
        else:
            values = visual_ranker(query, images)
        ranked = sorted(zip(valid, values), key=lambda kv:kv[1], reverse=True)[:5]
        matches=[]
        for ref, sim in ranked:
            d=dict(ref)
            d['similarity']=round(float(sim),3)
            matches.append(d)
        # A Commons image title is NOT a product identification. Use the search
        # category that found it, corroborated by two references if possible.
        def label_for_term(term):
            low=term.lower()
            for name, variants in CATEGORIES.items():
                if any(low == v.lower() for v in variants):
                    if name=='Book' and ('siddur' in low or 'prayer book' in low):
                        return 'Siddur'
                    if name in ('Earbuds case','AirPods case') and 'airpods' in low:
                        return 'AirPods case'
                    return name
            return str(suggestion.get('item') or '').strip()
        groups={}
        for ref, score in ranked:
            if float(score) < .245: continue
            label=label_for_term(ref['search_term'])
            if label:
                groups.setdefault(label, []).append((float(score), ref))
        totals=[]
        for label, vals in groups.items():
            vals.sort(key=lambda p:p[0],reverse=True)
            # One good match is evidence; a second image increases reliability.
            grade=vals[0][0] + (min(.09,vals[1][0] * .20) if len(vals)>1 else 0)
            totals.append((grade, label, vals))
        totals.sort(reverse=True)
        choice=totals[0] if totals else None
        next_grade=totals[1][0] if len(totals)>1 else 0.0
        likely_name=choice[1] if choice and choice[2][0][0] >= .275 else ''
        strong=bool(choice and likely_name and choice[2][0][0]>=.325 and (
            len(choice[2])>=2 or choice[0]-next_grade>=.07) and choice[0]-next_grade>=.040)
        evidence=choice[2][0][1] if choice else {}
        return {'catalogue_status':'ready','catalogue_note':'Closest Wikimedia Commons reference images; this is a suggestion, not verified identity.',
                'catalogue_matches':matches,'catalogue_queries':terms,
                'wiki_name': likely_name, 'wiki_strong':strong,
                'wiki_source':evidence.get('source',''),'wiki_license':evidence.get('license','')}
    except Exception as exc:
        return {'catalogue_status':'unavailable', 'catalogue_note':str(exc)[:220],
                'catalogue_matches':[], 'catalogue_queries':terms}
