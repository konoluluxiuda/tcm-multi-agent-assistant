import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from scripts.batch_pharmacopoeia_pdf import run
from tcm.pharmacopoeia import digest

from scripts.batch_pharmacopoeia_pdf import atomic_json, identity_for, load_cache, validate_plan
from tcm.pharmacopoeia_segmentation import fingerprint, page_lines, segment_pages


def line(text, y, column='left'):
    x = 50 if column == 'left' else 350
    return {'text': text, 'score': .99, 'pdf_box': [[x,y],[x+180,y],[x+180,y+10],[x,y+10]]}


def title(name='测试药', y=100, column='left', latin=('TEST HERBA',)):
    texts = [name, 'Ceshiyao', *latin, '本品为植物的干燥全草。', '【性状】绿色。']
    return [line(t, y+i*20, column) for i,t in enumerate(texts)]


def page(number=1, lines=None, document='a', printed_page=None):
    return {'document': document, 'source_sha256': document*64, 'pdf_page': number,
            'printed_page': printed_page, 'pdf_width': 600, 'pdf_height': 800,
            'page_result_sha256': str(number)*64, 'lines': lines if lines is not None else title()}


class SegmentationTests(unittest.TestCase):
    def test_running_headers_and_footer_excluded_but_bottom_dose_retained(self):
        data=page(lines=[line('另一味药',45), *title(),line('10~15g。',755),line('3',755),line('·3·',775)])
        kept, excluded=page_lines(data)
        self.assertEqual([x['text'] for x in excluded],['另一味药','·3·'])
        self.assertIn('10~15g。',[x['text'] for x in kept])
        self.assertIn('3',[x['text'] for x in kept])

    def test_cross_page_continuation_and_two_line_latin(self):
        a=page(lines=title()+[line('【用法与用量】3~',740)])
        b=page(2,lines=[line('6g。',95),*title('下味药',200,latin=('SOPHORAE TONKINENSIS','RADIX ET RHIZOMA'))])
        result=segment_pages([a,b]);self.assertEqual(result['metrics']['entries'],2)
        first=result['entries'][0]
        self.assertEqual(first['end_boundary'],'next_title')
        self.assertEqual(first['lines'][-1]['text'],'6g。')
        self.assertEqual(result['entries'][1]['boundary_status'],'open_requires_following_context')

    def test_column_order_and_slices_scope(self):
        lines=title()+[line('【贮藏】干燥处。',300),line('饮片',350),line('【用法与用量】3g。',390)]
        lines+=title('其他药',200,'right')
        result=segment_pages([page(lines=list(reversed(lines)))])
        self.assertEqual([x['name_zh_candidate'] for x in result['entries']],['测试药','其他药'])
        slices=result['entries'][0]['sections'][1]
        self.assertEqual(slices['scope'],'prepared_slices')
        self.assertEqual(slices['fields']['origin']['occurrences'],[])
        self.assertEqual(slices['fields']['usage']['occurrences'][0]['raw_text'],'3g。')

    def test_auxiliary_standard_not_independent_herb(self):
        result=segment_pages([page(lines=title()+[line('附1：胆红素质量标准',300),line('胆红素',340),line('本品由胆红素加工。',370)])])
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(result['entries'][0]['sections'][1]['scope'],'appendix_standard')

    def test_manufactured_monograph_source_opener(self):
        lines=title('人工牛黄')
        lines[3]['text']='本品由牛胆粉等加工制成。'
        result=segment_pages([page(lines=lines)])
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(result['entries'][0]['sections'][0]['fields']['origin']['occurrences'][0]['raw_text'],
                         '本品由牛胆粉等加工制成。')

    def test_gaps_and_sources_never_join(self):
        for next_page in (page(3,lines=[line('续文',100)]),page(2,document='b',lines=[line('续文',100)]),
                          page(2,printed_page=30,lines=[line('续文',100)])):
            result=segment_pages([page(1,printed_page=27),next_page])
            self.assertEqual(result['entries'][0]['end_boundary'],'source_or_page_gap')
            self.assertEqual(len(result['leading_continuations']),1)
            self.assertNotIn('续文',[x['text'] for x in result['entries'][0]['lines']])

    def test_leading_orphans_not_assigned_to_new_title(self):
        result=segment_pages([page(lines=[line('前味药续文',90),*title(y=200)])])
        self.assertEqual(result['leading_continuations'][0]['lines'][0]['text'],'前味药续文')
        self.assertNotIn('前味药续文',[x['text'] for x in result['entries'][0]['lines']])

    def test_cross_column_triplet_is_not_a_title(self):
        lines=title()[:2]+[line('TEST HERBA',140,'right'),line('本品为草。',160,'right')]
        self.assertEqual(segment_pages([page(lines=lines)])['entries'],[])

    def test_missing_source_and_short_name_cut_without_inventing_identity(self):
        first=title('前味药')+[line('【贮藏】阴凉处。',260)]
        second=title('白',400)
        second[3]['text']='断开的来源续文。'
        data=segment_pages([page(lines=first+second)])
        self.assertEqual(len(data['entries']),2)
        self.assertNotIn('白',[l['text'] for l in data['entries'][0]['lines']])
        recovered=data['entries'][1]
        self.assertEqual(recovered['identity_status'],'unresolved')
        self.assertEqual(recovered['review_status'],'requires_source_review')
        self.assertEqual(recovered['source_status'],'unresolved_missing_source_opener')
        self.assertEqual(recovered['sections'][0]['fields']['origin']['occurrences'],[])
        self.assertEqual(recovered['sections'][0]['fields']['pinyin']['occurrences'][0]['raw_text'],'Ceshiyao')
        self.assertIsNone(recovered['canonical_entity_mapping'])

    def test_source_opener_variants_are_fields_not_boundary_requirements(self):
        for opener in ('本品以','本品系'):
            lines=title();lines[3]['text']=opener+'相关材料加工。'
            data=segment_pages([page(lines=lines)])['entries'][0]
            self.assertEqual(data['sections'][0]['fields']['origin']['occurrences'][0]['raw_text'],opener+'相关材料加工。')
            self.assertEqual(data['source_status'],'unreviewed_source_candidate')

    def test_title_at_page_end_can_receive_source_on_following_page(self):
        first=page(lines=title(y=700)[:3])
        second=page(2,lines=[line('本品为植物的干燥全草。',100),line('【性状】绿色。',130)])
        data=segment_pages([first,second])['entries'][0]
        self.assertEqual(data['source_status'],'unreviewed_source_candidate')
        self.assertNotIn('origin_opener_not_observed',data['title_flags'])
        self.assertEqual(data['sections'][0]['fields']['origin']['occurrences'][0]['evidence'][0]['pdf_page'],2)

    def test_body_triplet_with_misalignment_or_large_gap_is_rejected(self):
        lines=title()
        for point in lines[1]['pdf_box']:point[0]+=80
        self.assertEqual(segment_pages([page(lines=lines)])['entries'],[])
        lines=title();lines[1]=line('Ceshiyao',170);lines[2]=line('TEST HERBA',190)
        self.assertEqual(segment_pages([page(lines=lines)])['entries'],[])

    def test_unknown_source_and_low_score_preserve_raw_ocr_for_review(self):
        lines=title('错字药');lines[0]['score']=.7;lines[3]['text']='本品采用另一工艺。'
        data=segment_pages([page(lines=lines)])['entries'][0]
        self.assertEqual(data['name_zh_candidate'],'错字药')
        self.assertIn('low_confidence_title_identity',data['title_flags'])
        self.assertEqual(data['source_status'],'unresolved_missing_source_opener')
        self.assertFalse(data['human_signoff'])

    def test_parenthetical_title_is_one_unmapped_monograph(self):
        lines=title('药材（别名）')
        result=segment_pages([page(lines=lines)])['entries']
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['title_name_parts'],{'primary_candidate':'药材','parenthetical_candidate':'别名'})
        self.assertEqual(result[0]['evidence'][0]['text'],'药材（别名）')
        self.assertIsNone(result[0]['canonical_entity_mapping'])

    def test_damaged_pinyin_is_a_boundary_but_never_valid_identity(self):
        lines=title();lines[1]['text']='!zbu!x!r'
        result=segment_pages([page(lines=lines)])['entries'][0]
        self.assertEqual(result['pinyin_status'],'unresolved_ocr')
        self.assertEqual(result['identity_status'],'unresolved')
        field=result['sections'][0]['fields']['pinyin']
        self.assertEqual(field['state'],'unresolved_ocr_value')
        self.assertEqual(field['occurrences'][0]['raw_text'],'!zbu!x!r')
        self.assertEqual(result['review_status'],'requires_source_review')

    def test_fallback_requires_source_and_balanced_title(self):
        lines=title();lines[1]['text']='!zbu!x!r';lines[3]['text']='无来源的技术续文。'
        self.assertEqual(segment_pages([page(lines=lines)])['entries'],[])
        lines=title('药材（别名')
        fallback=segment_pages([page(lines=lines)])['entries'][0]
        self.assertIsNone(fallback['name_zh_candidate'])
        self.assertEqual(fallback['identity_status'],'unresolved')
        lines=title();lines[1]['text']='[formula]'
        self.assertEqual(segment_pages([page(lines=lines)])['entries'],[])

    def test_missing_entire_chinese_title_cuts_with_explicit_unknown_identity(self):
        first=title('前药')+[line('【贮藏】干燥处。',300)]
        tail=title('未知药',400)[1:]
        result=segment_pages([page(lines=first+tail)])['entries']
        self.assertEqual(len(result),2)
        recovered=result[1]
        self.assertIsNone(recovered['name_zh_candidate'])
        self.assertIn('chinese_title_not_observed',recovered['title_flags'])
        self.assertEqual(recovered['identity_status'],'unresolved')
        self.assertEqual(recovered['sections'][0]['fields']['pinyin']['occurrences'][0]['raw_text'],'Ceshiyao')
        self.assertEqual(result[0]['end_boundary'],'next_title')
        # The pinyin line in a complete title must not create a duplicate entry.
        self.assertEqual(len(segment_pages([page()])['entries']),1)

    def test_missing_name_fallback_needs_source_in_same_column(self):
        lines=title()[1:];lines[2]['text']='未观察到来源开头。'
        self.assertEqual(segment_pages([page(lines=lines)])['entries'],[])

    def test_bad_geometry_rejected_and_output_deterministic(self):
        original=page();self.assertEqual(fingerprint(segment_pages([original])),fingerprint(segment_pages([original])))
        broken=copy.deepcopy(original);broken['lines'][0]['pdf_box'][0][0]=-1
        with self.assertRaises(ValueError):segment_pages([broken])


