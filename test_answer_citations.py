"""Critical citation validation checks; no API calls or fake answers in results."""
import unittest
from answer_course import check_citations


class CitationChecks(unittest.TestCase):
    def test_unknown_citation_is_rejected(self):
        result = check_citations('数值[E7]', [{'evidence_id': 'E1'}])
        self.assertFalse(result['passed'])
        self.assertEqual(result['unknown_ids'], ['E7'])

    def test_uncited_answer_is_rejected(self):
        self.assertFalse(check_citations('数值', [{'evidence_id': 'E1'}])['passed'])

    def test_grouped_citations_are_rejected(self):
        self.assertFalse(check_citations('数值[E1,E2]', [{'evidence_id': 'E1'}, {'evidence_id': 'E2'}])['passed'])

    def test_grounded_citations_and_insufficient_answer(self):
        self.assertTrue(check_citations('甲[E1]与乙[E2]', [{'evidence_id': 'E1'}, {'evidence_id': 'E2'}])['passed'])
        self.assertTrue(check_citations('缺少证据。', [], insufficient=True)['passed'])


if __name__ == '__main__':
    unittest.main()
