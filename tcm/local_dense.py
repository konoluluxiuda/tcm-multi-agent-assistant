"""Optional offline dense experiment. All model files must already exist locally."""
import json
import math
from pathlib import Path
import time

from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_guards import field_body,reference_target

RECIPE={'version':1,'document':'name + space + label + newline + text','pooling':'CLS',
        'normalize':'L2','max_tokens':512,'query_prefix':'为这个句子生成表示以用于检索相关文章：'}


def verify_model_files(directory):
    directory=Path(directory);receipt=json.loads((directory/'download.json').read_text())
    if receipt['repo']!='Xenova/bge-small-zh-v1.5' or receipt['revision']!='75c43b069aac4d136ba6bc1122f995fedcfd2781':
        raise ValueError('Expected the pinned BGE ONNX export')
    required={'config.json','tokenizer.json','tokenizer_config.json','onnx/model.onnx'}
    if {row['file'] for row in receipt['files']}!=required:raise ValueError('Incomplete pinned model files')
    for row in receipt['files']:
        path=(directory/row['file']).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path)!=row['sha256']:raise ValueError('Local model file changed')
    return receipt


def check_cache_manifest(receipt,index_hash,model_hash,vector_hash,item_hash):
    expected={'recipe':RECIPE,'index_sha256':index_hash,'model_fingerprint':model_hash,
              'vectors_sha256':vector_hash,'items_sha256':item_hash}
    if any(receipt[k]!=v for k,v in expected.items()):raise ValueError('Dense cache/source/model mismatch')


def top_indices(scores,identifiers,k,allowed=None):
    if type(k) is not int or not 1<=k<=20:raise ValueError('k must be 1–20')
    if len(scores)!=len(identifiers):raise ValueError('Score/ID length mismatch')
    indices=list(range(len(scores))) if allowed is None else list(allowed)
    if any(type(i) is not int or i<0 or i>=len(scores) for i in indices):raise ValueError('Invalid candidate index')
    if any(not math.isfinite(float(scores[i])) for i in indices):raise ValueError('Non-finite similarity')
    return sorted(indices,key=lambda i:(-float(scores[i]),identifiers[i]))[:k]


