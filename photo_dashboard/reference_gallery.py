"""Optional CLIP reference gallery. Only loads locally indexed images.

The reference gallery is built with BUILD_REFERENCE_LIBRARY.bat from Commons
images whose machine-readable licenses permit reuse. Nothing is uploaded.
"""
from __future__ import annotations
import json
from pathlib import Path
from threading import Lock

ROOT=Path(__file__).resolve().parent
INDEX=ROOT/'reference_bank'/'embeddings.npz'
_meta=None
_loaded=None
_mtime=None
_lock=Lock()

def gallery_vote(image):
    global _loaded,_meta,_mtime
    if not INDEX.is_file():return None
    import numpy as np
    from PIL import ImageOps
    with _lock:
        stamp=INDEX.stat().st_mtime
        if _loaded is None or _mtime!=stamp:
            try:
                with np.load(INDEX,allow_pickle=False) as data:
                    _loaded=data['vectors'].astype('float32')
                    _meta=[str(v) for v in data['labels'].tolist()]
                _mtime=stamp
            except (OSError,ValueError,KeyError):return None
        if _loaded is None or len(_meta)<3:return None
        from auto_vision import _model
        import torch
        model,processor=_model()
        full=ImageOps.contain(image.convert('RGB'),(448,448))
        inputs=processor(images=full,return_tensors='pt')
        with torch.inference_mode():
            rep=model.get_image_features(pixel_values=inputs['pixel_values'])
        if not isinstance(rep,torch.Tensor):
            rep=getattr(rep,'pooler_output',None)
            if rep is None:rep=getattr(rep,'image_embeds',None)
        if rep is None:return None
        vec=rep[0].detach().cpu().numpy().astype('float32')
        vec/=max(float(np.linalg.norm(vec)),1e-12)
        scores=_loaded@vec
        top=np.argsort(-scores)[:8]
        winners={}
        for i in top:
            label=_meta[int(i)]
            sim=float(scores[i])
            if sim>=.27:
                winners[label]=winners.get(label,0.0)+max(0.0,sim-.22)
        if not winners:return None
        best=sorted(winners.items(),key=lambda p:p[1],reverse=True)
        # Images need to be genuinely similar before voting; avoid bank overreach.
        if len(top)<3 or float(scores[top[0]]) < .36:return None
        if len(best)>1 and best[0][1]-best[1][1]<.075:return None
        return best[0][0]
