"""Bounded offline cross-encoder ranking; original evidence is never rewritten."""
import json
import math
from pathlib import Path
import threading
import time

from tcm.mineru_pharmacopoeia import digest

REPO='Xenova/bge-reranker-base'
REVISION='cbd4c47f870c4bb77534e06b8853de9c6e958b82'
REQUIRED={'config.json','tokenizer_config.json','tokenizer.json','onnx/model_quantized.onnx'}


def verify_assets(directory):
    directory=Path(directory).resolve()
    receipt=json.loads((directory/'download.json').read_text())
    if receipt.get('repo')!=REPO or receipt.get('revision')!=REVISION:
        raise ValueError('Unexpected reranker model revision')
    files=receipt['files']
    if len(files)!=4 or {r['file'] for r in files}!=REQUIRED:
        raise ValueError('Incomplete reranker manifest')
    for row in files:
        file=(directory/row['file']).resolve()
        if not file.is_relative_to(directory) or digest(file)!=row['sha256']:
            raise ValueError('Reranker asset changed')
    # Anchor large assets to published LFS hashes, not only a mutable receipt.
    hashes={r['file']:r['sha256'] for r in files}
    if hashes['onnx/model_quantized.onnx']!='dd98f3e67837d23210a6b7550c08cced4f61845b940ac45be3565840a10f3244' or hashes['tokenizer.json']!='48564c5c7d3fa64d85d95e65414a542385f88b0f128fd8d4163fd7a57f2be05c':
        raise ValueError('Unexpected reranker weights or tokenizer')
    return receipt


def ranked_hits(hits, scores, truncated, k):
    if len(hits)!=len(scores) or len(hits)!=len(truncated) or any(not math.isfinite(float(s)) for s in scores):
        raise ValueError('Invalid cross-encoder scores')
    if len({h['chunk_id'] for h in hits})!=len(hits):raise ValueError('Duplicate candidates')
    indices=sorted(range(len(hits)),key=lambda i:(-float(scores[i]),hits[i]['chunk_id']))
    return [{**hits[i],'rerank_score':float(scores[i]),'rerank_truncated':bool(truncated[i])} for i in indices[:k]]


class LocalReranker:
    def __init__(self,directory,threads=2):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        directory=Path(directory);verify_assets(directory)
        self.fingerprint=digest(directory/'download.json')
        self.np=np;self.ort=ort
        self.tokenizer=Tokenizer.from_file(str(directory/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512,strategy='longest_first')
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id('<pad>'),pad_token='<pad>')
        ort.disable_telemetry_events()
        options=ort.SessionOptions();options.intra_op_num_threads=threads;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(directory/'onnx/model_quantized.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        self.inputs={x.name for x in self.session.get_inputs()}
        if not {'input_ids','attention_mask'}.issubset(self.inputs):raise ValueError('Unsupported reranker inputs')

    def rerank(self,question,hits,*,k=6,timeout=12):
        if not isinstance(question,str) or not 1<=len(question.strip())<=500:raise ValueError('Invalid query')
        if not 1<=len(hits)<=20 or type(k) is not int or not 1<=k<=20:raise ValueError('Candidate budget exceeded')
        if not 0<timeout<=20:raise ValueError('Invalid reranker time budget')
        started=time.monotonic();scores=[];truncated=[]
        for offset in range(0,len(hits),4):
            batch=hits[offset:offset+4]
            # Fixed pair recipe: include title/field to disambiguate equally phrased source text.
            pairs=[(question,h['name']+' '+h['label']+'\n'+h['text']) for h in batch]
            encoded=self.tokenizer.encode_batch(pairs)
            truncated.extend(bool(e.overflowing) for e in encoded)
            inputs={'input_ids':self.np.asarray([e.ids for e in encoded],dtype=self.np.int64),
                    'attention_mask':self.np.asarray([e.attention_mask for e in encoded],dtype=self.np.int64),
                    'token_type_ids':self.np.asarray([e.type_ids for e in encoded],dtype=self.np.int64)}
            remaining=timeout-(time.monotonic()-started)
            if remaining<=0:raise TimeoutError('Reranker time budget exhausted')
            options=self.ort.RunOptions()
            def terminate():options.terminate=True
            timer=threading.Timer(remaining,terminate);timer.daemon=True;timer.start()
            try:
                output=self.session.run(None,{key:value for key,value in inputs.items() if key in self.inputs},options)[0]
            finally:timer.cancel();timer.join()
            if time.monotonic()-started>timeout:raise TimeoutError('Reranker time budget exhausted')
            if output.shape not in ((len(batch),1),(len(batch),)):raise ValueError('Unexpected score shape')
            scores.extend(output.reshape(-1).tolist())
        return ranked_hits(hits,scores,truncated,k),{'model':REPO,'revision':REVISION,'fingerprint':self.fingerprint,
            'status':'used','candidates':len(hits),'returned':min(k,len(hits)),
            'truncated_candidates':sum(truncated),'inference_ms':round((time.monotonic()-started)*1000,2)}


def maybe_rerank(question,hits,model_directory,*,factory=LocalReranker):
    """Single candidate needs no comparison. Failure preserves the original ranking."""
    if not model_directory:return hits[:6],{'status':'not_configured'}
    if len(hits)<2:return hits[:6],{'status':'not_needed','candidates':len(hits)}
    started=time.monotonic()
    try:
        reranker=factory(model_directory)
        result,metadata=reranker.rerank(question,hits[:20])
        metadata['total_ms']=round((time.monotonic()-started)*1000,2)
        return result,metadata
    except Exception:
        return hits[:6],{'status':'unavailable','fallback':'original_rrf','candidates':len(hits),
                         'total_ms':round((time.monotonic()-started)*1000,2)}
