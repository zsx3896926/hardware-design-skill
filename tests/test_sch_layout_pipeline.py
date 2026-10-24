import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'design-hardware' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('sch_layout_pipeline',
                                              SCRIPTS / 'sch_layout_pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def before(anchor_y=224, pin_y=210):
    return {'components': [{
        'componentType': 'part', 'designator': 'D4', 'x': 900, 'y': anchor_y,
        'pins': [{'pinNumber': '1', 'x': 880, 'y': pin_y},
                 {'pinNumber': '2', 'x': 920, 'y': pin_y}],
    }]}


def page(anchor_x=0, anchor_y=0, pin_y=-15):
    return {'zones': [{'layout': {'placements': [{
        'designator': 'D4', 'x': anchor_x, 'y': anchor_y,
        'pins': [{'number': '1', 'x': -20, 'y': pin_y},
                 {'number': '2', 'x': 20, 'y': pin_y}],
    }]}}]}


class RigidTranslation(unittest.TestCase):
    def test_uniform_translation_passes(self):
        result = pipeline.verify_rigid_translation(page(pin_y=-14), before())
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['checked'], 1)

    def test_anchor_normalisation_is_detected(self):
        result = pipeline.verify_rigid_translation(page(pin_y=-15), before())
        self.assertEqual(result['status'], 'geometry-inconsistent')
        self.assertEqual(result['problems'][0]['reason'],
                         'anchor translation differs from pin translation')

    def test_missing_designator_is_reported(self):
        result = pipeline.verify_rigid_translation(page(), {'components': []})
        self.assertEqual(result['problems'][0]['reason'],
                         'missing from before snapshot')


if __name__ == '__main__':
    unittest.main()
