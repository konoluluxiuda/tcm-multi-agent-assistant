"""Optional CPU translation. Loads pinned local files only; no providers or downloads."""
import hashlib
import json
from pathlib import Path
import re


class LocalTranslator:
    def __init__(self,directory):
        directory=Path(directory)
        receipt=json.loads((directory/'receipt.json').read_text())
        for name,expected in receipt['files'].items():
            path=(directory/name).resolve()
            if not path.is_relative_to(directory.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise ValueError('Translation model file integrity mismatch')
        import ctranslate2
        import sentencepiece
        self.receipt=receipt
        self.tokenizer=sentencepiece.SentencePieceProcessor(model_file=str(directory/'sentencepiece.model'))
        self.model=ctranslate2.Translator(str(directory/'model'),device='cpu',compute_type='int8',
                                         inter_threads=1,intra_threads=4)

    def translate(self,texts):
        # Split on bounded token spans rather than allowing the engine to silently truncate.
        inputs=[];owners=[];counts=[0]*len(texts)
        for i,text in enumerate(texts):
            tokens=self.tokenizer.encode(text,out_type=str)
            for start in range(0,len(tokens),120):
                inputs.append(tokens[start:start+120]);owners.append(i);counts[i]+=1
        outputs=['']*len(texts)
        if inputs:
            results=self.model.translate_batch(inputs,beam_size=4,max_batch_size=32,
                                               max_input_length=0,max_decoding_length=256)
            for owner,result in zip(owners,results):
                translated=self.tokenizer.decode(result.hypotheses[0]).replace('▁',' ').strip()
                outputs[owner]+=translated
        return [{'text':text,'status':'machine_candidate','method':'local_argos_en_zh_1.9',
                 'human_reviewed':False,'segmented':counts[i]>1,
                 'quality_flags':(['no_chinese_output'] if not re.search(r'[\u4e00-\u9fff]',text) else [])+
                                 (['multiple_token_spans'] if counts[i]>1 else [])+
                                 ['medical_terms_not_verified']}
                for i,text in enumerate(outputs)]
