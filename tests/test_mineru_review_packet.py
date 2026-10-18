"""Risk selection must retain flagged records and controls without cherry-picking."""
import unittest
from scripts.build_mineru_review_packet import select


class ReviewSelectionTests(unittest.TestCase):
    def entries(self):
        return [{'id': f'entry-{n:02}', 'flags': ['test'] if n == 0 else [],
                 'parts': [f'part-{n // 5}']} for n in range(15)]

    def test_all_flags_are_included_and_controls_are_stratified(self):
        flagged, controls = select(self.entries())
        self.assertEqual([e['id'] for e in flagged], ['entry-00'])
        self.assertTrue(all(not e['flags'] for e in controls))
        self.assertEqual(len(controls), 6)
        self.assertEqual({e['parts'][0] for e in controls}, {'part-0', 'part-1', 'part-2'})

    def test_control_selection_is_stable_under_input_reordering(self):
        entries = self.entries()
        self.assertEqual(select(entries)[1], select(list(reversed(entries)))[1])

    def test_every_cross_part_control_is_kept_without_duplicates(self):
        entries = self.entries()
        for entry in entries[1:5]:
            entry['parts'].append('next-part')
        _, controls = select(entries)
        ids = [e['id'] for e in controls]
        self.assertTrue({e['id'] for e in entries[1:5]}.issubset(ids))
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == '__main__':
    unittest.main()
