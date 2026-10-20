"""Reverse queries count herb sources and retain bounded raw UniProt witnesses."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_etcm_enrichment import build as build_enrichment
from scripts.build_target_ingredient_index import build as build_targets
from tcm.herbs import import_snapshot
from tcm.relation_queries import parse, execute
from tcm.relation_memory import execute_turn


class TargetHerbTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.source = self.root/'source'
        (self.source/'etcm_herbs').mkdir(parents=True)
        self.originals = {}
        for i in range(23):
            rows = [{'Target Name': ['Declared target'], 'UniProt Accession Number': ['P12345'],
                     'Gene Symbol': ['EX1']} for _ in range(4 if i == 0 else 1)]
            # Names alone and ambiguous identifiers must not create reverse edges.
            rows += [{'Target Name': ['Declared target']},
                     {'Target Name': ['Other target'], 'UniProt Accession Number': ['P12345', 'Q12345']}]
            doc = {'code': 1, 'data': [{'id': 'base_information', 'value': [
                {'key': 'Herb Name in Pinyin', 'value': 'SameName' if i < 2 else 'Herb'+str(i)},
                {'key': 'Medicinal Part', 'value': 'Root'}]},
                {'id': 'related_table', 'value': [{'type': 'target', 'value': rows}]}]}
            if i != 2:doc['complete_fetch_summary'] = [{'section': 'related_table', 'type': 'target',
                                                        'status': 'skipped' if i == 1 else 'partial'}]
            path = self.source/'etcm_herbs'/f'Herb{i:02}.json';path.write_text(json.dumps(doc));self.originals[str(path.relative_to(self.source))] = doc
        self.base = self.root/'herbs.sqlite3';import_snapshot(self.source, self.base, 'v1')
        with redirect_stdout(io.StringIO()):build_enrichment(self.source, self.base, 'v1', self.root/'derived')
        self.enrichment = self.root/'derived/enrichment.sqlite3'
        with sqlite3.connect(self.enrichment) as con:
            fingerprint = json.loads(con.execute("SELECT value FROM metadata WHERE key='source_fingerprint'").fetchone()[0])
        audit = self.root/'audit';audit.mkdir();contents=[];rows=[]
        for i, (uniprot, gene) in enumerate([('P12345', 'EX1'), ('Q12345', 'EX2')]):
            identity = {'Target Name': 'Declared target '+str(i), 'Gene Symbol': gene, 'UniProt Accession Number': uniprot}
            sha = str(i)*64;file = 'etcm_targets/target'+str(i)+'.json'
            contents.append({'status':'source_identity_consistent_not_externally_verified','identity':identity,
                             'file_sha256':sha,'canonical_source':file})
            rows.append({'target':identity,'source_file':file,'source_file_sha256':sha,
                'ingredient_name':'Compound','raw_row':{'Ingredient Name':['Compound']},
                'json_pointer':'/data/1/value/0/value/0','capture_status':[{'status':'partial'}]})
        report={'status':'completed','contents':contents,'dataset_version':'v1','herb_source_fingerprint':fingerprint,
                'exported_target_ingredient_rows':2,'current_herb_uniprot_groups':3}
        (audit/'report.json').write_text(json.dumps(report));(audit/'target_ingredients.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.targets=self.root/'targets.sqlite3';build_targets(audit,self.targets)
        env=patch.dict(os.environ,{'TCM_DB':str(self.base),'TCM_DATASET_VERSION':'v1','ETCM_ENRICHMENT_DB':str(self.enrichment),
            'ETCM_TARGET_DB':str(self.targets),'RESEARCH_TOOLS_ONLY':'1','ENABLE_NATURAL_ROUTING':'1',
            'ENABLE_RESEARCH_BOOKMARKS':'1','RESEARCH_BOOKMARK_DB':str(self.root/'bookmarks.sqlite3')})
        env.start();self.addCleanup(env.stop)

    def test_parser_is_bounded_and_does_not_accept_treatment_questions(self):
        from tcm.assistant_router import resolve
        for text in ('CYP24A1关联哪些药材','靶点Q07973对应哪些药材','请查询TNF关联的药材有哪些'):
            self.assertEqual(parse(text)['kind'],'target_herb')
            self.assertEqual(resolve(text)['reason'],'etcm_relation_query')
        self.assertEqual(parse('/relations 靶点药材 EX1 第2页',explicit=True)['page'],2)
        self.assertEqual(parse('/relations 靶点药材 EX1 | EX2',explicit=True)['error'],'invalid_query')
        for text in ('EX1关联哪些药材并推荐治疗糖尿病','EX1可以用什么药','靶点芳香化酶对应哪些药材'):
            self.assertIsNone(parse(text))
        self.assertEqual(parse('/relations 靶点药材 EX1 第1001页',explicit=True)['error'],'invalid_query')

    def test_pages_count_sources_not_rows_or_names_and_preserve_witnesses(self):
        before=hashlib.sha256(self.base.read_bytes()).hexdigest()
        a=execute(parse('EX1关联哪些药材'))['relations']['data']
        b=execute(parse('/relations 靶点药材 P12345 第2页',explicit=True))['relations']['data']
        self.assertEqual((a['total'],len(a['items']),len(b['items'])),(23,20,3))
        items=a['items']+b['items'];self.assertEqual(len({r['source_record_id'] for r in items}),23)
        self.assertEqual(sum(r['name']=='SameName' for r in items),2)
        self.assertEqual(sum(r['source_row_count'] for r in items),26)
        repeated=next(r for r in items if r['source_row_count']==4)
        self.assertTrue(repeated['witnesses_truncated']);self.assertEqual(len(repeated['witnesses']),3)
        self.assertTrue(any(r['source_capture_status']==[] for r in items))
        self.assertTrue(any(r['source_capture_status']==[{'section':'related_table','type':'target','status':'skipped'}] for r in items))
        for item in items:
            original=self.originals[item['source']['relative_file_path']]
            for witness in item['witnesses']:
                raw=original
                for token in witness['source_pointer'].split('/')[1:]:raw=raw[int(token)] if isinstance(raw,list) else raw[token]
                self.assertEqual(witness['raw_row'],raw)
                self.assertEqual(raw['UniProt Accession Number'],['P12345'])
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(),before)

    def test_ambiguity_not_covered_and_zero_rows_have_different_meanings(self):
        zero=execute(parse('Q12345关联哪些药材'))
        self.assertEqual((zero['status'],zero['relations']['data']['total']),('ok',0))
        missing=execute(parse('P99999关联哪些药材'))
        self.assertEqual(missing['relations']['resolution_status'],'not_covered');self.assertNotIn('data',missing['relations'])
        with sqlite3.connect(self.targets) as con:con.execute("UPDATE targets SET gene='EX1',gene_key='EX1' WHERE uniprot='Q12345'")
        ambiguous=execute(parse('EX1关联哪些药材'))
        self.assertEqual(ambiguous['relations']['resolution_status'],'ambiguous_target');self.assertNotIn('data',ambiguous['relations'])
        self.assertEqual(len(ambiguous['relations']['options']),2)
        self.assertEqual(execute(parse('P12345关联哪些药材'))['relations']['data']['total'],23)

    def test_missing_and_stale_catalog_or_base_denominator_fail_closed(self):
        request=parse('EX1关联哪些药材')
        for key in ('ETCM_TARGET_DB','ETCM_ENRICHMENT_DB'):
            with patch.dict(os.environ,{key:''}):self.assertEqual(execute(request)['status'],'unavailable')
        missing=self.root/'missing.sqlite3'
        with patch.dict(os.environ,{'TCM_DB':str(missing)}):self.assertEqual(execute(request)['status'],'unavailable')
        self.assertFalse(missing.exists())
        with patch.dict(os.environ,{'TCM_DATASET_VERSION':'v2'}):self.assertEqual(execute(request)['status'],'unavailable')
        # Even a source not visible on page 1 invalidates the total count.
        with sqlite3.connect(self.base) as con:
            identifier=con.execute('SELECT source_record_id FROM records ORDER BY source_record_id DESC LIMIT 1').fetchone()[0]
            con.execute("UPDATE records SET sha256=? WHERE source_record_id=?",('f'*64,identifier))
        self.assertEqual(execute(request)['status'],'unavailable')

    def test_focus_backup_and_evidence_note_remain_target_bound(self):
        from tcm.agent_research import pack_contexts
        from tcm.research_bookmarks import capture,preview
        from tcm.bookmark_backup import encode,decode
        from tcm.bookmark_report import export_note
        _,focus=execute_turn(parse('/relations 靶点药材 EX1 第2页',explicit=True),None)
        self.assertEqual(focus['herb_sources'],[]);self.assertEqual(focus['uniprot'],'P12345')
        self.assertNotIn('raw_row',json.dumps(focus))
        item={'title':'靶点来源','note':'PRIVATE_NOTE','payload':capture(pack_contexts(None,None,None,focus))}
        restored=decode(encode([item]))[0]
        self.assertEqual(preview(restored['payload'],None)['assistant']['relations']['data']['total'],23)
        note=export_note({**restored,'revision':1},None)
        self.assertIn('不是去重药材种数',note);self.assertIn('本页返回：3项',note);self.assertNotIn('PRIVATE_NOTE',note)
        result,_=execute_turn({'followup':True,'action':'paths'},focus)
        self.assertEqual(result['status'],'clarification_needed')
        with sqlite3.connect(self.targets) as con:con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='export_sha256'")
        result,new=execute_turn({'followup':True,'action':'replay'},focus)
        self.assertEqual(result['status'],'clarification_needed');self.assertIsNone(new)

    def test_chat_restore_bookmark_preview_and_download_are_model_free(self):
        import app
        from agents import agent_decision as ad
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        def deny(*a,**kw):raise AssertionError('Reverse query invoked model')
        memory=ConversationStore(self.root/'memory.sqlite3')
        with patch.object(ad,'memory',memory),patch.object(ad.config.agent_decision,'llm',RunnableLambda(deny)), \
             patch.object(ad.config.conversation,'llm',RunnableLambda(deny)),patch.object(ad.LocalGuardrails,'check_input',deny), \
             patch.object(ad.LocalGuardrails,'check_output',deny),TestClient(app.app) as client,redirect_stdout(io.StringIO()):
            response=client.post('/chat',json={'query':'EX1关联哪些药材'})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['assistant']['llm_calls'],0)
            with patch.object(ad,'memory',ConversationStore(memory.path)):
                self.assertEqual(client.get('/conversation').json()['assistant']['relations']['data']['total'],23)
                self.assertEqual(client.post('/chat',json={'query':'出处呢'}).json()['assistant']['relations']['kind'],'target_herb')
            state=client.get('/api/research/bookmarks').json()
            saved=client.post('/api/research/bookmarks',json={'expected_revision':state['conversation_revision'],'title':'反查','note':''})
            self.assertEqual(saved.status_code,200,saved.text);identifier=saved.json()['item']['id']
            self.assertEqual(client.post('/api/research/bookmarks/'+identifier+'/preview').status_code,200)
            report=client.get('/api/research/bookmarks/'+identifier+'/report?revision=1')
            self.assertEqual(report.status_code,200);self.assertIn('不是去重药材种数',report.text)
            snapshot=memory.load_serialized(client.cookies[app.SESSION_COOKIE_NAME])
            self.assertNotIn('raw_row',json.dumps(snapshot.tcm_context));self.assertNotIn('Declared target',json.dumps(snapshot.messages))


if __name__=='__main__':unittest.main()
