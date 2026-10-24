import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'design-hardware' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('sch_layout_model',
                                              SCRIPTS / 'sch_layout_model.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def snapshot(anchor_y=200):
    return {
        'components': [
            {'componentType': 'part', 'designator': 'R1', 'primitiveId': 'p1',
             'x': 100, 'y': anchor_y, 'rotation': 0, 'mirror': False,
             'bbox': {'minX': 92.5, 'minY': 195.5, 'maxX': 107.5, 'maxY': 204.5},
             'pins': [
                 {'pinNumber': '1', 'pinName': '1', 'net': 'N1', 'x': 90, 'y': 200,
                  'rotation': 180, 'noConnected': False},
                 {'pinNumber': '2', 'pinName': '2', 'net': 'GND', 'x': 110, 'y': 200,
                  'rotation': 0, 'noConnected': False},
             ]},
            {'componentType': 'part', 'designator': 'C1', 'primitiveId': 'p2',
             'x': 130, 'y': 200, 'rotation': 0, 'mirror': False,
             'bbox': {'minX': 124.5, 'minY': 195.5, 'maxX': 135.5, 'maxY': 204.5},
             'pins': [
                 {'pinNumber': '1', 'pinName': '1', 'net': 'N1', 'x': 120, 'y': 200,
                  'rotation': 180, 'noConnected': False},
                 {'pinNumber': '2', 'pinName': '2', 'net': 'GND', 'x': 140, 'y': 200,
                  'rotation': 0, 'noConnected': False},
             ]},
        ],
        'wires': [{'primitiveId': 'w1', 'net': 'N1', 'rawLine': [90, 200, 120, 200]}],
    }


def connectivity():
    return {
        'schemaVersion': '1.4',
        'components': [{'id': 'cmp-R1', 'ref': 'R1'}, {'id': 'cmp-C1', 'ref': 'C1'}],
        'nets': [{'id': 'net-n1', 'name': 'N1'}, {'id': 'net-g', 'name': 'GND'}],
        'connections': [
            {'componentId': 'cmp-R1', 'pinNumber': '1', 'netId': 'net-n1'},
            {'componentId': 'cmp-R1', 'pinNumber': '2', 'netId': 'net-g'},
            {'componentId': 'cmp-C1', 'pinNumber': '1', 'netId': 'net-n1'},
            {'componentId': 'cmp-C1', 'pinNumber': '2', 'netId': 'net-g'},
        ],
    }


def designators():
    return {'count': 2, 'designators': [
        {'parentId': 'p1', 'value': 'R1', 'bbox': {'minX': 95, 'minY': 205, 'maxX': 105, 'maxY': 210}},
        {'parentId': 'p2', 'value': 'C1', 'bbox': {'minX': 125, 'minY': 205, 'maxX': 135, 'maxY': 210}},
    ]}


def sheet():
    return {'sheet': {'bbox': {'minX': 0, 'minY': 0, 'maxX': 200, 'maxY': 100},
                      'innerBBox': {'minX': 5, 'minY': 5, 'maxX': 195, 'maxY': 95}},
            'titleBlock': {'bbox': {'minX': 100, 'minY': 5, 'maxX': 195, 'maxY': 25}},
            'keepouts': []}


def plan(members=None):
    return {'zones': [{'id': 'm', 'title': 'm', 'core': 'R1',
                       'members': members or ['R1', 'C1'],
                       'netPolicies': {'N1': 'direct'}}],
            'defaultNetPolicies': {'GND': 'local_ground'},
            'readingOrder': ['m']}


class BuildModel(unittest.TestCase):
    def build(self, snap=None, conn=None, des=None, zone_plan=None):
        with tempfile.TemporaryDirectory() as temp:
            paths = {}
            for name, data in (('snapshot', snap or snapshot()),
                               ('connectivity', conn or connectivity()),
                               ('designators', des or designators()),
                               ('sheet', sheet()),
                               ('plan', zone_plan or plan())):
                path = Path(temp) / f'{name}.json'
                path.write_text(json.dumps(data), encoding='utf-8')
                paths[name] = path
            return model.build(paths['snapshot'], paths['connectivity'],
                               paths['designators'], paths['sheet'], paths['plan'])

    def test_builds_cmp_ids_and_pin_states(self):
        source, quality, report = self.build()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual([z['coreComponentId'] for z in source['zones']], ['cmp-R1'])
        self.assertEqual(source['zones'][0]['componentIds'], ['cmp-R1', 'cmp-C1'])
        self.assertEqual(source['netPolicies']['N1'], 'direct')
        self.assertEqual(quality['modules'][0]['members'], ['R1', 'C1'])
        self.assertEqual(report['counts']['parts'], 2)

    def test_anchor_off_grid_is_normalised_and_blocked(self):
        source, quality, report = self.build(snap=snapshot(anchor_y=202))
        measured = source['components'][0]['measurement']
        self.assertEqual(measured['y'], 200)
        self.assertEqual([b['code'] for b in report['blockers']],
                         ['anchor_requires_review'])
        self.assertIn('anchor_off_grid', [w['code'] for w in report['warnings']])

    def test_missing_zone_member_is_reported(self):
        source, quality, report = self.build(zone_plan=plan(['R1']))
        self.assertEqual(report['status'], 'blocked')
        codes = [b['code'] for b in report['blockers']]
        self.assertIn('component_not_in_any_zone', codes)

    def test_pin_net_mismatch_is_reported(self):
        snap = snapshot()
        snap['components'][0]['pins'][0]['net'] = 'GND'
        source, quality, report = self.build(snap=snap)
        codes = [b['code'] for b in report['blockers']]
        self.assertIn('pin_net_mismatch', codes)

    def test_unused_explicit_policy_is_rejected(self):
        zone_plan = plan()
        zone_plan['zones'][0]['netPolicies']['+5V'] = 'local_power'
        source, quality, report = self.build(zone_plan=zone_plan)
        self.assertIn('unused_net_policy', [b['code'] for b in report['blockers']])


if __name__ == '__main__':
    unittest.main()
