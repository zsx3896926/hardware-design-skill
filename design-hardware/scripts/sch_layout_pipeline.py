#!/usr/bin/env python3
"""Run the offline part of the schematic layout pipeline and keep evidence.

Steps (each command is recorded in run-manifest.json):

  1. easyeda sch layout-plan --zones     local placement/wire candidate
  2. easyeda sch layout-sheet-plan       pack zones onto the measured sheet
  3. easyeda sch layout-render           vector preview, no EDA mutation
  4. sch_layout_quality.py               independent readability check

The script never writes to EDA and never reports an Apply. With --before and a
composition source it can additionally emit the compose command for a dry run,
but it refuses to execute it: compose --preserve-instances requires the explicit
"EasyEDA Agent Component ID" property on every part, and that evidence must be
checked first.

Usage:
  python sch_layout_pipeline.py --easyeda <path> --source out/layout-source.json \
      --model-report out/model-report.json --out-dir out/run
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run(command, manifest, label, allow_failure=False, stdout_path=None):
    result = subprocess.run(command, capture_output=True)
    if stdout_path:
        Path(stdout_path).write_bytes(result.stdout)
    record = {
        'step': label,
        'command': command,
        'exit': result.returncode,
        'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
        'stderr_sha256': hashlib.sha256(result.stderr).hexdigest(),
        'stderr': result.stderr.decode('utf-8', 'replace')[:2000],
    }
    manifest['steps'].append(record)
    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(f'{label} failed ({result.returncode}): '
                           f'{result.stderr.decode("utf-8", "replace")[:800]}')
    return result


def _load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return data.get('result', data) if isinstance(data, dict) else data


def verify_rigid_translation(page, before):
    """Check every planned placement is a rigid translation of the measurement.

    The solver may normalise a stored anchor that is off the 5-raw grid. If the
    anchor-to-pin offset then differs from the measured offset, a writer that
    moves parts by their anchor would land the pins one or more raw away from
    the planned wires. Detect it here instead of at apply time.
    """
    measured = {c['designator']: c for c in before.get('components') or []
                if c.get('componentType') == 'part'}
    problems = []
    checked = 0
    for zone in page.get('zones') or []:
        for placement in (zone.get('layout') or {}).get('placements') or []:
            ref = placement['designator']
            source = measured.get(ref)
            if source is None:
                problems.append({'designator': ref, 'reason': 'missing from before snapshot'})
                continue
            checked += 1
            source_pins = {str(p['pinNumber']): p for p in source.get('pins') or []}
            planned_pins = {str(p['number']): p for p in placement.get('pins') or []}
            if set(source_pins) != set(planned_pins):
                problems.append({'designator': ref, 'reason': 'pin set differs'})
                continue
            deltas = {}
            for number in source_pins:
                dx = planned_pins[number]['x'] - source_pins[number]['x']
                dy = planned_pins[number]['y'] - source_pins[number]['y']
                deltas[number] = (round(dx, 6), round(dy, 6))
            unique = set(deltas.values())
            if len(unique) != 1:
                problems.append({'designator': ref, 'reason': 'pins do not share one translation',
                                 'deltas': {k: list(v) for k, v in deltas.items()}})
                continue
            dx, dy = next(iter(unique))
            anchor_dx = placement['x'] - source.get('x', placement['x'])
            anchor_dy = placement['y'] - source.get('y', placement['y'])
            if abs(anchor_dx - dx) > 1e-6 or abs(anchor_dy - dy) > 1e-6:
                problems.append({'designator': ref,
                                 'reason': 'anchor translation differs from pin translation',
                                 'anchor_delta': [round(anchor_dx, 6), round(anchor_dy, 6)],
                                 'pin_delta': [dx, dy]})
    return {'status': 'ok' if not problems else 'geometry-inconsistent',
            'checked': checked, 'problems': problems}


def planned_quality(pages, model_report):
    """Translate the selected sheet page into sch_layout_quality input."""
    page = pages['pages'][0]
    sheet = model_report['page']
    components, wires, flags, ports = [], [], [], []
    modules = []
    for zone in page['zones']:
        frame = zone['frame']['rect']
        position = zone['sheetPosition']
        dx = position['x'] - frame['minX']
        dy = position['y'] - frame['maxY']
        layout = zone['layout']
        members = []
        for placement in layout['placements']:
            members.append(placement['designator'])
            bbox = placement['bbox']
            component = {
                'id': placement.get('primitiveId') or placement['designator'],
                'designator': placement['designator'],
                'bbox': [bbox['minX'] + dx, bbox['minY'] + dy,
                         bbox['maxX'] + dx, bbox['maxY'] + dy],
                'pins': [dict(pin, x=pin['x'] + dx, y=pin['y'] + dy)
                         for pin in placement.get('pins') or []],
                'text_bboxes': [[box['minX'] + dx, box['minY'] + dy,
                                 box['maxX'] + dx, box['maxY'] + dy]
                                for box in placement.get('textBboxes') or []],
                'module': zone['id'],
            }
            components.append(component)
        for wire in layout['wires']:
            wires.append({'net': wire['net'],
                          'points': [[point[0] + dx, point[1] + dy]
                                     for point in wire['points']]})
        for flag in layout['flags']:
            flags.append({'net': flag['net'], 'kind': flag.get('kind', 'unknown'),
                          'x': flag['pinX'] + dx, 'y': flag['pinY'] + dy})
        modules.append({'id': zone['id'], 'title': zone['title'],
                        'core': zone.get('coreComponentId'), 'members': members})
    title = sheet.get('titleBlock')
    return {
        'coordinate_units': 'schematic raw (0.01 inch), y-up',
        'page': {'bbox': list(sheet['bbox']),
                 'title_block': list(title) if title else None},
        'components': components, 'wires': wires, 'flags': flags, 'ports': ports,
        'modules': modules,
        'reading_order': [zone['id'] for zone in page['zones']],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--easyeda', required=True)
    parser.add_argument('--source', required=True, help='layout-source.json')
    parser.add_argument('--model-report', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--flow', default='z', choices=['z', 'compact'])
    parser.add_argument('--compose-source', help='composition JSON for an optional dry-run')
    parser.add_argument('--before', help='fresh sch list snapshot for compose dry-run')
    parser.add_argument('--page', help='selected page JSON for compose dry-run')
    args = parser.parse_args(argv)

    source = _load(args.source)
    report = _load(args.model_report)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {'status': 'running', 'inputs': {
        'source_sha256': _sha(args.source),
        'model_report_sha256': _sha(args.model_report),
        'easyeda': args.easyeda,
    }, 'steps': []}
    try:
        zones = out / 'zones-geom.json'
        _run([args.easyeda, 'sch', 'layout-plan', '--zones', '--from', args.source,
              '--out', str(zones), '--report', str(out / 'layout-report.json')],
             manifest, 'layout-plan')
        page = report['page']
        sheet = {
            'bounds': {'minX': page['bbox'][0], 'minY': page['bbox'][1],
                       'maxX': page['bbox'][2], 'maxY': page['bbox'][3]},
            'border': ({'minX': page['innerBBox'][0], 'minY': page['innerBBox'][1],
                        'maxX': page['innerBBox'][2], 'maxY': page['innerBBox'][3]}
                       if page.get('innerBBox') else
                       {'minX': page['bbox'][0], 'minY': page['bbox'][1],
                        'maxX': page['bbox'][2], 'maxY': page['bbox'][3]}),
            'keepouts': [{'minX': k['bbox']['minX'], 'minY': k['bbox']['minY'],
                          'maxX': k['bbox']['maxX'], 'maxY': k['bbox']['maxY']}
                         for k in page.get('keepouts') or []],
            'padding': source.get('spacing', 10),
            'gap': source.get('spacing', 10),
            'flow': args.flow,
        }
        render = {'schemaVersion': 1, 'spacing': source.get('spacing', 10),
                  'zones': _load(zones)['zones'], 'sheet': sheet}
        render_path = out / 'render-input.json'
        render_path.write_text(json.dumps(render, ensure_ascii=False, indent=1),
                               encoding='utf-8')
        pages_path = out / 'pages.json'
        _run([args.easyeda, 'sch', 'layout-sheet-plan', '--from', str(render_path),
              '--out', str(pages_path), '--flow', args.flow], manifest, 'layout-sheet-plan')
        pages = _load(pages_path)
        if len(pages.get('pages') or []) != 1:
            manifest['status'] = 'needs-pagination'
            manifest['pages'] = len(pages.get('pages') or [])
        else:
            manifest['status'] = 'planned'
            if args.before:
                consistency = verify_rigid_translation(pages['pages'][0], _load(args.before))
                manifest['placement_consistency'] = consistency['status']
                (out / 'placement-consistency.json').write_text(
                    json.dumps(consistency, ensure_ascii=False, indent=1), encoding='utf-8')
                if consistency['status'] != 'ok':
                    manifest['status'] = 'geometry-inconsistent'
            page_path = out / 'page.json'
            page_path.write_text(json.dumps(pages['pages'][0], ensure_ascii=False, indent=1),
                                 encoding='utf-8')
            _run([args.easyeda, 'sch', 'layout-render', '--from', str(page_path),
                  '--out', str(out / 'layout.svg')], manifest, 'layout-render')
            quality = planned_quality(pages, report)
            quality_path = out / 'planned-quality-input.json'
            quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=1),
                                    encoding='utf-8')
            _run([sys.executable, str(Path(__file__).with_name('sch_layout_quality.py')),
                  str(quality_path), '--json'], manifest, 'quality', allow_failure=True,
                 stdout_path=out / 'planned-quality.json')

        if args.compose_source and args.before and args.page:
            compose_cmd = [args.easyeda, 'sch', 'compose', '--from', args.compose_source,
                           '--layout-page', args.page, '--out', str(out / 'compose-plan.json'),
                           '--before', args.before, '--replace', '--preserve-instances',
                           '--playbook', str(out / 'apply.json'), '--dry-run']
            manifest['compose_dry_run_command'] = compose_cmd
            manifest['compose_note'] = ('not executed: stable component binding and '
                                        'instance-preservation evidence must be checked first')

        manifest['status'] = manifest.get('status', 'planned')
        (out / 'run-manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps({'status': manifest['status'], 'out_dir': str(out.resolve())},
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        manifest['status'] = 'failed'
        manifest['error'] = str(error)
        (out / 'run-manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
