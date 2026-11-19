"""Offline repaired item table with search, colors and reversible row deletion."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from pathlib import Path

LOG = logging.getLogger(__name__)

# Deliberately conservative, editable collection of common items and loanwords.
# Do not infer an item's color from its type.
ITEMS = {
    'Coat': [r'coats?', r'overcoats?', r'\bמאנטל\b', r'\bמאַנטל\b', r'\bקאט\b'],
    'Pants': [r'pants?', r'trousers?', r'\bהויזן\b', r'\bהױזן\b'],
    'Socks': [r'socks?', r'\bזאקן\b', r'\bזאָקן\b'],
    'Shirt': [r'shirts?', r'\bהעמד\b'],
    'Jacket': [r'jackets?', r'\bדזשאקעט\b', r'\bזשאקעט\b', r'\bדזשאַקעט\b'],
    'Wallet': [r'wallets?', r'\bוואלעט\b', r'\bװאלעט\b', r'\bבערזל\b'],
    'iPad': [r'i[ -]?pads?', r'\bאייפאד\b', r'\bאייפעד\b'],
    'AirPods': [r'air[ -]?pods?', r'ear[ -]?pods?', r'\bאיירפאדס\b', r'\bאיירפודס\b'],
    'iPhone': [r'i[ -]?phones?', r'\bאייפאון\b', r'\bאייפון\b'],
    'Phone': [r'(?:cell[ -]?|smart[ -]?)?phones?(?!\s*(?:numbers?|#|no\b|line)|\s+(?:is\s+)?[\d(+])', r'\bטעלעפאן\b', r'\bטעלעפֿאָן\b'],
    'Siddur': [r'siddur', r'sidur', r'prayer[ -]?books?', r'\bסידור\b'],
    'Stapler': [r'staplers?'],
    'Pen': [r'pens?'],
    'Alarm clock': [r'alarm[ -]?clocks?'],
    'Earrings': [r'ear[ -]?rings?', r'stud earrings?'],
    'Power bank': [r'power[ -]?banks?'],
    'Headphones': [r'head[ -]?phones?', r'on[ -]ear (?:head)?phones?'],
    'Backpack': [r'backpacks?', r'\bרוקזאק\b', r'\bרוקזאַק\b'],
    'Bag': [r'handbags?', r'\bbags?', r'\bבעג\b', r'\bטאש\b', r'\bטאַש\b'],
    'Suitcase': [r'suitcases?', r'luggage', r'\bסוטקעיס\b', r'\bוואליז\b', r'\bװאַליז\b'],
    'Carriage': [r'carriages?', r'strollers?', r'\bקערידזש\b', r'\bקארעטש\b'],
    'Plastic cover': [r'plastic[ -]?covers?', r'\bפלאסטיק[- ]קאווער\b'],
    'Cover': [r'covers?', r'\bקאווער\b'],
    'Jewelry': [r'jewell?ery', r'\bדזשולערי\b', r'\bדזשעוועלרי\b', r'\bצירונגען\b'],
    'Watch': [r'watches?', r'\bוואטש\b', r'\bזייגער\b'],
    'Glasses': [r'(?:sun)?glasses', r'\bברילן\b'],
    'Hat': [r'\bhats?', r'\bהוט\b', r'\bהיט\b'],
    'Hat box': [r'hat[ -]?boxes?', r'\bהוט[- ]באקס\b'],
    'Scarf': [r'scarves?', r'\bשאל\b', r'\bשאַל\b'],
    'Shawl': [r'shawls?', r'\bשאול\b', r'\bשאָל\b'],
    'Shoes': [r'shoes?', r'\bשיך\b'],
    'Tefillin bag': [r'tefill?in[ -]?bags?', r'tfillin[ -]?bags?'],
    'Tallis bag': [r'tall?is[ -]?bags?', r'tallit[ -]?bags?'],
    'Tefillin': [r'tefillin', r'tfillin', r'\bתפילין\b'],
    'Tallis': [r'tall?is', r'tallit', r'tallith', r'talith', r'talles', r'taleis', r'\bטלית\b', r'\bטליס\b', r'\bטאלעס\b'],
    'Keys': [r'keys?', r'\bשליסל\b', r'\bשליסלעך\b'],
    'Car key': [r'car[ -]?keys?', r'\bקאר[- ]שליסל\b'],
    'Credit card': [r'credit[ -]?cards?', r'\bקרעדיט[- ]קארד\b'],
    'Passport': [r'passports?', r'\bפאספארט\b'],
    'Picture': [r'pictures?', r'photos?', r'\bבילד\b', r'\bפיקטשע\b'],
    'Picture frame': [r'picture[ -]?frames?', r'\bפיקטשע[- ]פרעם\b'],
    'Camera': [r'cameras?', r'\bקאמערע\b'],
    'Laptop': [r'laptops?', r'\bלעפטאפ\b', r'\bלאפטאפ\b'],
    'Book': [r'books?', r'\bבוך\b', r'\bספר\b'],
    'Umbrella': [r'umbrellas?', r'\bשירעם\b'],
    'Blanket': [r'blankets?', r'\bקאלדרע\b'],
    'Sweater': [r'sweaters?', r'\bסוועטער\b'],
}
# Higher-priority multiword terms suppress their generic contained item.
MORE_SPECIFIC = {'Hat box':'Hat', 'Picture frame':'Picture','Car key':'Keys',
                 'Plastic cover':'Cover', 'Credit card':'Card', 'Backpack':'Bag',
                 'Handbag':'Bag'}

COLORS = {
    'blue': ('#2458D3', ['blue', 'בלוי', 'בלויע', 'בלויער']),
    'black': ('#111827', ['black', 'שווארץ', 'שוואַרץ', 'שווארצע', 'שוואַרצע']),
    'silver': ('#68798E', ['silver', 'זילבער', 'זילבערן']),
    'red': ('#CC2F3D', ['red', 'רויט', 'רויטע', 'רויטער']),
    'green': ('#14834E', ['green', 'גרין', 'גרינע', 'גרינער']),
    'yellow': ('#956800', ['yellow', 'געל', 'געלע', 'געלער']),
    'orange': ('#B85512', ['orange', 'אראנזש', 'אָראַנזש']),
    'purple': ('#8744BB', ['purple', 'פופער', 'לילא']),
    'pink': ('#C43B75', ['pink', 'פינק', 'ראזעווע', 'ראָזעװע']),
    'brown': ('#855127', ['brown', 'ברוין', 'ברוינע']),
    'gray': ('#6C7482', ['gray', 'grey', 'גרוי', 'גרויע']),
    'white': ('#475569', ['white', 'ווייס', 'װײַס', 'ווייסע', 'װײַסע']),
    'gold': ('#947000', ['gold', 'golden', 'גאלד', 'גאָלד']),
    'beige': ('#876E4A', ['beige', 'בעזש']),
    'navy': ('#173567', ['navy', 'נעיווי']),
}
WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z'-]*|[\u0590-\u05ff]+")
PHONE_PATTERN = re.compile(r'(?<!\d)(?:\+?1[\s.()\-]*)?\(?\d{3}\)?[\s.()\-]*\d{3}[\s.()\-]*\d{4}(?!\d)')
NAME_RE = [
    re.compile(r"\bmy name is\s+([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+){0,2})", re.I),
    re.compile(r"\bthis is\s+([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+){0,2})", re.I),
    re.compile(r"(?:מיין נאמען איז|מײַן נאָמען איז)\s+([\u0590-\u05ff]+(?:\s+[\u0590-\u05ff]+){0,2})"),
]

def _english_part(text):
    """The saved transcript can hold the Yiddish AND its English translation.
    Read items from the English part only, so one spoken item is not counted twice."""
    parts = re.split(r'(?i)\bEnglish:\s*', str(text or ''))
    tail = parts[-1].strip() if len(parts) > 1 else ''
    return tail or str(text or '')

def _subject_name(subject):
    m = re.search(r'message from\s+"([^"(]+?)\s*(?:\(|")', str(subject or ''), re.I)
    return m.group(1).strip().title() if m else ''

def _tokens(text):
    return [dict(word=m.group(), a=m.start(), b=m.end()) for m in WORD_PATTERN.finditer(text)]

def _item_hits(text):
    hits=[]
    for label, variants in ITEMS.items():
        for p in variants:
            # Boundaries are essential: 'hat' must not match 'that'.
            for match in re.finditer(r'(?<![\w\u0590-\u05ff])(?:'+p+r')(?![\w\u0590-\u05ff])', text, flags=re.I):
                hits.append((match.start(), match.end(), label))
    # longest first, then no overlapping duplicates
    hits.sort(key=lambda h:(h[0], -(h[1]-h[0])))
    selected=[]
    for a,b,label in hits:
        if any(a < B and b > A for A,B,_ in selected):
            continue
        selected.append((a,b,label))
    # Do not discard repeated mentions. A caller can report two coats.
    # Each occurrence gets its own row; the caller may delete a repeated mention.
    return selected

def _color_for(text, start, end, hits):
    tokens=_tokens(text)
    overlapping=[i for i,t in enumerate(tokens) if t['a'] < end and t['b'] > start]
    if not overlapping:
        return None
    first,last=overlapping[0],overlapping[-1]
    # A color is only meaningful near the item, not somewhere else in voicemail.
    nearby=[]
    for j,t in enumerate(tokens):
        if j < first-3 or j > last+3 or first <= j <= last:
            continue
        if any(t['a'] < B and t['b'] > A for A,B,_ in hits if A!=start):
            continue
        between=tokens[min(j,last)+1:max(j,first)] if j<first else tokens[last+1:j]
        # Do not borrow color from another item or a distinct clause.
        if any(x['word'].lower() in ('and','און','but','or','אדער') for x in between):
            continue
        key=t['word'].lower()
        for name,(_,variants) in COLORS.items():
            if key in variants:
                distance = (first-j if j<first else j-last)
                nearby.append((distance, name, j))
    if not nearby:
        return None
    nearby.sort(key=lambda x:x[0])
    color=nearby[0][1]
    return color

def _name(text):
    for p in NAME_RE:
        m=p.search(text)
        if m:
            candidate=m.group(1).strip(' .,:;-')
            words=candidate.split()
            # Keep only two names; a claimed name may be uncertain.
            return ' '.join(words[:2]) if words else ''
    return ''

def _phone(entry):
    # Number heard in the voicemail first, then the caller ID from the email subject.
    for key in ('phone','caller_id','subject'):
        m=PHONE_PATTERN.search(str(entry.get(key) or ''))
        if m:
            return m.group().strip()
    m=PHONE_PATTERN.search(_english_part(entry.get('transcript') or ''))
    return m.group().strip() if m else ''

def _safe(value):
    return html.escape(str(value or ''),quote=True)

def _record_id(e):
    seed=str(e.get('filename') or '')+'|'+str(e.get('date') or '')
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]

def _structured_items(entry, items_by_file):
    """Items Gemini listed for this voicemail, or None when there is no saved list."""
    data=(items_by_file or {}).get(str(entry.get('filename') or ''))
    if not isinstance(data,list):
        return None
    out=[]; seen=set()
    for it in data:
        if not isinstance(it,dict):
            continue
        label=re.sub(r'\s+',' ',str(it.get('item') or '')).strip()[:60]
        color=re.sub(r'[^a-z ]','',str(it.get('color') or '').lower()).strip()[:20]
        if not label or (label.lower(),color) in seen:
            continue
        seen.add((label.lower(),color)); out.append((label,color or None))
    return out

def build_rows(entries, items_by_file=None):
    """One row per DIFFERENT item. Rows from the same voicemail share the same 'vm' id."""
    rows=[]
    for e in entries:
        if not isinstance(e,dict):
            continue
        full=str(e.get('transcript') or '')
        text=_english_part(full)
        found=_structured_items(e,items_by_file)
        if found is None:
            hits=_item_hits(text)
            found=[]; seen=set()
            for a,b,label in hits:
                color=_color_for(text,a,b,hits)
                if (label,color) in seen:
                    continue            # same item said twice = one item
                seen.add((label,color)); found.append((label,color))
        if not found:
            found=[('Item not recognized',None)]
        rid=_record_id(e)
        used={}
        name=_name(text) or _name(full) or _subject_name(e.get('subject')) or 'Not stated'
        phone=_phone(e) or 'Not recognized'
        caller=str(e.get('caller_id') or '')
        for label,color in found:
            slug=re.sub(r'[^a-z0-9]','',label.lower()) or 'item'
            used[slug]=used.get(slug,0)+1
            rows.append({
                'id':rid+'-'+slug+('-'+str(used[slug]) if used[slug]>1 else ''),
                'vm':rid,
                'item':label,
                'color':color,
                'name':name,
                'phone':phone,
                'caller_id':caller,
                'date':str(e.get('date') or ''),
                'snippet':full,
                'filename':str(e.get('filename') or ''),
                'flagged':bool(e.get('flagged')),
            })
    return rows