class CacheTests(unittest.TestCase):
    def test_corruption_recipe_and_completed_file_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.json';result=page()
            identity=identity_for({'document':'a','pdf_page':1},{'sha256':'a'*64},'recipe')
            value={'identity':identity,'result':result,'result_sha256':fingerprint(result)}
            atomic_json(path,value);self.assertEqual(load_cache(path,identity)['result'],result)
            with self.assertRaises(ValueError):load_cache(path,{**identity,'recipe_sha256':'changed'})
            with self.assertRaises(ValueError):load_cache(path,identity,'0'*64)
            value['result']['lines'][0]['text']='修改';atomic_json(path,value)
            with self.assertRaises(ValueError):load_cache(path,identity)

    def test_plan_rejects_duplicate_and_reordered_pages(self):
        plan={'schema_version':1,'render_dpi':240,'chapter_profile':'herb_monographs_two_column_v1','documents':{'a':{}},
              'pages':[{'document':'a','pdf_page':2},{'document':'a','pdf_page':1}]}
        with self.assertRaises(ValueError):validate_plan(plan)
        plan['pages'][1]['pdf_page']=2
        with self.assertRaises(ValueError):validate_plan(plan)



class ResumeTests(unittest.TestCase):
    def test_partial_resume_noop_and_failed_page_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.pdf';source.write_bytes(b'fixture')
            plan=root/'plan.json';atomic_json(plan,{'schema_version':1,'render_dpi':240,'chapter_profile':'herb_monographs_two_column_v1',
                'documents':{'a':{'path':str(source),'sha256':digest(source)}},
                'pages':[{'document':'a','pdf_page':i} for i in (1,2,3)]})
            class Document:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def __getitem__(self,i):return self
                rect=SimpleNamespace(width=600,height=800)
                def get_pixmap(self,**kwargs):return self
                def save(self,path):Path(path).write_bytes(b'fixture image')
            def recognize_fixture(engine_name,engine,image,meta):
                if meta['pdf_page']==2:raise RuntimeError('synthetic OCR failure')
                return {'lines':title(),'blocks':[],'reading_text':'fixture'}
            with patch('scripts.batch_pharmacopoeia_pdf.importlib.metadata.version',return_value='fixture'), \
                 patch('scripts.batch_pharmacopoeia_pdf.model_fingerprints',return_value={'model':'fixture'}), \
                 patch('scripts.batch_pharmacopoeia_pdf.make_engine',return_value=object()) as engine, \
                 patch('scripts.batch_pharmacopoeia_pdf.recognize',side_effect=recognize_fixture) as recognize_mock, \
                 patch.dict('sys.modules',{'pymupdf':SimpleNamespace(open=lambda path:Document())}):
                out=root/'out'
                first=run(plan,out,max_new_pages=1)
                self.assertEqual(first['status'],'partial');self.assertEqual(first['new_ocr_pages'],1)
                second=run(plan,out)
                self.assertEqual(second['status'],'failed_pages');self.assertEqual(second['completed_pages'],2)
                parsed=json.loads((out/'entries.json').read_text())
                self.assertEqual(parsed['entries'][0]['end_boundary'],'source_or_page_gap')
                recognize_mock.side_effect=lambda *args: {'lines':title(),'blocks':[],'reading_text':'fixed'}
                third=run(plan,out)
                self.assertEqual(third['status'],'completed');self.assertEqual(third['new_ocr_pages'],1)
                engine.reset_mock();recognize_mock.reset_mock()
                fourth=run(plan,out,max_new_pages=0)
                self.assertEqual(fourth['cached_pages'],3);self.assertEqual(fourth['new_ocr_pages'],0)
                self.assertEqual(third['entries_sha256'],fourth['entries_sha256'])
                engine.assert_not_called();recognize_mock.assert_not_called()
                # A changed plan cannot silently reuse a completed run.
                spec=json.loads(plan.read_text());spec['pages'].append({'document':'a','pdf_page':4});atomic_json(plan,spec)
                with self.assertRaises(ValueError):run(plan,out)


