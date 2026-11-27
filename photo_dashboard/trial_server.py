"""Local-only Monsey Trails Found Items photo trial; stdlib, no internet, no Gmail access."""
from __future__ import annotations

import base64
import hashlib
import json
import queue
import shutil
import traceback
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

from item_parser import build_rows, _english_part
from auto_vision import analyze

HOME = Path(__file__).resolve().parent
DATA = HOME / 'trial_data'
PHOTOS = DATA / 'photos'
STATE = DATA / 'found_records.json'
DASHBOARD = HOME / 'dashboard.html'
MAX_POST = 15 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ANALYSIS_QUEUE = queue.Queue()
LOCK = threading.RLock()
# Gemini uploads are saved immediately; recognition waits for available quota.
# Wait between completed jobs so ten photos don't all hit Google at once.
PHOTO_GAP_SECONDS = max(0, min(300, int(os.environ.get('MONSEY_GEMINI_PHOTO_GAP', '25'))))
WAITING_SCAN_SECONDS = 10



def find_listener():
    downloads = Path.home() / 'Downloads'
    candidates = [
        downloads / 'Free_Intermedia_Voicemail_Listener_Windows' / 'Free_Intermedia_Voicemail_Listener',
        downloads / 'Free_Intermedia_Voicemail_Listener',
        downloads / 'Free_Intermedia_Voicemail_Listener_Windows',
    ]
    custom = os.getenv('MONSEY_LISTENER_FOLDER', '').strip()
    if custom:
        candidates.insert(0, Path(custom))
    for path in candidates:
        if (path / 'voicemail_messages.json').is_file() or (path / 'listener.py').is_file() or (path / 'transcripts.txt').is_file():
            return path
    try:
        for path in downloads.glob('*/Free_Intermedia_Voicemail_Listener'):
            if (path / 'listener.py').is_file() or (path / 'voicemail_messages.json').is_file():
                return path
    except OSError:
        pass
    return None


LISTENER = find_listener()


def migrate_old_trial_data():
    """Bring previous photo records forward if V2 lives beside the old ZIP extraction."""
    if STATE.is_file():
        return
    downloads = Path.home() / 'Downloads'
    candidates = [
        downloads / 'Monsey_Trails_Photo_AI_Trial_v2' / 'trial_data',
        downloads / 'Monsey_Trails_Photo_AI_Trial' / 'trial_data',
        downloads / 'MONSEY_TRAILS_AUTO_PHOTO_FOUND_ITEMS_TRIAL_V2' / 'Monsey_Trails_Photo_AI_Trial_v2' / 'trial_data',
        downloads / 'Monsey_Trails_Photo_Matching_Trial' / 'trial_data',
        downloads / 'MONSEY_TRAILS_FOUND_ITEMS_WORKING_TRIAL' / 'Monsey_Trails_Photo_Matching_Trial' / 'trial_data',
        downloads / 'MONSEY_TRAILS_FOUND_ITEMS_WORKING_TRIAL (1)' / 'Monsey_Trails_Photo_Matching_Trial' / 'trial_data',
    ]
    for outer in sorted(downloads.glob('MONSEY_TRAILS_AUTO_PHOTO_FOUND_ITEMS_TRIAL_V2*')):
        candidates.append(outer / 'Monsey_Trails_Photo_AI_Trial_v2' / 'trial_data')
    for outer in sorted(downloads.glob('MONSEY_TRAILS_AUTO_PHOTO_FOUND_ITEMS_TRIAL*')):
        candidates.append(outer / 'Monsey_Trails_Photo_AI_Trial' / 'trial_data')
    for outer in sorted(downloads.glob('MONSEY_TRAILS_FOUND_ITEMS_WORKING_TRIAL*')):
        candidates.append(outer / 'Monsey_Trails_Photo_Matching_Trial' / 'trial_data')
    for old in candidates:
        if old.resolve() == DATA.resolve() or not (old/'found_records.json').is_file():
            continue
        try:
            source=json.loads((old/'found_records.json').read_text(encoding='utf-8'))
            if not isinstance(source,dict) or not isinstance(source.get('found'),list):
                continue
            DATA.mkdir(parents=True,exist_ok=True)
            PHOTOS.mkdir(parents=True,exist_ok=True)
            for pic in (old/'photos').glob('*') if (old/'photos').is_dir() else []:
                if pic.is_file() and not (PHOTOS/pic.name).exists():
                    shutil.copy2(pic,PHOTOS/pic.name)
            shutil.copy2(old/'found_records.json',STATE)
            print('Imported earlier found items and photos from:',old)
            return
        except (OSError,ValueError) as ex:
            print('Could not import older photo records:',ex)


