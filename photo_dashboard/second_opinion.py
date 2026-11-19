"""Independent, free Florence-2 image second opinion on the same local computer.

Uses the safe/standard Transformers architecture and MIT licensed weights.
This is not guaranteed to recognize all objects. Never sends photos to an API.
"""
from __future__ import annotations
import logging
import re
from threading import Lock

LOG=logging.getLogger(__name__)
MODEL_ID='florence-community/Florence-2-base-ft'
_model=None
_proc=None
_load_failed=False
_lock=Lock()

def _load():
    global _model, _proc, _load_failed
    if _load_failed:return None, None
    if _model is None:
        try:
            import torch
            from transformers import AutoProcessor, Florence2ForConditionalGeneration
            torch.set_num_threads(min(4,max(1,torch.get_num_threads())))
            _proc=AutoProcessor.from_pretrained(MODEL_ID)
            _model=Florence2ForConditionalGeneration.from_pretrained(MODEL_ID,torch_dtype=torch.float32,use_safetensors=True).eval()
        except Exception as ex:
            _load_failed=True
            LOG.warning('Florence second model unavailable; using existing CLIP: %s',ex)
            return None, None
    return _model, _proc

# Specific phrase order matters: avoid calling a bare phone CASE a phone.
RULES=[
 ('Tefillin bag',r'\b(tefillin bag|tefillin pouch)\b'),
 ('Tefillin',r'\b(tefillin|phylacteries|tefilin)\b'),
 ('Tallis bag',r'\b(tallis bag|tallit bag|prayer shawl bag)\b'),
 ('Tallis',r'\b(tallit|tallis|prayer shawl|tallith)\b'),
 ('Siddur',r'\b(siddur|siddurim|jewish prayer book|prayer book)\b'),
 ('Shtreimel',r'\b(shtreimel|streimel|fur hasidic hat)\b'),
 ('Kippah',r'\b(kippah|yarmulke|skullcap)\b'),
 ('Tzitzis',r'\b(tzitzit|tzitzis|fringed jewish garment)\b'),
 ('Chumash',r'\b(chumash|pentateuch)\b'),
 ('Mezuzah',r'\b(mezuzah|mezuzot)\b'),
 ('Sefer',r'\b(sefer|seforim|religious hebrew book)\b'),
 ('AirPods',r'\b(airpods|apple air pods|airpod charging case)\b'),
 ('Carriage',r'\b(baby carriage|baby stroller|pram|buggy)\b'),
 ('Phone case',r'\b(phone case|smartphone case|cellphone cover|mobile phone cover|case for (a )?(cell|mobile|flip|smart) ?phone|case of (a )?(cell|mobile|flip|smart) ?phone)\b'),
 ('Phone',r'\b(flip phone|folded phone|folding phone|clamshell phone|cell phone|cellphone|mobile phone|telephone handset|smartphone|feature phone|mobile handset|wireless phone)\b'),
 ('Car key',r'\b(car keys?|vehicle keys?|key fob|car remote control|ignition key)\b'),
 ('iPad',r'\b(ipad|tablet computer)\b'),
 ('Wallet',r'\b(wallet|billfold)\b'),
 ('Glasses',r'\b(glasses|eyeglasses|spectacles)\b'),
 ('Handbag',r'\b(handbag|purse)\b'),
 ('Backpack',r'\b(backpack|school bag)\b'),
 ('Coat',r'\b(coat|overcoat|winter coat)\b'),
 ('Jacket',r'\b(jacket)\b'),
 ('Watch',r'\b(wristwatch|watch)\b'),
 ('Keys',r'\b(keys?|keyring|key chain)\b'),
 ('Laptop',r'\b(laptop|notebook computer)\b'),
 ('Book',r'\b(book|hardcover)\b'),
 ('Umbrella',r'\b(umbrella)\b'),
]

def guess_from_caption(caption):
    caption=(caption or '').lower().strip()
    for item,pattern in RULES:
        if re.search(pattern,caption,re.I):
            return item
    return None

def describe(image):
    with _lock:
        model,proc=_load()
        if model is None:return '',None
        try:
            import torch
            # The caption model sees the full view, never the small dashboard thumbnail.
            from PIL import ImageOps
            display=ImageOps.contain(image.convert('RGB'),(1024,1024))
            prompt='<MORE_DETAILED_CAPTION>'
            inputs=proc(text=prompt,images=display,return_tensors='pt')
            with torch.inference_mode():
                ids=model.generate(**inputs,max_new_tokens=100,num_beams=2,do_sample=False)
            text=proc.batch_decode(ids,skip_special_tokens=False)[0]
            try:
                parsed=proc.post_process_generation(text,task=prompt,image_size=display.size)
                caption=parsed.get(prompt,text) if isinstance(parsed,dict) else str(parsed)
            except Exception:
                caption=proc.batch_decode(ids,skip_special_tokens=True)[0]
            caption=str(caption).strip()[:650]
            return caption,guess_from_caption(caption)
        except Exception as ex:
            LOG.warning('Florence image caption failed, retaining CLIP result: %s',ex)
            return '',None


def resolve_candidates(first, first_uncertain, second, bank=None):
    """Deterministic adjudication. Unclear phone/key cases become review, not false fact.

    This function is separately testable without downloading either model.
    """
    if second is None and not bank:
        return first,first_uncertain,'Original CLIP model (second opinion unavailable)'
    # A licensed-photo reference for a specific Jewish item or AirPods can
    # refine an otherwise generic book / case guess. Mark for review.
    specialized = {'Siddur', 'Sefer', 'Chumash', 'Tefillin', 'Tallis', 'Tallis bag',
                   'Tefillin bag', 'Shtreimel', 'Kippah', 'Tzitzis', 'Mezuzah', 'AirPods'}
    generic = {'Book', 'Small box', 'Earbuds', 'Phone case', 'Plastic cover', 'Sefer'}
    if bank in specialized and first in generic and (second is None or second in generic or second==bank):
        return bank, True, 'Licensed photo references suggest a specific item; check the actual photo'
    if second==first:
        return first,False,'Both image models agree'
    if bank and (bank==second or bank==first):
        return bank, False,'Reference gallery agrees with one model'
    confusable={'Phone','Car key','Keys','AirPods','Wallet','Phone case','Small box','Power bank'}
    if second=='Phone' and first in {'Car key','Keys','AirPods','Wallet','Small box','Power bank','Phone case'}:
        return 'Phone',True,'Independent caption identifies a phone; please verify'
    if second and second in confusable and first in confusable:
        return 'Item needs review',True,'AI models disagree; photo kept for review'
    if second:
        return second,True,'Caption-based second opinion; please verify'
    return first, True,'Reference model not decisive'
