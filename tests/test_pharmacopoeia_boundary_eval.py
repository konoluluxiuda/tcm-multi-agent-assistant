import unittest

from tcm.pharmacopoeia_boundary_eval import evaluate_boundaries


def visual(number=1, titles=None, block='a', printed=1):
    return {'document':'high','pdf_page':number,'printed_page':printed,'block':block,
            'titles':titles if titles is not None else [{'name':'甲药','column':'left','y_range':[.15,.25]}]}


def evidence(text='甲药', y=.2, number=1):
    return {'text':text,'document':'high','pdf_page':number,'column':'left',
            'center_y_fraction':y,'line_index':1}


def entry(identity='first', name='甲药', lines=None):
    values=lines or [evidence()]
    return {'id':identity,'name_zh_candidate':name,'evidence':[values[0]],'lines':values,
            'end_boundary':'batch_end'}


def reference(pages=None):
    return {'human_signoff':False,'frozen_before_ocr':True,'pages':pages or [visual()]}


class BoundaryEvaluationTests(unittest.TestCase):
    def test_location_match_does_not_hide_name_recognition_error(self):
        report=evaluate_boundaries(reference(),{'entries':[entry(name='申药')],'leading_continuations':[]})
        self.assertEqual(report['counts']['location_matches'],1)
        self.assertEqual(report['counts']['name_differences_among_matches'],1)
        self.assertIsNone(report['ocr_character_accuracy'])
        self.assertFalse(report['human_signoff'])

    def test_unknown_name_is_reported_as_identity_difference(self):
        report=evaluate_boundaries(reference(),{'entries':[entry(name=None)]})
        self.assertEqual(report['counts']['location_matches'],1)
        self.assertEqual(report['counts']['name_differences_among_matches'],1)
        self.assertIsNone(report['matched_starts'][0]['prediction']['name'])
        self.assertFalse(report['human_signoff'])

    def test_missed_title_inside_previous_entry_is_quarantined(self):
        ref=reference([visual(titles=[{'name':'甲药','column':'left','y_range':[.15,.25]},
                                     {'name':'乙药','column':'left','y_range':[.65,.75]}])])
        data={'entries':[entry(lines=[evidence(),evidence('乙药',.7)])]}
        report=evaluate_boundaries(ref,data)
        self.assertEqual(report['counts']['location_matches'],1)
        self.assertEqual(report['counts']['missed_starts'],1)
        self.assertEqual(report['missed_starts'][0]['impact'],'cross_entry_mixing')
        self.assertEqual(report['quarantine_entry_ids'],['first'])
        self.assertEqual(report['status'],'completed_with_boundary_failures')

    def test_multiple_predictions_in_one_window_are_not_counted_as_correct(self):
        data={'entries':[entry(),entry('second',lines=[evidence(y=.21)])]}
        report=evaluate_boundaries(reference(),data)
        self.assertEqual(report['counts']['location_matches'],0)
        self.assertEqual(report['counts']['ambiguous_reference_starts'],1)
        self.assertEqual(report['status'],'completed_with_boundary_failures')

    def test_negative_page_unexpected_title_and_leading_continuation(self):
        report=evaluate_boundaries(reference([visual(titles=[])]),{'entries':[entry()]})
        self.assertEqual(report['counts']['unexpected_starts'],1)
        self.assertEqual(report['status'],'completed_with_boundary_failures')
        report=evaluate_boundaries(reference(),{'entries':[],
            'leading_continuations':[{'lines':[evidence()]}]})
        self.assertTrue(report['missed_starts'][0]['found_in_leading_continuation'])

    def test_cross_block_join_is_an_error_not_ordinary_continuation(self):
        ref=reference([visual(),visual(2,[],block='b',printed=2)])
        data={'entries':[entry(lines=[evidence(),evidence('续文',.2,2)])]}
        report=evaluate_boundaries(ref,data)
        self.assertEqual(report['counts']['cross_block_entries'],1)
        self.assertIn('first',report['quarantine_entry_ids'])
        self.assertEqual(report['status'],'completed_with_boundary_failures')

    def test_coverage_invalid_geometry_and_printed_page_gap(self):
        with self.assertRaises(ValueError):evaluate_boundaries(reference(),{'entries':[entry(lines=[evidence(number=9)])]})
        bad=reference();bad['pages'][0]['titles'][0]['y_range']=[.9,.1]
        with self.assertRaises(ValueError):evaluate_boundaries(bad,{'entries':[]})
        ref=reference([visual(1,[],printed=27),visual(2,[],printed=30)])
        report=evaluate_boundaries(ref,{'entries':[]})
        self.assertEqual(len(report['observed_source_page_gaps']),1)
        self.assertEqual(report['counts']['reference_starts'],0)


if __name__=='__main__':unittest.main()