class LocalEncoder:
    def __init__(self,directory,threads=4):
        self.directory=Path(directory);self.receipt=verify_model_files(self.directory)
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np=np
        self.fingerprint=digest(self.directory/'download.json')
        self.dimension=json.loads((self.directory/'config.json').read_text())['hidden_size']
        self.tokenizer=Tokenizer.from_file(str(self.directory/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=RECIPE['max_tokens'])
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id('[PAD]'),pad_token='[PAD]')
        ort.disable_telemetry_events()
        options=ort.SessionOptions();options.intra_op_num_threads=threads;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(self.directory/'onnx/model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        self.input_names={v.name for v in self.session.get_inputs()}
        self.last_truncated=[]

    def encode(self,texts,query=False):
        np=self.np
        if not texts or any(not isinstance(t,str) or not t for t in texts):raise ValueError('Nonempty text batch required')
        encoded=self.tokenizer.encode_batch([RECIPE['query_prefix']+t if query else t for t in texts])
        self.last_truncated=[bool(e.overflowing) for e in encoded]
        inputs={'input_ids':np.asarray([e.ids for e in encoded],dtype=np.int64),
                'attention_mask':np.asarray([e.attention_mask for e in encoded],dtype=np.int64),
                'token_type_ids':np.asarray([e.type_ids for e in encoded],dtype=np.int64)}
        output=self.session.run(None,{k:v for k,v in inputs.items() if k in self.input_names})[0]
        vectors=output[:,0,:].astype(np.float32)
        lengths=np.linalg.norm(vectors,axis=1,keepdims=True)
        if vectors.shape!=(len(texts),self.dimension) or not np.isfinite(vectors).all() or (lengths<=0).any():
            raise ValueError('Invalid local embeddings')
        return vectors/lengths


def build_vectors(retriever,encoder,output,batch_size=32):
    import numpy as np
    output=Path(output)
    if output.exists():raise FileExistsError('Use a fresh dense cache directory')
    if type(batch_size) is not int or not 1<=batch_size<=128:raise ValueError('Invalid batch size')
    rows=list(retriever.connection.execute('SELECT * FROM chunks ORDER BY chunk_id'))
    items=[{k:r[k] for k in ('chunk_id','entry_id','name','label','scope')} for r in rows]
    texts=[r['name']+' '+r['label']+'\n'+r['text'] for r in rows]
    order=sorted(range(len(rows)),key=lambda i:(len(texts[i]),items[i]['chunk_id']))
    matrix=np.zeros((len(rows),encoder.dimension),dtype=np.float32)
    start=time.monotonic();output.mkdir(parents=True,mode=0o700)
    for offset in range(0,len(order),batch_size):
        selected=order[offset:offset+batch_size];matrix[selected]=encoder.encode([texts[i] for i in selected])
        for i,truncated in zip(selected,encoder.last_truncated):items[i]['embedding_truncated']=truncated
        if offset%(batch_size*16)==0:print(json.dumps({'embedded':min(offset+batch_size,len(order)),'total':len(order)}),flush=True)
    np.save(output/'vectors.npy',matrix,allow_pickle=False)
    (output/'items.json').write_text(json.dumps(items,ensure_ascii=False))
    receipt={'recipe':RECIPE,'index_sha256':retriever.receipt['index_sha256'],'model_fingerprint':encoder.fingerprint,
             'dimension':encoder.dimension,'count':len(rows),'truncated_documents':sum(i['embedding_truncated'] for i in items),
             'build_seconds':round(time.monotonic()-start,3),'vectors_sha256':digest(output/'vectors.npy'),
             'items_sha256':digest(output/'items.json'),'builder_code_sha256':digest(Path(__file__))}
    (output/'manifest.json').write_text(json.dumps(receipt,indent=2));return receipt


class DenseRetriever:
    def __init__(self,retriever,encoder,directory):
        import numpy as np
        directory=Path(directory);self.lexical=retriever;self.encoder=encoder
        self.receipt=json.loads((directory/'manifest.json').read_text())
        check_cache_manifest(self.receipt,retriever.receipt['index_sha256'],encoder.fingerprint,
                             digest(directory/'vectors.npy'),digest(directory/'items.json'))
        self.items=json.loads((directory/'items.json').read_text())
        self.vectors=np.load(directory/'vectors.npy',mmap_mode='r',allow_pickle=False)
        if self.vectors.shape!=(len(self.items),encoder.dimension) or not np.isfinite(self.vectors).all():raise ValueError('Invalid dense matrix')
        if not np.allclose(np.linalg.norm(self.vectors,axis=1),1,atol=0.001):raise ValueError('Expected normalized vectors')
        self.identifiers=[i['chunk_id'] for i in self.items]

    def rank(self,vector,k,allowed):
        scores=self.vectors@vector
        return [(i,float(scores[i])) for i in top_indices(scores,self.identifiers,k,allowed)]

    def query(self,question,method='dense',k=5):
        if method not in ('dense','name_dense'):raise ValueError('Unknown dense method')
        if not isinstance(question,str) or not 1<=len(question.strip())<=500:raise ValueError('Question must contain 1–500 characters')
        if type(k) is not int or not 1<=k<=20:raise ValueError('k must be 1–20')
        started=time.monotonic();allowed=None
        base={'method':method,'question':question,'status':'candidates','hits':[],'query_encoding_ms':0,
              'external_calls':0,'research_only':True,'human_signoff':False}
        if method=='name_dense':
            gate=self.lexical.query(question,method='name_field',k=1)
            if gate['status'] not in ('candidates','field_not_resolved') or len(gate.get('names',[]))!=1:
                return {**base,'status':gate['status'],'query_ms':round((time.monotonic()-started)*1000,3)}
            name=gate['names'][0];labels=gate.get('fields',[])
            allowed=[i for i,item in enumerate(self.items) if item['name']==name and not item['scope'].startswith('appendix:')
                     and (not labels or item['label'] in labels)]
        encoding_start=time.monotonic();vector=self.encoder.encode([question],query=True)[0]
        base['query_encoding_ms']=round((time.monotonic()-encoding_start)*1000,3)
        selected=self.rank(vector,k,allowed)
        for i,score in selected:
            row=self.lexical.connection.execute('SELECT * FROM chunks WHERE chunk_id=?',(self.identifiers[i],)).fetchone()
            hit=self.lexical.hit(row);hit['score']=score;hit['embedding_truncated']=self.items[i]['embedding_truncated']
            if reference_target(hit['text']) or not field_body(hit['text']):
                base.setdefault('unresolved_evidence',[]).append(hit)
            else:base['hits'].append(hit)
        if base.get('unresolved_evidence'):base['status']='partial_evidence' if base['hits'] else 'reference_unresolved'
        elif not base['hits']:base['status']='no_match'
        if method=='name_dense' and base['hits']:
            labels={h['label'] for h in base['hits']}
            if any(sum(i['name']==name and i['label']==label and not i['scope'].startswith('appendix:') for i in self.items)>1 for label in labels):
                base['status']='ambiguous_field'
        return {**base,'query_ms':round((time.monotonic()-started)*1000,3)}
