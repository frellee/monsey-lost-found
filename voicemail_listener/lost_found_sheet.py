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
    'Phone': [r'(?:cell[ -]?)?phones?', r'\bטעלעפאן\b', r'\bטעלעפֿאָן\b'],
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
    'Tefillin': [r'tefillin', r'tfillin', r'\bתפילין\b'],
    'Tallis': [r'tallis', r'tallit', r'\bטלית\b', r'\bטליס\b'],
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
    p=str(entry.get('phone') or '')
    if PHONE_PATTERN.search(p):
        return PHONE_PATTERN.search(p).group().strip()
    t=str(entry.get('transcript') or '')
    m=PHONE_PATTERN.search(t)
    return m.group().strip() if m else ''

def _safe(value):
    return html.escape(str(value or ''),quote=True)

def _record_id(e):
    seed=str(e.get('filename') or '')+'|'+str(e.get('date') or '')
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]

def build_rows(entries):
    rows=[]
    for e in entries:
        if not isinstance(e,dict):
            continue
        text=str(e.get('transcript') or '')
        hits=_item_hits(text)
        if not hits:
            # Still show the caller as unclassified, so no lost report disappears.
            hits=[(0,0,'Item not recognized')]
        seen={}
        for a,b,label in hits:
            seen[label]=seen.get(label,0)+1
            # Keep original per-item ID for the first occurrence to preserve existing statuses;
            # additional occurrences get stable suffixes.
            occurrence='-'+str(seen[label]) if seen[label]>1 else ''
            rows.append({
                'id':_record_id(e)+'-'+re.sub(r'[^a-z0-9]','',label.lower())+occurrence,
                'item':label,
                'color':_color_for(text,a,b,hits) if b>a else None,
                'name':_name(text) or 'Not stated',
                'phone':_phone(e) or 'Not recognized',
                'date':str(e.get('date') or ''),
                'snippet':text,
                'filename':str(e.get('filename') or ''),
                'flagged':bool(e.get('flagged')),
            })
    return rows

def _read_saved_transcripts(path):
    """Conservative fallback for messages missing from the JSON history."""
    if not path.is_file():
        return []
    try:
        raw=path.read_text(encoding='utf-8-sig')
    except (UnicodeDecodeError,OSError) as exc:
        LOG.warning('Could not read transcript history: %s',exc)
        return []
    found=[]
    parts=re.split(r'(?m)^---\s+(.+?)\s+---\s*$',raw)
    for i in range(1,len(parts)-1,2):
        stamp,chunk=parts[i].strip(),parts[i+1]
        name=re.search(r'(?m)^Attachment:\s*(.+)$',chunk)
        content=re.search(r'(?ms)^Transcript:\s*(.*?)(?=^---\s+|\Z)',chunk)
        if not name or not content:continue
        found.append(dict(date=stamp,filename=name.group(1).strip(),transcript=content.group(1).strip(),phone='',flagged=True))
    return found

def _read_saved_html(path):
    """Last-resort recovery if JSON was removed; don't parse generated JS."""
    if not path.is_file():return []
    try:raw=path.read_text(encoding='utf-8-sig')
    except (OSError,UnicodeDecodeError):return []
    found=[]
    for part in re.findall(r'(?is)<article\b[^>]*class=["\']card["\'][^>]*>(.*?)</article>',raw):
        date=re.search(r'(?is)<time[^>]*>(.*?)</time>',part)
        transcript=re.search(r'(?is)<div[^>]*class=["\']transcript["\'][^>]*>(.*?)</div>',part)
        if not transcript:continue
        from html import unescape
        clean=lambda value:unescape(re.sub(r'<[^>]*>','',value or '')).strip()
        filenames=re.findall(r'(?i)vm_[^<\s]+\.mp3',part)
        if not filenames:continue
        found.append(dict(date=clean(date.group(1) if date else ''),filename=clean(filenames[-1]),transcript=clean(transcript.group(1)),phone='',flagged=True))
    return found

def _load_entries(root):
    src=root/'voicemail_messages.json'
    entries=[]
    if src.exists():
        try:
            saved=json.loads(src.read_text(encoding='utf-8-sig'))
            if isinstance(saved,list):entries.extend(e for e in saved if isinstance(e,dict))
            else:LOG.warning('voicemail_messages.json is not a list; reading fallbacks')
        except (OSError,ValueError) as exc:
            LOG.warning('Could not read voicemail JSON; reading fallbacks: %s',exc)
    keys=set()
    def key(e):
        file=str(e.get('filename') or '').strip()
        return file.lower() or str(e.get('date') or '')+'|'+str(e.get('transcript') or '')[:40]
    for e in entries:keys.add(key(e))
    # Supplement the JSON from the original, non-destructive saved history.
    for source in (_read_saved_transcripts(root/'transcripts.txt'),_read_saved_html(root/'voicemail_messages.html')):
        for e in source:
            k=key(e)
            if k and k not in keys:
                entries.append(e);keys.add(k)
    return entries