def new_state():
    return {'found': [], 'confirmed': [], 'hidden_lost': [], 'pairs': [], 'lost_meta': {}, 'manual_lost': []}


def read_state():
    if not STATE.is_file():
        return new_state()
    try:
        obj = json.loads(STATE.read_text(encoding='utf-8'))
        if isinstance(obj, dict) and all(isinstance(obj.get(k), list) for k in ('found','confirmed','hidden_lost')):
            if not isinstance(obj.get('pairs'), list):
                obj['pairs'] = []
            if not isinstance(obj.get('lost_meta'), dict):
                obj['lost_meta'] = {}
            if not isinstance(obj.get('manual_lost'), list):
                obj['manual_lost'] = []
            return obj
    except (ValueError, OSError):
        pass
    raise ValueError('Could not read saved trial data. Original file was not changed.')


def write_state(obj):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / ('found_records_' + uuid.uuid4().hex[:8] + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, STATE)


def load_entries():
    if LISTENER is None:
        return []
    path = LISTENER / 'voicemail_messages.json'
    if path.is_file():
        try:
            obj=json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(obj,list):
                return [x for x in obj if isinstance(x,dict)]
        except (OSError, ValueError):
            pass
    # Read the existing non-destructive transcript history if JSON is missing.
    path = LISTENER / 'transcripts.txt'
    if path.is_file():
        try:
            s=path.read_text(encoding='utf-8-sig')
            parts=re.split(r'(?m)^---\s+(.+?)\s+---\s*$',s)
            entries=[]
            for j in range(1,len(parts)-1,2):
                stamp, chunk=parts[j].strip(), parts[j+1]
                f=re.search(r'(?m)^Attachment:\s*(.+)$',chunk)
                t=re.search(r'(?ms)^Transcript:\s*(.*?)(?=^---\s+|\Z)',chunk)
                if f and t:
                    entries.append(dict(date=stamp,filename=f.group(1).strip(), transcript=t.group(1).strip()))
            return entries[::-1]
        except OSError:
            pass
    return []


def load_items_sidecar():
    """Item lists Gemini heard in each voicemail (written by the listener)."""
    if LISTENER is None:
        return {}
    try:
        obj=json.loads((LISTENER/'voicemail_items.json').read_text(encoding='utf-8-sig'))
        return obj if isinstance(obj,dict) else {}
    except (OSError, ValueError):
        return {}


def load_trips_sidecar():
    if LISTENER is None:
        return {}
    try:
        obj=json.loads((LISTENER/'voicemail_trips.json').read_text(encoding='utf-8-sig'))
        return obj if isinstance(obj,dict) else {}
    except (OSError, ValueError):
        return {}


def guess_trip(text):
    """Best-effort bus number / time / route from the English words of an older voicemail."""
    t=str(text or '')
    trip={'bus':'','time':'','from':'','to':''}
    m=re.search(r'\bbus\s*(?:number|no\.?|#)?\s*(\d{1,4})\b',t,re.I) or re.search(r'\b(?:number|#)\s*(\d{1,4})\s+bus\b',t,re.I)
    if m:trip['bus']=m.group(1)
    m=re.search(r'\b(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))',t,re.I)
    if m:trip['time']=m.group(1).strip()
    m=re.search(r'\bfrom\s+([A-Z][A-Za-z.\' ]{2,28}?)\s+to\s+([A-Z][A-Za-z.\' ]{2,28}?)(?=[.,;!?]|\s+(?:and|at|on|in|around|about|i|we|my)\b|$)',t)
    if m:trip['from'],trip['to']=m.group(1).strip(),m.group(2).strip()
    return trip


def trip_fields(req):
    return {'bus':clamp(req.get('bus'),20),'ride_time':clamp(req.get('ride_time'),30),
            'from':clamp(req.get('from'),60),'to':clamp(req.get('to'),60)}


