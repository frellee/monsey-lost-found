"""Free local, best-effort photo identification. No uploaded images or paid API calls.

Improved V6 notes:
- stronger prompt coverage for phones, especially closed flip phones
- aggregated prompt scoring per item instead of a single caption per class
- richer color detection supporting multicolor / camouflage-style items
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from pathlib import Path
from threading import Lock

CATEGORIES = [
    'Coat','Jacket','Sweater','Shirt','Pants','Skirt','Dress','Socks','Shoes','Boots',
    'Hat','Scarf','Gloves','Belt','Tie','Tallis','Tefillin','Tallis bag','Tefillin bag','Shtreimel','Kippah','Tzitzis','Sefer','Chumash','Mezuzah','Bag','Handbag','Backpack',
    'Suitcase','Wallet','Purse','Carriage','Stroller','Baby blanket','Blanket',
    'AirPods','Earbuds','Headphones','iPad','Tablet','iPhone','Phone','Laptop','Charger',
    'Power bank','Watch','Glasses','Sunglasses','Keys','Car key','Passport','ID card',
    'Credit card','Jewelry','Necklace','Bracelet','Ring','Earrings','Umbrella',
    'Water bottle','Thermos','Lunchbox','Book','Siddur','Notebook','Picture frame',
    'Photo','Camera','Toys','Stuffed animal','Plastic cover','Phone case','Tablet case',
    'Shopping bag','Golf club','Tool','Flashlight','Portable speaker','Small box',
    'Earbuds case','Coin purse','Pencil case','Lunch bag','School bag','Briefcase','Hand sanitizer',
    'Medicine bottle','Water flask','Sewing kit','Hairbrush','Comb','Towel','Prayer book bag',
    'Shoe bag','Swim goggles','Bicycle helmet','Phone stand','Charging cable','USB drive',
    'Remote control','Smartwatch','Earphone case','Cardholder','Badge holder','Religious book',
    'Toys','Toy car','Speaker','Portable radio','Baseball cap','Glasses case','Shaving kit',
]

CATEGORY_VARIANTS = {
    'Phone': [
        'a closed black kosher flip mobile phone',
        'a closed gray folding cell phone with a hinge',
        'a mobile phone',
        'a cell phone',
        'a smartphone',
        'a closed flip phone',
        'a folded flip phone',
        'a small closed flip phone seen from the outside',
        'a compact folded clamshell mobile telephone with a display on the outside',
        'a tiny gray flip cell phone folded closed',
        'a closed old-style flip phone with its camera on the lid',
        'a rectangular black feature phone with keypad and hinge',
        'a flip phone',
        'a small black phone',
        'a small mobile phone lying on a table',
        'a closed black flip phone with rounded corners',
        'a folded clamshell flip phone from the back, with an outer camera',
        'a closed flip cell phone resting on a wooden table',
        'an old fashioned folding mobile phone closed with a hinge',
        'a feature phone with its lid flipped shut',
        'a small closed mobile handset with a charging port',
        'a closed kosher flip phone with no visible keyboard',

    ],
    'iPhone': [
        'an Apple iPhone',
        'an iPhone smartphone',
        'a modern smartphone',
    ],
    'iPad': ['an Apple iPad tablet', 'an iPad lying flat'],
    'Tablet': ['a tablet computer', 'a touchscreen tablet'],
    'Earbuds case': ['a small white wireless earbuds charging case', 'a closed smooth plastic case for wireless earbuds'],
    'Glasses case': ['a protective glasses case or eyeglasses box'],
    'Religious book': ['a Hebrew religious book with Hebrew lettering', 'a Jewish prayer or study book'],
    'AirPods': [
        'Apple AirPods with their charging case',
        'a closed white Apple AirPods charging case with rounded edges',
        'a gray protective case for Apple AirPods',
        'a small rounded wireless earbud charging case with hinged lid',
        'a white AirPods charging case viewed from the top with no earbuds visible',
        'a compact rectangular AirPods case with a small charging light',
        'a white wireless earbuds charging case, not a phone',
    ],
    'Earbuds': ['wireless earbuds in a case', 'a pair of earbuds'],
    'Wallet': ['a wallet', 'a folded wallet', 'a black wallet'],
    'Purse': ['a purse', 'a handbag purse'],
    'Phone case': ['an empty smartphone protective phone case', 'a flat silicone phone cover without electronics',
                   'a rubber phone case with camera cutouts'],
    'Small box': ['a cardboard box with straight square edges', 'a small cardboard package'],
    'Plastic cover': ['a clear plastic cover', 'a transparent protective cover'],
    'Tallis': ['a folded Jewish prayer shawl (tallit)', 'a folded tallis bag or tallis'],
    'Tefillin': ['a pair of Jewish black leather tefillin prayer boxes'],
    'Tefillin bag': ['a velvet bag embroidered with a tefillin case', 'a tefillin storage pouch'],
    'Tallis bag': ['an embroidered velvet prayer shawl bag', 'a tallit bag'],
    'Shtreimel': ['a fur shtreimel Hasidic Jewish hat', 'a round fur Jewish ceremonial hat'],
    'Kippah': ['a yarmulke skullcap', 'a Jewish kippah'],
    'Tzitzis': ['a white Jewish tzitzit garment with fringes', 'a pair of tzitzit fringes'],
    'Sefer': ['a Jewish religious book or sefer', 'a Hebrew book on a table'],
    'Chumash': ['a Chumash Torah book', 'a Hebrew printed Pentateuch book'],
    'Mezuzah': ['a small Jewish mezuzah case', 'a mezuzah for a doorpost'],
    'Carriage': ['a baby carriage or pram', 'a stroller carriage'],
    'Stroller': ['a folding baby stroller'],
    'Siddur': [
        'a Jewish siddur prayer book with Hebrew lettering on the cover',
        'a compact hardcover siddur for daily prayers',
        'a red siddur Hebrew prayer book lying on a table',
        'a gray Hebrew Jewish prayer book with decorative lettering',
        'an Orthodox Jewish prayer book with Hebrew words printed on the front',
        'a bound siddur with gold Hebrew writing on a dark cover',
    ],
    'Book': [
        'an ordinary hardcover book',
        'a paperback novel in English',
        'a non-religious book',
    ],
    'Sefer': [
        'a Hebrew Jewish religious book with ornate Hebrew lettering',
        'a sefer with a traditional Hebrew cover',
        'a Jewish Torah study book',
    ],
    'Car key': ['a car key with a remote fob'],
    'Picture frame': ['a framed photograph'],
    'Jewelry': ['a piece of jewelry', 'small jewelry such as ring bracelet necklace'],
}
CLIP_ID = 'openai/clip-vit-base-patch32'
CLIP_REVISION = 'bfc19449bc6f18c54292b85378cbccc3ad91a24e'
_MODEL = None
_PROCESSOR = None
_MODEL_LOCK = Lock()


def _model():
    global _MODEL, _PROCESSOR
    if _MODEL is None:
        from transformers import CLIPModel, CLIPProcessor
        import torch
        torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
        _PROCESSOR = CLIPProcessor.from_pretrained(CLIP_ID, revision=CLIP_REVISION)
        _MODEL = CLIPModel.from_pretrained(CLIP_ID, revision=CLIP_REVISION, use_safetensors=True).eval()
    return _MODEL, _PROCESSOR


def _build_prompts():
    prompts = []
    owners = []
    for name in CATEGORIES:
        variants = CATEGORY_VARIANTS.get(name, [f'a clear photo of a {name.lower()}'])
        enriched = []
        for v in variants:
            enriched.append(v if v.startswith('a ') or v.startswith('an ') else 'a clear photo of ' + v)
        for p in enriched:
            prompts.append(p)
            owners.append(name)
    return prompts, owners

PROMPTS, PROMPT_OWNERS = _build_prompts()


def _foreground_stats(image):
    import numpy as np
    from PIL import ImageOps
    im = ImageOps.exif_transpose(image).convert('RGB')
    im.thumbnail((320,320))
    a = np.asarray(im).astype('float32')
    h, w = a.shape[:2]
    pad = max(2, round(min(w,h)*.06))
    edges = np.concatenate((a[:pad].reshape(-1,3), a[-pad:].reshape(-1,3), a[:,:pad].reshape(-1,3), a[:,-pad:].reshape(-1,3)), axis=0)
    backdrop = np.median(edges, axis=0)
    edge_var = float(np.median(np.linalg.norm(edges-backdrop, axis=1)))
    y1,y2 = int(h*.12), int(h*.88)
    x1,x2 = int(w*.12), int(w*.88)
    crop = a[y1:y2, x1:x2]
    if crop.size == 0:
        return {'mask': None, 'bbox_ratio': 0.0, 'aspect': 1.0, 'dark_share': 0.0, 'neutral_share': 0.0}
    flat = crop.reshape(-1,3)
    dist = np.linalg.norm(flat-backdrop, axis=1)
    thresh = max(34, edge_var*2.15) if edge_var < 40 else max(42, edge_var*1.7)
    mask = (dist.reshape(crop.shape[:2]) > thresh)
    if mask.mean() < 0.05:
        mask = dist.reshape(crop.shape[:2]) > max(26, edge_var*1.2)
    if mask.mean() < 0.03:
        return {'mask': mask, 'bbox_ratio': 0.0, 'aspect': 1.0, 'dark_share': 0.0, 'neutral_share': 0.0}
    ys, xs = np.where(mask)
    top, bottom = ys.min(), ys.max()
    left, right = xs.min(), xs.max()
    hh = max(1, bottom-top+1)
    ww = max(1, right-left+1)
    box = crop[top:bottom+1, left:right+1]
    box_flat = box.reshape(-1,3)
    mx = box_flat.max(axis=1)
    mn = box_flat.min(axis=1)
    sat = (mx-mn)/np.maximum(mx,1)
    dark_share = float((mx < 78).mean())
    neutral_share = float((sat < .18).mean())
    return {
        'mask': mask,
        'bbox_ratio': float(mask.mean()),
        'aspect': ww / max(1, hh),
        'dark_share': dark_share,
        'neutral_share': neutral_share,
    }




def _largest_foreground(image):
    """Find a likely central object on a mostly plain background.

    Returns (cropped-image, foreground-pixels, segmentation-reliable).
    Avoids picking colors from the tabletop when the object is black.
    A heuristic; complicated backgrounds are marked uncertain.
    """
    import numpy as np
    from PIL import ImageOps
    source = ImageOps.exif_transpose(image).convert('RGB')
    im = source.copy()
    im.thumbnail((320, 320))
    rgb = np.asarray(im, dtype=np.float32)
    h, w = rgb.shape[:2]
    if h < 30 or w < 30:
        return image, rgb.reshape(-1, 3), False
    pad = max(2, round(min(h, w)*.075))
    edges = np.concatenate([rgb[:pad,:,:].reshape(-1, 3),
                            rgb[-pad:,:,:].reshape(-1, 3),
                            rgb[:,:pad,:].reshape(-1, 3),
                            rgb[:,-pad:,:].reshape(-1, 3)], axis=0)
    base = np.median(edges, axis=0)
    edge_dist = np.linalg.norm(edges-base, axis=1)
    # If edges contain clutter, avoid over-confident segmentation.
    edge_noise = float(np.percentile(edge_dist, 55))
    threshold = max(42., min(90., edge_noise * 1.18 + 20.))
    d = np.linalg.norm(rgb-base, axis=2)
    mask = (d > threshold)
    # Remove border connected parts, which commonly represent table edges or shadows.
    work = mask.copy()
    work[0,:] = False;work[-1,:] = False;work[:,0] = False;work[:,-1] = False
    seen = np.zeros(work.shape, dtype=np.uint8)
    components = []
    for y in range(1, h-1):
        for x in range(1, w-1):
            if not work[y,x] or seen[y,x]:
                continue
            q=[(y,x)];seen[y,x]=1
            points=[]
            ys=[y,y];xs=[x,x]
            while q:
                cy,cx=q.pop()
                points.append((cy,cx))
                if cy<ys[0]:ys[0]=cy
                if cy>ys[1]:ys[1]=cy
                if cx<xs[0]:xs[0]=cx
                if cx>xs[1]:xs[1]=cx
                for ny,nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                    if 0<ny<h-1 and 0<nx<w-1 and work[ny,nx] and not seen[ny,nx]:
                        seen[ny,nx]=1;q.append((ny,nx))
            if len(points) > h*w*0.009:
                cy=(ys[0]+ys[1])/2;cx=(xs[0]+xs[1])/2
                distance=((cy-h/2)/(h/2))**2 + ((cx-w/2)/(w/2))**2
                score=len(points)*max(.45, 1-.18*distance)
                components.append((score, ys, xs, points))
    if not components:
        # No reliable isolated object. Return a central crop with uncertainty.
        return image, rgb[int(.23*h):int(.77*h),int(.23*w):int(.77*w)].reshape(-1,3), False
    _, ys, xs, points = max(components, key=lambda t:t[0])
    y0,y1=ys;x0,x1=xs
    height=y1-y0+1;width=x1-x0+1
    gap=max(4,int(max(height,width)*.10))
    left=max(0,x0-gap);top=max(0,y0-gap);right=min(w,x1+gap+1);bottom=min(h,y1+gap+1)
    # Rescaled crop is only used for item identification, not to overwrite photos.
    # Use the ORIGINAL high-detail pixels for CLIP; the 320px mask is only
    # used to find the object bounds. Do not send a tiny resized crop to AI.
    sx=source.width/w; sy=source.height/h
    crop=source.crop((int(left*sx),int(top*sy),min(source.width,int(right*sx)),min(source.height,int(bottom*sy))))
    pixel_mask=np.zeros((h,w),dtype=np.bool_)
    for cy,cx in points:pixel_mask[cy,cx]=True
    # Include internal lighter elements (screen, logo) inside the tight object area.
    inside=rgb[y0:y1+1,x0:x1+1]
    local=inside.reshape(-1,3)
    if len(local)>1:
        local_distance=np.linalg.norm(local-base,axis=1)
        local=local[local_distance>threshold*.60]
    if len(local)<20:
        local=rgb[pixel_mask]
    area=len(points)/(h*w)
    reliable = area>.025 and (edge_noise<95 or area>.06) and width>=9 and height>=9
    return crop, local, bool(reliable)



def _square_letterbox(image):
    """Fit FULL image into a square without cropping off any side.

    CLIP's default preprocessing uses a center crop. Padding before its
    preprocessor avoids silently discarding the sides of a tall phone photo.
    """
    from PIL import Image, ImageOps
    im=ImageOps.exif_transpose(image).convert('RGB')
    fitted=ImageOps.contain(im,(448,448),method=Image.Resampling.LANCZOS)
    # Plain gray margin avoids introducing a strong table/background color.
    canvas=Image.new('RGB',(448,448),(226,228,229))
    canvas.paste(fitted,((448-fitted.width)//2,(448-fitted.height)//2))
    return canvas

def identify_item(image):
    """Classify from the full view AND an automatic foreground close-up.

    Scores are relative model suggestions, not calibrated probabilities.
    """
    import torch
    crop, _, reliable = _largest_foreground(image)
    with _MODEL_LOCK:
        model,processor = _model()
        # Two views let a closed phone occupy most of the model's attention.
        images = [_square_letterbox(image), _square_letterbox(crop)] if reliable else [_square_letterbox(image)]
        inp = processor(text=PROMPTS, images=images, return_tensors='pt',padding=True,truncation=True)
        with torch.inference_mode():
            logits = model(**inp).logits_per_image.float()
    if len(images)>1:
        similarity = .42*logits[0] + .58*logits[1]
    else:
        similarity = logits[0]
    by_category=defaultdict(list)
    for owner,score in zip(PROMPT_OWNERS, similarity.tolist()):
        by_category[owner].append(float(score))
    ranked=[]
    for name,vals in by_category.items():
        vals=sorted(vals, reverse=True)
        # Extra prompts add coverage, without multiplying the class probability.
        score=vals[0]*.78 + sum(vals[:min(2,len(vals))])/min(2,len(vals))*.22
        ranked.append((name,score))
    ranked.sort(key=lambda pair:pair[1], reverse=True)
    name, top=ranked[0]
    second=ranked[1][1] if len(ranked)>1 else top
    # A close phone/AirPods-case call is genuinely uncertain; label for review.
    uncertain=(top-second<.85) or not reliable
    alternatives=[{'item': n,'score':round(float(score),2)} for n,score in ranked[:4]]
    # Prefer phone when the focused visual close-up agrees and scores are nearly tied.
    if name in {'AirPods','Phone case','Wallet','Small box','Power bank'}:
        phone=next((v for k,v in ranked if k=='Phone'), float('-inf'))
        # Only nudge borderline cases, never force every dark box to be a phone.
        if 0 <= top-phone<.45 and reliable:
            name='Phone';uncertain=True
    return name,uncertain,alternatives

COLOR_HEX = {
    'blue':'#235bd5','black':'#151c28','silver':'#7c899b','white':'#f5f8ff','red':'#d33449',
    'green':'#258557','gray':'#68778c','pink':'#c43c86','brown':'#935c3d','gold':'#a17a17',
    'yellow':'#b58b00','purple':'#8046bb','orange':'#d86f24','beige':'#857153','navy':'#153d75'
}





def _color_name(rgb):
    """Broad readable color family; continuous exact RGB is kept separately."""
    import colorsys
    r,g,b = [float(c)/255.0 for c in rgb]
    h,s,v = colorsys.rgb_to_hsv(r,g,b)
    if v < .31: return 'black'
    if s < .28:
        if v >= .83: return 'white'
        if v >= .66: return 'light gray'
        if v >= .47: return 'gray'
        return 'charcoal'
    # Near-neutral warm pale materials
    if s < .29 and v > .77 and .08 < h < .16: return 'cream'
    if s < .30 and .07 < h < .14 and v > .49: return 'beige'
    deg=h*360.0
    if deg < 12 or deg>=348:
        return 'maroon' if v<.51 else 'red'
    if deg < 20:return 'coral' if v>.72 else 'red'
    if deg < 44:return 'brown' if v<.62 else 'orange'
    if deg < 65:return 'gold' if (v<.78 or s<.55) else 'yellow'
    if deg < 83:return 'olive' if v<.64 else 'lime'
    if deg < 158:return 'olive' if v<.42 else 'green'
    if deg < 187:return 'teal' if v<.63 else 'turquoise'
    if deg < 204:return 'teal' if v<.52 else 'cyan'
    if deg < 253:return 'navy' if v<.48 else 'blue'
    if deg < 283:return 'purple' if v<.64 else 'lavender'
    if deg < 318:return 'purple' if v<.53 else 'magenta'
    return 'burgundy' if v<.50 else 'pink'


def _median_hex(pixels):
    import numpy as np
    sample=np.asarray(pixels, dtype=np.float32)
    med=np.median(sample,axis=0).clip(0,255).astype('uint8')
    return '#%02x%02x%02x' % tuple(int(x) for x in med)


def identify_color(image):
    """Estimate the object's colors, not a fixed palette or the tabletop.

    Returns a broad label for matching PLUS the dominant actual CSS color.
    Color is estimated from object pixels, so material (silver vs gray)
    and lighting can remain ambiguous; never invent precision.
    """
    import numpy as np
    _, pixels, reliable = _largest_foreground(image)
    if len(pixels) < 20: return '', True, [], '', []
    pixels=np.asarray(pixels,dtype=np.float32)
    # If a large light tabletop was mistakenly segmented as foreground,
    # rescue a distinctly darker, centrally located object (e.g. closed phone).
    from PIL import ImageOps
    sample=ImageOps.exif_transpose(image).convert('RGB').copy()
    sample.thumbnail((320,320))
    a=np.asarray(sample,dtype=np.float32); h,w=a.shape[:2]
    pad=max(2,round(min(h,w)*.075))
    midx=slice(int(w*.16),int(w*.84));midy=slice(int(h*.16),int(h*.84))
    rim=np.concatenate([a[:pad,midx].reshape(-1,3),a[-pad:,midx].reshape(-1,3),
                        a[midy,:pad].reshape(-1,3),a[midy,-pad:].reshape(-1,3)],axis=0)
    rim_brightness=float(np.median(np.mean(rim,axis=1)))
    central=a[int(h*.10):int(h*.90),int(w*.10):int(w*.90)]
    bright=central.max(axis=2)
    dark=(bright<min(125,rim_brightness-48))
    dark_fraction=float(dark.mean())
    if rim_brightness>160 and .10<dark_fraction<.46:
        chosen=central[dark]
        # Exclude isolated specular holes, keep truly dark surfaces only.
        if len(chosen)>400 and np.percentile(chosen.max(axis=1),80)<123:
            pixels=chosen
    # One neutral gray highlight should not turn a primarily black phone orange.
    pixels=pixels[::max(1,len(pixels)//30000)]
    brightness=pixels.max(axis=1)
    families=[_color_name(rgb) for rgb in pixels]
    counter=Counter(families)
    total=sum(counter.values())
    ranked=[(name,count/total) for name,count in counter.most_common()]
    top,fraction=ranked[0]
    candidates={name:pixels[[j for j,n in enumerate(families) if n==name]] for name,_ in ranked[:4]}
    all_palette=[(name,frac,_median_hex(candidates[name])) for name,frac in ranked[:4]]
    # Flip-phone screens, reflection spots and highlights are often small
    # regions. Require meaningful color area before calling an item patterned.
    chromatic={'red','coral','orange','yellow','gold','green','olive','lime','teal','turquoise','cyan',
               'navy','blue','purple','lavender','magenta','pink','maroon','burgundy','brown'}
    active=[t for t in all_palette if t[1]>=.20 and t[0] in chromatic]
    if len(active)>=2 and sum(t[1] for t in active)>=.56:
        palette=active[:3]
        # Multicolor lettering uses actual found colors, not a fixed rainbow.
        return 'multicolor', not reliable, [a for a,_,_ in palette], '#46566d', [hx for _,_,hx in palette]
    earth={'green','olive','brown','beige','charcoal','black','gray','navy'}
    camo=[t for t in all_palette if t[1]>=.17 and t[0] in earth]
    if len(camo)>=3 and top not in ('black','white'):
        return 'camouflage', not reliable, [a for a,_,_ in camo[:3]], '#46566d', [hx for _,_,hx in camo[:3]]
    # White, light gray and silver can look similar in different lighting.
    # The less certain metallic 'silver' claim requires a broad spread of
    # neutral highlights; otherwise say light gray rather than guessing metal.
    neutral = top in ('white','light gray','gray','charcoal')
    use=candidates[top]
    shade=_median_hex(use)
    if top in ('white','light gray','gray','charcoal'):
        # Neutral surfaces photographed under warm office light should still
        # display as neutral colored letters, not orange/yellow.
        lum=int(np.median(np.mean(use,axis=1)).clip(0,255))
        shade='#%02x%02x%02x' % (lum,lum,lum)
    if top=='light gray':
        lum=np.mean(use,axis=1)
        hi=float((lum>225).mean())
        lo=float((lum<145).mean())
        if hi>.20 and lo>.08:top='silver'
    if top=='gray' and float((brightness>227).mean())>.18 and fraction>.5:
        top='silver'
    # Very pale letters need a dark outline in the dashboard to stay readable.
    return top, (not reliable or fraction<.40), [top], shade, [shade]

def analyze(path: Path) -> dict:
    from PIL import Image, ImageOps
    with Image.open(path) as raw:
        if raw.width*raw.height>36_000_000:
            raise ValueError('Photo is too large to analyze. Try a smaller photo.')
        image=ImageOps.exif_transpose(raw).convert('RGB')
        # The PHOTO remains intact; the model is fed the full view and a sharp
        # automatically focused crop, while the dashboard keeps square thumbnails.
        image.thumbnail((1800,1800))
        name, item_uncertain, alternatives=identify_item(image)
        from second_opinion import describe, resolve_candidates
        from reference_gallery import gallery_vote
        caption, caption_item=describe(image)
        bank_item=gallery_vote(image)
        name, decision_uncertain, decision_note=resolve_candidates(name,item_uncertain,caption_item,bank_item)
        item_uncertain=item_uncertain or decision_uncertain
        color, color_uncertain, palette, color_hex, palette_hex=identify_color(image)
    return {
        'item':name,
        'color':color,
        'color_palette': palette,
        'color_hex': color_hex, 'color_palette_hex': palette_hex,
        'recognition_state':'review' if (item_uncertain or color_uncertain) else 'suggested',
        'suggestions':alternatives, 'caption':caption, 'visual_decision':decision_note,
        'recognition_note':(decision_note + '. Photo and color guesses should be reviewed before returning property.')
    }