def rebuild(root):
    root=Path(root)
    out=root/'lost_found_items.html'
    entries=_load_entries(root)
    # Do not replace a previously populated page with an empty sheet.
    if not entries and out.is_file():
        raise RuntimeError('No saved transcripts found; existing sheet was left unchanged')
    rows=build_rows(entries)
    # Escape before embedding into a script tag: audio transcripts are untrusted.
    payload=json.dumps(rows,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    output=HTML_TEMPLATE.replace('/*ROWS_JSON*/[]',payload)
    if output==HTML_TEMPLATE or 'const DATA=' not in output:
        raise RuntimeError('Could not insert rows into HTML')
    dest=out.with_suffix('.html.tmp')
    dest.write_text(output,encoding='utf-8')
    dest.replace(out)
    voicemail_page=root/'voicemail_messages.html'
    if voicemail_page.is_file():
        try:
            source=voicemail_page.read_text(encoding='utf-8-sig')
            if 'data-lf-items-nav="v1"' not in source:
                match=re.search(r'<body\b[^>]*>',source,re.I)
                if match:
                    link = ('<div data-lf-items-nav="v1" style="max-width:860px;margin:14px auto 4px;padding:0 16px">'
                            '<a href="lost_found_items.html" style="display:inline-block;padding:12px 18px;'
                            'background:#153e69;color:white;border-radius:12px;text-decoration:none;'
                            'font:700 15px Segoe UI,Arial,sans-serif">Open Lost &amp; Found Item Sheet</a></div>')
                    patched=source[:match.end()]+link+source[match.end():]
                    scratch=voicemail_page.with_suffix('.html.tmp')
                    scratch.write_text(patched,encoding='utf-8')
                    scratch.replace(voicemail_page)
        except OSError as exc:
            LOG.warning('Could not update page link: %s',exc)
    return out,len(rows)

HTML_TEMPLATE=r'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monsey Trails | Lost & Found Items</title>
<style>
:root{font-family:Segoe UI,Arial,sans-serif;color:#1b2939;background:#f4f7fb}*{box-sizing:border-box}
body{margin:0}.wrap{max-width:1210px;padding:32px 22px;margin:auto}
header{display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap;margin-bottom:22px}
.eyebrow{font-size:12px;letter-spacing:.16em;font-weight:800;color:#72839a;text-transform:uppercase}
h1{font-size:31px;line-height:1.1;margin:6px 0 5px;color:#162a44}p.sub{margin:0;color:#63748a;font-size:14px}
.link{padding:12px 16px;background:white;border:1px solid #d6e1ee;border-radius:10px;text-decoration:none;color:#1b436c;font-weight:700}
.stats{display:flex;gap:14px;margin:22px 0;flex-wrap:wrap}.stat{background:white;border:1px solid #e2e9f2;border-radius:14px;padding:17px 23px;min-width:155px;box-shadow:0 2px 7px #18263805}.stat strong{display:block;font-size:28px;color:#1e3552}.stat span{font-size:12px;color:#697e94}
.main{background:#fff;border:1px solid #dfe8f2;box-shadow:0 5px 28px #122e4a0b;border-radius:18px;overflow:visible}
.toolbar{position:sticky;top:0;z-index:10;padding:19px 21px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #e8eef5;background:white}
.toolbar input,.toolbar select{font:inherit;padding:12px 13px;border-radius:9px;border:1px solid #d4deea;background:#fff;color:#24384b}.toolbar input{flex:1;min-width:200px;font-size:16px} .toolbar input:focus{outline:3px solid #b9d9fa;border-color:#2a6eb0}
.search-label{font-weight:800;color:#234b73} .search-count{font-size:12px;color:#61758d;white-space:nowrap}
.toolbar-btn{cursor:pointer;border:1px solid #cbd8e6;border-radius:10px;background:#f5f8fc;color:#234765;padding:11px 12px;font-weight:700}
.toolbar-btn:hover{background:#e5effa}.row-btn{cursor:pointer;border:1px solid #ead4d4;background:#fff9f9;color:#a33434;border-radius:8px;padding:9px 12px;font-weight:750}.row-btn:hover{background:#ffe7e7}
.restore-btn{border-color:#c7decd;background:#f3fff5;color:#237148}.restore-btn:hover{background:#dcf9e5}
.deleted-switch{display:flex;align-items:center;gap:5px;font-size:13px;font-weight:650;color:#48627e;white-space:nowrap}
.deleted-switch input{min-width:unset;flex:unset;accent-color:#2e6197}
.undo{border:1px solid #cbdfea;background:#f1f7fc;padding:11px 18px;color:#2b567a;font-size:14px;display:none;gap:12px;align-items:center;justify-content:space-between}
.undo button{cursor:pointer;padding:7px 11px;border-radius:8px;border:1px solid #759fc1;background:#fff;color:#215485;font-weight:750}
.actions{text-align:right}.sr-note{font-size:12px;color:#65798f}

.tablewrap{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:760px}th{text-align:left;background:#f7f9fc;color:#536b86;font-size:12px;letter-spacing:.06em;text-transform:uppercase;padding:16px 20px}
td{padding:17px 20px;border-top:1px solid #edf1f6;font-size:14px;vertical-align:middle}tr:hover td{background:#fafcff}
.itemword{font-weight:850;font-size:21px;line-height:1.25;display:inline-block;word-break:break-word}
.normal{color:#233953}.hint{font-size:11px;color:#8999a9;margin-top:5px}.muted{color:#8593a3}
.customer{font-weight:650;font-size:14px}.phone{color:#31577d;font-weight:650;font-variant-numeric:tabular-nums;margin-top:4px;white-space:nowrap}
.status-select{border-radius:9px;border:1px solid #d7e5f2;background:#f0f6ff;font-weight:700;color:#295887;font-size:13px;padding:9px 8px;max-width:135px}
.date{color:#65798f;white-space:nowrap}.detail{font-size:12px;margin-top:7px}.detail summary{cursor:pointer;color:#286399;font-weight:650}.detail div{max-width:400px;max-height:110px;overflow:auto;margin-top:9px;background:#f8fafc;border-radius:8px;padding:10px;white-space:pre-wrap}
.note{padding:16px 21px;color:#718196;font-size:12px;border-top:1px solid #e8eef5;line-height:1.65}
.empty{padding:35px 20px;text-align:center;color:#61758d}button{font:inherit}
@media(max-width:680px){.wrap{padding:20px 10px}h1{font-size:24px}.stat{flex:1;min-width:100px;padding:12px}td,th{padding:12px}.itemword{font-size:18px}}
</style></head><body><div class="wrap">
<header><div><div class="eyebrow">Monsey Trails · Voicemail system</div><h1>Lost & Found Items</h1><p class="sub">Item names appear in the color the customer actually mentioned.</p></div><a class="link" href="voicemail_messages.html">View voicemail transcripts</a></header>
<div class="stats"><div class="stat"><strong id="total">0</strong><span>Item entries</span></div><div class="stat"><strong id="calls">0</strong><span>Voicemails</span></div><div class="stat"><strong id="needs">0</strong><span>Needs review</span></div></div>
<div class="main"><div class="toolbar"><label for="search" class="search-label">Search</label><input id="search" type="search" autocomplete="off" placeholder="Item, customer, phone, date, Yiddish…" aria-label="Search Lost and Found rows"><button type="button" id="clear-search" class="toolbar-btn">Clear</button><select id="filter" aria-label="Filter by status"><option value="all">All statuses</option><option>New</option><option>Looking</option><option>Found</option><option>Matched</option><option>Returned</option><option>Needs review</option></select><label class="deleted-switch"><input type="checkbox" id="show-deleted"> Deleted rows</label><span id="search-count" class="search-count" role="status" aria-live="polite"></span></div><div class="undo" id="undo-box" role="status">Row removed from sheet; original voicemail kept. <button type="button" id="undo-button">Undo delete</button></div><div class="tablewrap"><table><thead><tr><th>Item</th><th>Customer / Phone</th><th>Reported</th><th>Status</th><th>Message</th><th class="actions">Action</th></tr></thead><tbody id="rows"></tbody></table></div><div class="note">The item's color is shown only when explicitly mentioned close to that item; the color name is not displayed. Names, phone numbers and item recognition can contain speech-recognition mistakes. Status changes and deleted-row choices are saved only in this browser. Delete removes one row from this sheet, even after refresh, but never deletes the source voicemail, email, or database. Use the Deleted rows checkbox to restore a row. No information is uploaded online.</div></div>
</div><script>
const DATA=/*ROWS_JSON*/[];
const COLOR={blue:'#2458D3',black:'#111827',silver:'#68798E',red:'#CC2F3D',green:'#14834E',yellow:'#956800',orange:'#B85512',purple:'#8744BB',pink:'#C43B75',brown:'#855127',gray:'#6C7482',white:'#475569',gold:'#947000',beige:'#876E4A',navy:'#173567'};
const STATUSES=['New','Looking','Found','Matched','Returned','Needs review'];
const DELETE_KEY='mtlf:deleted-items:v1';
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const getStatus=r=>{try{return localStorage.getItem('lfstatus:'+r.id)|| (r.item==='Item not recognized'?'Needs review':'New')}catch(e){return r.item==='Item not recognized'?'Needs review':'New'}};
const readDeleted=()=>{try{let value=JSON.parse(localStorage.getItem(DELETE_KEY)||'[]');return new Set(Array.isArray(value)?value:[])}catch(e){return new Set()}};
let deleted=readDeleted();
let lastDeleted=null;
function saveDeleted(next){
  try{localStorage.setItem(DELETE_KEY,JSON.stringify([...next]));deleted=next;return true}
  catch(e){alert('Chrome could not save this change. No row was deleted. Please check browser storage settings.');return false}
}
const index=DATA.reduce((m,r)=>{m[r.id]=r;return m},{});
function searchable(r){return [r.item,r.color||'',r.name,r.phone,r.date,r.filename,r.snippet,getStatus(r)].join(' ').toLocaleLowerCase()}
function searchableFields(r){return [r.item,r.color||'',r.name,r.phone,r.date,r.filename,getStatus(r)].join(' ').toLocaleLowerCase()}
function matchesText(s,q){return q.split(/\s+/u).filter(Boolean).every(t=>s.includes(t))}
function render(){
 const q=$('search').value.toLocaleLowerCase().trim();const f=$('filter').value;const showDeleted=$('show-deleted').checked;
 const available=DATA.filter(r=>deleted.has(r.id)===showDeleted);
 const eligible=available.filter(r=>(f==='all'||getStatus(r)===f));
 let list=eligible.filter(r=>matchesText(searchableFields(r),q));
 if(q && !list.length) list=eligible.filter(r=>matchesText(searchable(r),q));
 const visible=DATA.filter(r=>!deleted.has(r.id));
 $('total').textContent=visible.length;
 $('calls').textContent=new Set(visible.map(r=>r.filename+'|'+r.date)).size;
 $('needs').textContent=visible.filter(r=>getStatus(r)==='Needs review').length;
 $('search-count').textContent=`${list.length} of ${available.length} ${showDeleted?'deleted':'active'} rows`;
 $('rows').innerHTML=list.length ? list.map(r=>{
  const s=getStatus(r),color=COLOR[r.color]||'#233953';
  const opts=STATUSES.map(x=>`<option ${x===s?'selected':''}>${esc(x)}</option>`).join('');
  const rid=esc(r.id);
  const action=showDeleted?`<button type="button" class="row-btn restore-btn" data-action="restore" data-id="${rid}">Restore</button>`:`<button type="button" class="row-btn" data-action="delete" data-id="${rid}" aria-label="Delete ${esc(r.item)} row">Delete</button>`;
  return `<tr><td><span class="itemword" style="color:${color}">${esc(r.item)}</span></td><td><div class="customer">${esc(r.name)}</div><div class="phone">${esc(r.phone)}</div></td><td class="date">${esc(r.date)}</td><td><select class="status-select" data-id="${rid}">${opts}</select></td><td><details class="detail"><summary>View details</summary><div>${esc(r.snippet)}\n\n${esc(r.filename)}</div></details></td><td class="actions">${action}</td></tr>`;
 }).join(''):`<tr><td colspan="6" class="empty">${showDeleted?'No deleted rows match.':'No matching items. Try another search or status.'}</td></tr>`;
 $('rows').querySelectorAll('.status-select').forEach(el=>el.addEventListener('change',()=>{
  try{localStorage.setItem('lfstatus:'+el.dataset.id,el.value)}catch(e){alert('Could not save status in this browser.')};render();
 }));
 $('rows').querySelectorAll('button[data-action]').forEach(el=>el.addEventListener('click',()=>{
  const id=el.dataset.id,r=index[id];if(!r)return;
  if(el.dataset.action==='delete'){
   if(!confirm(`Delete the ${r.item} row from this Lost & Found sheet?\n\nThe original voicemail and saved transcript will NOT be deleted.`))return;
   const next=new Set(deleted);next.add(id);
   if(saveDeleted(next)){lastDeleted=id;$('undo-box').style.display='flex';render()}
  }else{
   const next=new Set(deleted);next.delete(id);if(saveDeleted(next)){lastDeleted=null;$('undo-box').style.display='none';render()}
  }
 }));
}
$('search').addEventListener('input',render);
$('filter').addEventListener('change',render);
$('show-deleted').addEventListener('change',()=>{$('undo-box').style.display='none';render()});
$('clear-search').addEventListener('click',()=>{$('search').value='';$('filter').value='all';$('search').focus();render()});
$('undo-button').addEventListener('click',()=>{
 if(!lastDeleted)return;const next=new Set(deleted);next.delete(lastDeleted);
 if(saveDeleted(next)){lastDeleted=null;$('undo-box').style.display='none';$('show-deleted').checked=false;render()}
});
render();
</script></body></html>'''

if __name__=='__main__':
    import sys
    dest,count=rebuild(sys.argv[1] if len(sys.argv)>1 else Path(__file__).resolve().parent)
    print('Updated:',dest, 'Items:',count)