def get_lost():
    entries=load_entries()
    rows=build_rows(entries, load_items_sidecar())
    trips=load_trips_sidecar()
    text_by_file={str(e.get('filename') or ''):_english_part(e.get('transcript') or '') for e in entries}
    full_by_file={str(e.get('filename') or ''):str(e.get('transcript') or '') for e in entries}
    output=[]
    for m in read_state().get('manual_lost',[]):
        for n,it in enumerate(m.get('items',[])):
            output.append({'id':m['vm']+'-'+str(n+1),'item':it.get('item') or 'Item','color':it.get('color') or '',
                'name':m.get('name') or 'Not provided','phone':m.get('phone') or 'Not recognized',
                'detail':m.get('note') or 'Added by staff (typed in).','date':m.get('date') or '',
                'vm':m['vm'],'caller_id':'','status':'New','filename':'','trip':m.get('trip') or {}})
    for x in rows:
        item=x.get('item') or 'Item not recognized'
        if item == 'Item not recognized':
            continue  # Only match extracted items; keep original transcript untouched.
        output.append({
            'id':str(x['id']), 'item':item, 'color':x.get('color') or '',
            'name':x.get('name') or 'Not provided','phone':x.get('phone') or 'Not recognized',
            'detail':x.get('snippet') or '', 'date':x.get('date') or '',
            'vm':x.get('vm') or str(x['id']), 'caller_id':x.get('caller_id') or '', 'full':full_by_file.get(x.get('filename') or '',''),
            'status':'New', 'filename':x.get('filename') or '',
            'trip':(lambda tr:{'bus':str(tr.get('bus') or ''),'time':str(tr.get('time') or ''),'from':str(tr.get('from') or ''),'to':str(tr.get('to') or '')})(trips.get(x.get('filename') or '') or guess_trip(text_by_file.get(x.get('filename') or '','')))
        })
    return output,len(entries)


def response_state():
    state=read_state()
    lost, entry_count=get_lost()
    return {'lost':lost,'found':[dict(x,photo=('/photos/'+x['image_file']) if x.get('image_file') else '') for x in state['found'] if not x.get('archived')],
            'confirmed':state['confirmed'], 'hidden_lost':state['hidden_lost'],
            'pairs':state.get('pairs',[]), 'lost_meta':state.get('lost_meta',{}),
            'integrated':bool(LISTENER), 'listener_location':str(LISTENER) if LISTENER else '',
            'voicemails_seen':entry_count, 'local_only':False, 'photo_ai':True}


TEXT_COLORS={'','blue','black','silver','white','red','green','gray','pink','brown','gold','yellow','purple','orange','beige','navy','multicolor','camouflage','charcoal','light gray','cream','tan','khaki','teal','turquoise','cyan','olive','lime','coral','maroon','burgundy','lavender','magenta'}


def clamp(s, n=150):
    return str(s or '').strip()[:n]