class ChapterProfileTests(unittest.TestCase):
    def plan(self):
        return {'schema_version':1,'render_dpi':240,'chapter_profile':'herb_monographs_two_column_v1',
                'documents':{'a':{}},'pages':[{'document':'a','pdf_page':12,'layout':'two_column'}]}

    def test_new_plan_requires_chapter_declaration(self):
        spec=self.plan();del spec['chapter_profile']
        with self.assertRaisesRegex(ValueError,'chapter_profile'):validate_plan(spec)

    def test_single_column_page_cannot_enter_two_column_extractor(self):
        spec=self.plan();spec['pages'][0]['layout']='single_column'
        with self.assertRaisesRegex(ValueError,'Mixed chapter or layout'):validate_plan(spec)

    def test_same_columns_do_not_make_different_chapters_compatible(self):
        spec=self.plan();spec['chapter_profile']='formulations_two_column_v1'
        with self.assertRaisesRegex(ValueError,'not supported'):validate_plan(spec)
        spec=self.plan();spec['pages'][0]['chapter_profile']='latin_index_two_column_v1'
        with self.assertRaisesRegex(ValueError,'Mixed chapter or layout'):validate_plan(spec)

    def test_supported_profile_and_exact_historical_replay(self):
        validate_plan(self.plan())
        original=json.loads((Path(__file__).resolve().parents[1]/'eval/pharmacopoeia_batch_pilot_v1.json').read_text())
        validate_plan(original)
        original['pages'].append({'document':'high','pdf_page':30})
        with self.assertRaisesRegex(ValueError,'chapter_profile'):validate_plan(original)

if __name__=='__main__':unittest.main()
