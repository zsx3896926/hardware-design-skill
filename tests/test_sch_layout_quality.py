import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'design-hardware' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('sch_layout_quality',
                                              SCRIPTS / 'sch_layout_quality.py')
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


def snapshot():
    return {
        'coordinate_units': 'raw',
        'page': {'bbox': [0, 0, 100, 100], 'title_block': [50, 0, 100, 20]},
        'components': [
            {'id': 'a', 'designator': 'U1', 'bbox': [80, 70, 95, 95],
             'text_bboxes': [[80, 95, 95, 99]], 'pins': []},
            {'id': 'b', 'designator': 'R1', 'bbox': [70, 80, 90, 95],
             'text_bboxes': [[70, 95, 90, 99]], 'pins': []},
            {'id': 'c', 'designator': 'C1', 'bbox': [75, 10, 95, 18],
             'text_bboxes': [[75, 18, 95, 19]], 'pins': []},
        ],
        'wires': [
            {'net': 'N1', 'points': [[10, 50], [60, 50]]},
            {'net': 'N2', 'points': [[20, 40], [20, 60]]},
        ],
        'flags': [{'net': 'GND', 'kind': 'ground', 'x': 5, 'y': 5}],
        'ports': [],
        'modules': [
            {'id': 'm1', 'title': 'right', 'core': 'U1', 'members': ['U1', 'R1']},
            {'id': 'm2', 'title': 'title', 'core': 'C1', 'members': ['C1']},
        ],
        'reading_order': ['m1', 'm2'],
    }


class LayoutQuality(unittest.TestCase):
    def test_reports_the_failure_modes_the_agent_scorer_misses(self):
        result = quality.analyze(snapshot())
        codes = {finding['code'] for finding in result['findings']}
        self.assertIn('symbol_overlap', codes)
        self.assertIn('component_over_title_block', codes)
        self.assertIn('module_over_title_block', codes)
        self.assertIn('page_imbalance', codes)
        self.assertEqual(result['counts']['error'] >= 3, True)
        self.assertEqual(result['coverage']['text_bboxes'], True)

    def test_clean_spread_layout_has_no_errors(self):
        data = {
            'coordinate_units': 'raw',
            'page': {'bbox': [0, 0, 200, 100], 'title_block': [120, 0, 200, 20]},
            'components': [
                {'id': 'a', 'designator': 'U1', 'bbox': [20, 40, 60, 80],
                 'text_bboxes': [[20, 81, 50, 88]], 'pins': []},
                {'id': 'b', 'designator': 'R1', 'bbox': [90, 40, 110, 55],
                 'text_bboxes': [[90, 56, 108, 62]], 'pins': []},
            ],
            'wires': [],
            'modules': [{'id': 'm', 'title': 'm', 'core': 'U1', 'members': ['U1', 'R1']}],
        }
        result = quality.analyze(data)
        self.assertEqual([f for f in result['findings'] if f['severity'] == 'error'], [])

    def test_compare_lists_resolved_and_introduced(self):
        before = quality.analyze(snapshot())
        fixed = snapshot()
        fixed['components'] = [
            {'id': 'a', 'designator': 'U1', 'bbox': [10, 60, 30, 80],
             'text_bboxes': [[10, 81, 30, 88]], 'pins': []},
            {'id': 'c', 'designator': 'C1', 'bbox': [40, 60, 50, 70],
             'text_bboxes': [[40, 71, 50, 76]], 'pins': []},
        ]
        fixed['modules'] = [{'id': 'm1', 'title': 'left', 'core': 'U1',
                             'members': ['U1', 'C1']}]
        fixed['reading_order'] = ['m1']
        after = quality.analyze(fixed)
        delta = quality.compare(before, after)
        self.assertIn('symbol_overlap', delta['resolved'])
        self.assertIn('component_over_title_block', delta['resolved'])

    def test_invalid_snapshot_is_rejected(self):
        data = snapshot()
        data['components'][0]['bbox'] = [10, 10, 5, 5]
        with self.assertRaises(ValueError):
            quality.analyze(data)


if __name__ == '__main__':
    unittest.main()