class Handler(BaseHTTPRequestHandler):
    server_version='MonseyTrial/1.0'
    def log_message(self, fmt, *args):
        print('%s %s' % (self.log_date_time_string(),fmt%args))

    def send_data(self, status, data, typ='application/json; charset=utf-8'):
        if isinstance(data,(dict,list)):
            data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        elif isinstance(data,str):
            data=data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',typ)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path=urlparse(self.path).path
        if path in ('/','/index.html'):
            self.send_data(200,DASHBOARD.read_bytes(),'text/html; charset=utf-8');return
        if path=='/api/state':
            try:self.send_data(200,response_state())
            except Exception as ex:self.send_data(500,{'error':str(ex)})
            return
        if path.startswith('/photos/'):
            fn=unquote(path[len('/photos/'):])
            if not re.fullmatch(r'[0-9a-f]{32}\.(?:jpg|png|webp|gif)',fn):
                self.send_data(404,{'error':'Not found'});return
            p=PHOTOS/fn
            if not p.is_file():self.send_data(404,{'error':'Photo missing'});return
            typ={'jpg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif'}[p.suffix[1:]]
            self.send_data(200,p.read_bytes(),typ);return
        self.send_data(404,{'error':'Not found'})

    def do_POST(self):
        path=urlparse(self.path).path
        if path not in ('/api/found','/api/edit-found','/api/confirm','/api/hide-lost','/api/archive-found','/api/reanalyze-found',
                        '/api/pair','/api/pair-status','/api/pair-remove','/api/lost-meta','/api/found-text','/api/found-where','/api/lost-add','/api/found-flag'):
            self.send_data(404,{'error':'Not found'});return
        origin=self.headers.get('Origin','')
        if origin and origin != ('http://127.0.0.1:'+str(self.server.server_port)):
            self.send_data(403,{'error':'Other websites are not allowed to change your records.'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>MAX_POST:
                self.send_data(413,{'error':'This photo is too large to upload. Try another photo or use the automatic resize again.'});return
            req=json.loads(self.rfile.read(length).decode('utf-8'))
            if not isinstance(req,dict):raise ValueError('Invalid request')
            with LOCK:
                state=read_state()
                if path=='/api/found':
                    # No item or color requested: upload the photo and analyze later.
                    data_url=str(req.get('photo') or '')
                    if not data_url:
                        raise ValueError('Please take or choose a photo.')
                    m=re.fullmatch(r'data:image/(jpeg|png|webp|gif);base64,([A-Za-z0-9+/=]+)',data_url)
                    if not m:
                        raise ValueError('Upload a JPG, PNG, or WebP image.')
                    raw=base64.b64decode(m.group(2),validate=True)
                    if len(raw)>MAX_IMAGE_BYTES:
                        raise ValueError('Photo must be under 8 MB after automatic resizing.')
                    sig=m.group(1)
                    valid=(sig=='jpeg' and raw[:3]==b'\xff\xd8\xff') or (sig=='png' and raw[:8]==b'\x89PNG\r\n\x1a\n') or (sig=='webp' and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP') or (sig=='gif' and raw[:3] in (b'GIF',))
                    if not valid:
                        raise ValueError('Photo format was not recognized.')
                    ext={'jpeg':'jpg','png':'png','webp':'webp','gif':'gif'}[sig]
                    # The client uses the same ID when retrying a timed-out upload.
                    # Never duplicate a saved record for the same photograph.
                    upload_id=clamp(req.get('upload_id'),80)
                    if not re.fullmatch(r'[A-Za-z0-9_-]{12,80}',upload_id):
                        raise ValueError('Please retry this upload from the dashboard.')
                    existing=next((x for x in state['found'] if x.get('upload_id')==upload_id),None)
                    if existing:
                        self.send_data(200,{'ok':True,'id':existing['id'],'already_saved':True})
                        return
                    fn=uuid.uuid4().hex+'.'+ext
                    PHOTOS.mkdir(parents=True,exist_ok=True)
                    (PHOTOS/fn).write_bytes(raw)
                    record={'id':'f'+uuid.uuid4().hex[:16],
                            'item':'Analyzing photo…','color':'',
                            'where':clamp(req.get('where'),100) or 'Office / location not entered',
                            **trip_fields(req),
                            'date':datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p'),
                            'image_file':fn,'art':'unknown','archived':False,'upload_id':upload_id,
                            'recognition_state':'analyzing',
                            'recognition_note':'Sending item photo to Google Gemini for identification. No voicemail/customer details are sent.'}
                    state['found'].insert(0,record)
                    try:write_state(state)
                    except Exception:
                        (PHOTOS/fn).unlink(missing_ok=True)
                        raise
                    ANALYSIS_QUEUE.put((record['id'],PHOTOS/fn))
                elif path=='/api/reanalyze-found':
                    record_id=clamp(req.get('id'),100)
                    record=next((r for r in state['found'] if r.get('id')==record_id),None)
                    if record is None or not record.get('image_file'):
                        raise ValueError('Found item photograph not available.')
                    photo=PHOTOS/record['image_file']
                    if not photo.is_file():
                        raise ValueError('Original photograph was not found.')
                    if record.get('recognition_state') in ('analyzing', 'waiting'):
                        raise ValueError('Photo is already queued for automatic recognition.')
                    record['recognition_state']='analyzing'
                    record['recognition_note']='Asking Google Gemini to identify the photo...'
                    write_state(state)
                    ANALYSIS_QUEUE.put((record_id,photo))
                elif path=='/api/edit-found':
                    record_id=clamp(req.get('id'),100)
                    record=next((f for f in state['found'] if f.get('id')==record_id),None)
                    if record is None:raise ValueError('Found item not found.')
                    item=clamp(req.get('item'),80)
                    if not item:raise ValueError('Please enter an item name.')
                    color=clamp(req.get('color'),20).lower()
                    allowed={'','blue','black','silver','white','red','green','gray','pink','brown','gold','yellow','purple','orange','beige','navy','multicolor','camouflage','charcoal','light gray','cream','tan','khaki','teal','turquoise','cyan','olive','lime','coral','maroon','burgundy','lavender','magenta'}
                    if color not in allowed:raise ValueError('Choose a listed color or leave it unspecified.')
                    record.update(**trip_fields(req))
                    if clamp(req.get('where'),100):record['where']=clamp(req.get('where'),100)
                    record.update(item=item,color=color,color_hex='',color_palette=[],color_palette_hex=[],recognition_state='corrected',
                                  recognition_note='You corrected the AI suggestion.',
                                  edited_at=datetime.now().isoformat(timespec='seconds'))
                    write_state(state)
                elif path=='/api/archive-found':
                    found_id=clamp(req.get('id'),100)
                    record=next((f for f in state['found'] if f.get('id')==found_id),None)
                    if record:record['archived']=bool(req.get('archived',True))
                    write_state(state)
                elif path=='/api/confirm':
                    key=clamp(req.get('key'),200)
                    if not re.fullmatch(r'[a-zA-Z0-9_-]+',key):raise ValueError('Invalid match ID')
                    if bool(req.get('confirmed')):
                        if key not in state['confirmed']:state['confirmed'].append(key)
                    else:
                        state['confirmed']=[x for x in state['confirmed'] if x!=key]
                    write_state(state)
                elif path=='/api/pair':
                    # Staff pressed Match: remember the pair. It waits for 'I have it' confirmation.
                    lost_id=clamp(req.get('lost_id'),160); found_id=clamp(req.get('found_id'),100)
                    if not re.fullmatch(r'[a-zA-Z0-9_-]+',lost_id) or not re.fullmatch(r'[a-zA-Z0-9_-]+',found_id):
                        raise ValueError('Invalid match')
                    pairs=state.setdefault('pairs',[])
                    if not next((p for p in pairs if p.get('lost_id')==lost_id and p.get('found_id')==found_id),None):
                        def snap(d,keys):
                            d=d if isinstance(d,dict) else {}
                            return {k:clamp(d.get(k),200) for k in keys}
                        pairs.insert(0,{'id':'p'+uuid.uuid4().hex[:12],'vm':clamp(req.get('vm'),100),
                            'lost_id':lost_id,'found_id':found_id,'status':'pending',
                            'created':datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p'),
                            'lost_snap':snap(req.get('lost'),('item','color','name','phone','caller_id','date')),
                            'found_snap':snap(req.get('found'),('item','color','where','date','photo'))})
                    write_state(state)
                elif path=='/api/pair-status':
                    pid=clamp(req.get('id'),40); status=clamp(req.get('status'),20)
                    if status not in ('pending','confirmed'):raise ValueError('Invalid status')
                    pair=next((p for p in state.get('pairs',[]) if p.get('id')==pid),None)
                    if pair is None:raise ValueError('Match not found.')
                    pair['status']=status
                    pair['confirmed_at']=datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p') if status=='confirmed' else ''
                    write_state(state)
                elif path=='/api/pair-remove':
                    pid=clamp(req.get('id'),40)
                    state['pairs']=[p for p in state.get('pairs',[]) if p.get('id')!=pid]
                    write_state(state)
                elif path=='/api/lost-meta':
                    vm=clamp(req.get('vm'),100)
                    if not re.fullmatch(r'[a-zA-Z0-9_-]+',vm):raise ValueError('Invalid report')
                    meta=state.setdefault('lost_meta',{}).setdefault(vm,{})
                    if 'bus' in req:meta['bus']=clamp(req.get('bus'),30)
                    if 'email' in req:meta['email']=clamp(req.get('email'),120)
                    for _k,_n in (('ride_time',30),('from',60),('to',60)):
                        if _k in req:meta[_k]=clamp(req.get(_k),_n)
                    if 'reject' in req:
                        rid=clamp(req.get('reject'),100)
                        if not re.fullmatch(r'[a-zA-Z0-9_-]+',rid):raise ValueError('Invalid item')
                        lst=meta.setdefault('rejected',[])
                        if rid not in lst:lst.append(rid)
                        del lst[:-100]
                    if 'note' in req:meta['note']=clamp(req.get('note'),300)
                    if 'pickup' in req:
                        if clamp(req.get('pickup'),20) not in ('picked_up','not_picked_up'):raise ValueError('Invalid pickup status')
                        meta['pickup']=clamp(req.get('pickup'),20)
                        meta['pickup_at']=datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p') if meta['pickup']=='picked_up' else ''
                    if 'log' in req:
                        kind=clamp(req.get('log'),10)
                        if kind not in ('call','text','email'):raise ValueError('Invalid contact type')
                        key={'call':'calls','text':'texts','email':'emails'}[kind]
                        hist=meta.setdefault('history',[])
                        if req.get('delta')==-1:
                            meta[key]=max(0,int(meta.get(key,0))-1)
                            for i,h in enumerate(hist):
                                if h.get('type')==kind:
                                    del hist[i];break
                        else:
                            meta[key]=int(meta.get(key,0))+1
                            meta['replied']='yes';meta['seen']='yes'
                            hist.insert(0,{'type':kind,'at':datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p')})
                            del hist[40:]
                    for _k,_ok in (('seen',('yes','')),('replied',('yes','unsure',''))):
                        if _k in req:
                            if clamp(req.get(_k),8) not in _ok:raise ValueError('Invalid flag')
                            meta[_k]=clamp(req.get(_k),8)
                    if 'trash' in req:
                        if clamp(req.get('trash'),4) not in ('yes',''):raise ValueError('Invalid trash flag')
                        meta['trash']=clamp(req.get('trash'),4)
                    if 'nf' in req:
                        if clamp(req.get('nf'),4) not in ('yes','no',''):raise ValueError('Invalid not-found flag')
                        meta['nf']=clamp(req.get('nf'),4)
                    if 'status' in req:
                        if clamp(req.get('status'),10) not in ('lost','found'):raise ValueError('Invalid status')
                        meta['status']=clamp(req.get('status'),10)
                    write_state(state)
                elif path=='/api/found-text':
                    # A found item that has no photo (for example one that arrived from the other yard).
                    item=clamp(req.get('item'),80)
                    if not item:raise ValueError('Please type what was found.')
                    color=clamp(req.get('color'),20).lower()
                    if color not in TEXT_COLORS:raise ValueError('Choose a listed color or leave it unspecified.')
                    record={'id':'f'+uuid.uuid4().hex[:16],'item':item,'color':color,
                            'where':clamp(req.get('where'),100) or 'Not entered',
                            **trip_fields(req),
                            'note':clamp(req.get('note'),300),
                            'date':datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p'),
                            'image_file':'','art':'unknown','archived':False,
                            'recognition_state':'manual','recognition_note':'Added by staff without a photo.'}
                    state['found'].insert(0,record)
                    write_state(state)
                elif path=='/api/found-flag':
                    found_id=clamp(req.get('id'),100)
                    record=next((f for f in state['found'] if f.get('id')==found_id),None)
                    if record is None:raise ValueError('Found item not found.')
                    if clamp(req.get('dp'),4) not in ('yes','no',''):raise ValueError('Invalid flag')
                    record['dp']=clamp(req.get('dp'),4)
                    write_state(state)
                elif path=='/api/found-where':
                    found_id=clamp(req.get('id'),100)
                    record=next((f for f in state['found'] if f.get('id')==found_id),None)
                    if record is None:raise ValueError('Found item not found.')
                    record['where']=clamp(req.get('where'),100) or 'Office'
                    write_state(state)
                elif path=='/api/lost-add':
                    # A lost report typed in by staff (for example a customer who came in or called someone else).
                    raw_items=[p.strip() for p in re.split(r'[,\n;]+',str(req.get('items') or '')) if p.strip()][:20]
                    if not raw_items:raise ValueError('Please type what was lost.')
                    items=[]
                    for part in raw_items:
                        part=clamp(part,80);words=part.split()
                        col=words[0].lower() if words and words[0].lower() in TEXT_COLORS and len(words)>1 else ''
                        name_txt=' '.join(words[1:]) if col else part
                        items.append({'item':name_txt[:1].upper()+name_txt[1:],'color':col})
                    vm='m'+uuid.uuid4().hex[:10]
                    state.setdefault('manual_lost',[]).insert(0,{'vm':vm,'name':clamp(req.get('name'),60) or 'Not provided',
                        'phone':clamp(req.get('phone'),30),'note':clamp(req.get('note'),300),'items':items,
                        'date':datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p'),
                        'trip':{'bus':clamp(req.get('bus'),20),'time':clamp(req.get('ride_time'),30),'from':clamp(req.get('from'),60),'to':clamp(req.get('to'),60)}})
                    write_state(state)
                elif path=='/api/hide-lost':
                    key=clamp(req.get('id'),100)
                    if bool(req.get('hidden')):
                        if key not in state['hidden_lost']:state['hidden_lost'].append(key)
                    else:
                        state['hidden_lost']=[x for x in state['hidden_lost'] if x!=key]
                    write_state(state)
            self.send_data(200,{'ok':True, 'id': record['id'] if path=='/api/found' else None})
        except (ValueError,TypeError) as ex:
            self.send_data(400,{'error':str(ex)})
        except Exception as ex:
            self.send_data(500,{'error':'Could not save this record: '+str(ex)})


def analyze_saved_photo(record_id, path):
    """Identify one saved photo; keep API-quota failures pending instead of mislabeling."""
    suggestion = None
    error_message = ''
    quota_limited = False
    try:
        from gemini_vision import analyze as analyze_photo_online
        suggestion = analyze_photo_online(path)
    except Exception as exc:
        error_message = str(exc)[:500]
        print('Gemini photo recognition issue:', error_message[:300])
        # Current Gemini client raises a GeminiError containing 'HTTP 429'.
        # Never reclassify a quota/network failure as a recognition failure.
        quota_limited = ('HTTP 429' in error_message or 'RESOURCE_EXHAUSTED' in error_message)

    with LOCK:
        state = read_state()
        record = next((r for r in state['found'] if r.get('id') == record_id), None)
        if record and record.get('recognition_state') == 'analyzing':
            if suggestion is not None:
                record.update(suggestion)
                record['art'] = str(record['item']).lower()
                record.pop('next_retry_at', None)
                record.pop('quota_retries', None)
            elif quota_limited:
                # Exponential delay: 90 s, 3 m, 6 m, 12 m, 24 m, then 1 h.
                # Daily quota/spend caps won't be bypassed; saved items remain waiting.
                count = min(99, int(record.get('quota_retries', 0) or 0) + 1)
                delay = min(3600, 90 * (2 ** min(count-1, 6)))
                record.update(recognition_state='waiting',
                    recognition_note='Google API limit reached (HTTP 429). Photo saved; automatic retry later. '
                                     'Check Google AI Studio rate limits if this continues.',
                    quota_retries=count, next_retry_at=time.time()+delay, ai_provider='Gemini')
                print(f'Photo queued after HTTP 429; next automatic retry in {delay} seconds.')
            else:
                if record.get('item') in ('Analyzing photo…', 'Analyzing photo...'):
                    record['item'] = 'Item needs review'
                record.update(recognition_state='needs_review',
                              recognition_note=error_message[:240] or 'Gemini could not analyze this photo.',
                              ai_provider='Gemini')
            write_state(state)
    status = (suggestion or {}).get('item') if suggestion else ('waiting for Gemini quota' if quota_limited else 'needs review')
    print('Photo result:', status)

def _recheck_previous_photos():
    """Automatically re-check previously misidentified photos, never overwrite edits."""
    try:
        with LOCK:
            state=read_state()
            eligible=[r for r in state['found'] if
                      r.get('image_file') and not r.get('archived') and
                      r.get('recognition_state') in ('suggested','review','analyzing','needs_review') and
                      not r.get('vision_v6_rechecked')]
            for r in eligible:
                r['recognition_state']='analyzing'
                r['recognition_note']='V6 rechecking recognition of siddurim, AirPods cases, and colors.'
            if eligible:write_state(state)
        if eligible:print('Rechecking',len(eligible),'older photos in the background...')
        for r in eligible:
            photo=PHOTOS/r['image_file']
            try:
                suggestion=analyze(photo)
            except Exception as ex:
                suggestion={'item':'Item needs review','color':'', 'color_palette':[], 'color_palette_hex':[], 'color_hex':'',
                            'recognition_state':'needs_review',
                            'recognition_note':('Could not recognize automatically: '+str(ex))[:200]}
            with LOCK:
                state=read_state()
                original=next((f for f in state['found'] if f.get('id')==r['id']),None)
                if original and original.get('recognition_state')=='analyzing':
                    original.update(suggestion)
                    original['vision_v6_rechecked']=True
                    original['art']=str(original.get('item','')).lower()
                    write_state(state)
        if eligible:print('Finished checking older photos. Refresh the dashboard to see updates.')
    except Exception as e:
        print('Older-photo recheck issue:', e)


def _analysis_worker():
    """Process saved photos serially, spacing requests to avoid bursts."""
    while True:
        record_id, photo_path = ANALYSIS_QUEUE.get()
        try:
            analyze_saved_photo(record_id, photo_path)
        except Exception as ex:
            print('Photo processing job failed:', str(ex)[:250])
        finally:
            ANALYSIS_QUEUE.task_done()
        if PHOTO_GAP_SECONDS and not ANALYSIS_QUEUE.empty():
            time.sleep(PHOTO_GAP_SECONDS)


def _resume_pending_photos():
    """Resume uploads saved before a program restart."""
    with LOCK:
        state = read_state()
        pending = [r for r in state['found'] if
                   r.get('recognition_state') == 'analyzing' and r.get('image_file')]
    for record in pending:
        photo = PHOTOS / record['image_file']
        if photo.is_file():
            ANALYSIS_QUEUE.put((record['id'], photo))
    if pending:
        print('Resuming', len(pending), 'saved photos in the background.')


def _waiting_photo_scheduler():
    """Persist quota-blocked photos and retry later, even after a restart.

    Automatically recovers old items that incorrectly showed Needs Review
    specifically because the API returned HTTP 429.
    """
    while True:
        try:
            now = time.time()
            with LOCK:
                state = read_state()
                to_queue = []
                changed = False
                for r in state['found']:
                    if r.get('archived') or not r.get('image_file'):
                        continue
                    if r.get('recognition_state') == 'needs_review' and 'HTTP 429' in str(r.get('recognition_note','')):
                        r['recognition_state'] = 'waiting'
                        r['next_retry_at'] = now + 30
                        changed = True
                    if r.get('recognition_state') != 'waiting':
                        continue
                    if float(r.get('next_retry_at', 0) or 0) <= now:
                        pic = PHOTOS / r['image_file']
                        if pic.is_file():
                            r['recognition_state'] = 'analyzing'
                            r['recognition_note'] = 'Retrying saved photo with Google Gemini...'
                            to_queue.append((r['id'], pic))
                            changed = True
                if changed:
                    write_state(state)
            for item in to_queue:
                ANALYSIS_QUEUE.put(item)
        except Exception as ex:
            print('Waiting-photo scheduler issue:', str(ex)[:240])
        time.sleep(WAITING_SCAN_SECONDS)

def main():
    migrate_old_trial_data()
    # Automatic rechecking of every old photo was overloading some office PCs.
    # Preserve old results; process only new and previously unfinished photos.
    DATA.mkdir(exist_ok=True)
    threading.Thread(target=_analysis_worker,daemon=True,name='photo-ai-queue').start()
    _resume_pending_photos()
    threading.Thread(target=_waiting_photo_scheduler, daemon=True, name='gemini-quota-scheduler').start()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    url='http://127.0.0.1:%d/' % server.server_port
    print('\nMONSEY TRAILS — FOUND ITEMS PHOTO TRIAL V7')
    print('Dashboard runs on this computer; item photos are sent to Google Gemini free-tier API.')
    print('Existing Intermedia folder:', LISTENER or 'NOT FOUND — trial still runs with no lost reports')
    print('Photo records saved in (V2):',DATA)
    print('Opening:',url)
    print('Keep this black window OPEN while using the trial. Ctrl+C stops it.\n')
    threading.Timer(1.0,lambda:webbrowser.open(url)).start()
    try:server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:print('\nTrial stopped. Your saved records remain on disk.')
    finally:server.server_close()

if __name__=='__main__':main()
