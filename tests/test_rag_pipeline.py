from copy import deepcopy
import json
import unittest

from tcm.rag_pipeline import RagPipeline,assemble,validate,payload,LIMITS


def result():
    return {'status':'evidence_found','method':'bm25','request':{},'latency_ms':1,
            'release_content_id':'release','index_sha256':'index','structured_hits':[],
            'hits':[{'kind':'pdf_field','entry_id':name,'name':name,'label':'贮藏','scope':'prepared_slices',
                     'text':text,'chunk_id':name+':field','evidence':[{'block_id':name,'source':{'source_pdf_page':2}}]}
                    for name,text in [('甲草','【贮藏】置干燥处。'),('乙草','【贮藏】置通风干燥处。')]]}


class Search:
    def __init__(self,value=None):self.value=value or result();self.calls=0
    def search(self,*args,**kwargs):self.calls+=1;return self.value


def draft(p):
    return {'findings':[{'kind':'shared_phrase','phrase':'干燥',
                         'supports':[{'evidence_id':s['id'],'quote':s['text']} for s in p['sources']]}]}


class Model:
    def __init__(self,answer=draft):
        self.answer=answer;self.calls=0;self.stats={'requests':0,'outcome':'not_dispatched','usage':{}}
    def generate(self,p):
        self.calls+=1;self.stats.update(requests=1,outcome='http_200')
        return {'content':json.dumps(self.answer(p),ensure_ascii=False),'stats':self.stats}


class PipelineTests(unittest.TestCase):
    def test_original_has_sources_and_no_model_request(self):
        model=Model();r=RagPipeline(Search(),model).run('比较甲草乙草贮藏')
        self.assertEqual(r['status'],'original');self.assertEqual(model.calls,0)
        self.assertIn('[E1]',r['message']);self.assertEqual(r['bundle']['sources'][0]['provenance']['chunk_id'],'甲草:field')

    def test_composition_is_one_request_with_checked_literal_comparison(self):
        model=Model();r=RagPipeline(Search(),model).run('比较两条记录',True)
        self.assertEqual(r['status'],'composed');self.assertEqual(model.calls,1)
        self.assertIn('共有原文片段：“干燥”',r['message'])
        self.assertFalse(r['validation']['free_paraphrase_semantics_verified'])
        self.assertEqual(set(payload('问题',r['bundle'])['sources'][0]),{'id','kind','name','label','scope','text','truncated'})

    def test_repeated_supports_are_rendered_once_without_provider_retest(self):
        def answer(p):
            d=draft(p);d['findings'].append({'kind':'parallel_records','supports':d['findings'][0]['supports']})
            return d
        r=RagPipeline(Search(),Model(answer)).run('比较资料',True)
        self.assertEqual(r['message'].count('【贮藏】置干燥处。'),1)
        self.assertNotIn('原始记录对照：',r['message'])

    def test_forged_reference_quote_shared_phrase_or_free_prose_falls_back_without_retry(self):
        def modified(change):
            def answer(p):d=draft(p);change(d);return d
            return answer
        changes=[lambda d:d['findings'][0]['supports'][0].update(evidence_id='E6'),
                 lambda d:d['findings'][0]['supports'][0].update(quote='药效保证。'),
                 lambda d:d['findings'][0].update(phrase='治愈'),
                 lambda d:d.update(conclusion='具有临床疗效'),
                 lambda d:d['findings'][0]['supports'].pop()]
        for change in changes:
            model=Model(modified(change));r=RagPipeline(Search(),model).run('比较资料',True)
            self.assertEqual(r['status'],'original_fallback');self.assertEqual(model.calls,1)
            self.assertIn('【贮藏】',r['message']);self.assertNotIn('临床疗效',r['message'])

    def test_missing_ambiguous_and_out_of_scope_do_not_call_model(self):
        for status in ('missing_field','clarification_required','reference_unresolved'):
            source=result();source.update(status=status,hits=[])
            model=Model();r=RagPipeline(Search(source),model).run('查甲草的注意',True)
            self.assertEqual(r['status'],status);self.assertEqual(model.calls,0)
        search=Search();r=RagPipeline(search,Model()).run('帮我治愈肺癌',True)
        self.assertEqual(r['status'],'out_of_scope');self.assertEqual(search.calls,0)

    def test_disabled_partial_and_truncated_evidence_return_originals_without_generation(self):
        r=RagPipeline(Search()).run('比较资料',True)
        self.assertEqual(r['reason'],'generation_disabled')
        for update in ('partial','long','many'):
            source=result()
            if update=='partial':source['status']='partial_evidence'
            elif update=='long':source['hits'][0]['text']='正文'*700+'。'
            else:source['hits']=source['hits']*4
            model=Model();r=RagPipeline(Search(source),model).run('比较资料',True)
            self.assertEqual(r['status'],'original_fallback');self.assertEqual(model.calls,0)
            self.assertLessEqual(sum(len(s['text']) for s in r['bundle']['sources']),LIMITS['total_source_chars'])

    def test_timeout_keeps_unknown_request_and_returns_originals(self):
        class Timeout(Model):
            def generate(self,p):self.calls+=1;self.stats.update(requests=1,outcome='unknown');raise TimeoutError('secret')
        model=Timeout();r=RagPipeline(Search(),model).run('比较资料',True)
        self.assertEqual(r['stats']['outcome'],'unknown');self.assertEqual(model.calls,1)
        self.assertNotIn('secret',json.dumps(r));self.assertEqual(r['status'],'original_fallback')

    def test_clipped_negation_and_cross_kind_common_phrase_rejected(self):
        source=result();source['hits'][0]['text']='【注意】不宜与乙草同用。'
        b=assemble(source)
        bad={'findings':[{'kind':'excerpt','supports':[{'evidence_id':'E1','quote':'宜与乙草同用。'}]},
                         {'kind':'excerpt','supports':[{'evidence_id':'E2','quote':b['sources'][1]['text']}]}]}
        with self.assertRaisesRegex(ValueError,'complete'):validate(bad,b)
        b=assemble(result());d=draft(payload('问题',b));b['sources'][1]['kind']='etcm_json'
        with self.assertRaises(ValueError):validate(d,b)

    def test_structured_raw_value_and_pointer_not_rewritten(self):
        source=result();source['hits']=[]
        source['structured_hits']=[{'kind':'etcm_json','raw_value':'<i>Root</i>','label':'medicinal_part',
                 'source_record_id':'sid','relative_file_path':'etcm_herbs/Alpha.json','file_sha256':'sha','json_pointer':'/a/0'}]
        b=assemble(source);self.assertEqual(b['sources'][0]['text'],'<i>Root</i>')
        self.assertEqual(b['sources'][0]['provenance']['json_pointer'],'/a/0')
        with self.assertRaisesRegex(ValueError,'in full'):
            validate({'findings':[{'kind':'excerpt','supports':[{'evidence_id':'E1','quote':'Root'}]}]},b)


if __name__=='__main__':unittest.main()
