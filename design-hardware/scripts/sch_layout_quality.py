#!/usr/bin/env python3
"""Independent readability checks for a measured schematic page.

The EasyEDA agent's layout-lint proves overlap/pin-coincidence and layout-score
scores labels/proximity; neither catches a page whose modules are all crowded
into one corner while half the sheet is empty. This checker works on a
normalised, measured snapshot (no EDA access) and reports named findings
instead of one "beauty" score.

Input (JSON):
  coordinate_units  string
  page              {bbox:[l,b,r,t], title_block?:[l,b,r,t]}
  components        [{id, designator, bbox:[l,b,r,t], pins?:[...],
                      text_bboxes?:[[l,b,r,t], ...]}]
  wires?            [{net, points:[[x,y], ...]}]
  flags?            [{net, kind, x, y}]
  ports?            [{net, x, y}]
  modules?          [{id, title, core, members:[designator, ...]}]
  reading_order?    [module id, ...]

Exit: 0 no findings, 1 findings, 2 invalid input.
"""
import argparse
import json
import math
import sys

from power_budget import fields, read_json


def _finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('finite number required')
    return float(value)


def _bbox(value, name='bbox'):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f'{name} must be [left,bottom,right,top]')
    box = tuple(_finite(v) for v in value)
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError(f'{name} must have positive extent')
    return box


def _point(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('point must be [x,y]')
    return (_finite(value[0]), _finite(value[1]))


def _union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _area(box):
    return 0.0 if box is None else max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _overlap_area(a, b):
    if not _overlap(a, b):
        return 0.0
    return (min(a[2], b[2]) - max(a[0], b[0])) * (min(a[3], b[3]) - max(a[1], b[1]))


def _contains(outer, inner):
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def _distance(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def _center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _segment_enters(a, b, box):
    """Open-box clipping: touching a border is not an interior crossing."""
    lo, hi = 0.0, 1.0
    for axis in (0, 1):
        delta = b[axis] - a[axis]
        if delta == 0:
            if not box[axis] < a[axis] < box[axis + 2]:
                return False
        else:
            t0, t1 = sorted(((box[axis] - a[axis]) / delta,
                             (box[axis + 2] - a[axis]) / delta))
            lo, hi = max(lo, t0), min(hi, t1)
    return lo < hi and hi > 0 and lo < 1


def _segments_cross(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    x, y = cross(a, b, c), cross(a, b, d)
    z, w = cross(c, d, a), cross(c, d, b)
    return ((x < 0 < y) or (y < 0 < x)) and ((z < 0 < w) or (w < 0 < z))


def _manhattan(points):
    return math.fsum(abs(b[0] - a[0]) + abs(b[1] - a[1])
                     for a, b in zip(points, points[1:]))


def load(data):
    fields(data, ['coordinate_units', 'page', 'components'],
           ['wires', 'flags', 'ports', 'modules', 'reading_order'])
    if not isinstance(data['coordinate_units'], str) or not data['coordinate_units'].strip():
        raise ValueError('coordinate_units must name the measured unit')
    page = data['page']
    fields(page, ['bbox'], ['title_block'])
    page_box = _bbox(page['bbox'], 'page.bbox')
    title = _bbox(page['title_block'], 'page.title_block') if 'title_block' in page else None
    if title is not None and not _overlap(page_box, title):
        raise ValueError('page.title_block does not intersect page.bbox')

    components = []
    seen = set()
    for index, item in enumerate(data['components']):
        fields(item, ['id', 'designator', 'bbox'],
               ['pins', 'text_bboxes', 'module'])
        identity = item['id']
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise ValueError(f'components[{index}].id must be unique and nonempty')
        seen.add(identity)
        designator = item['designator']
        if not isinstance(designator, str) or not designator.strip():
            raise ValueError(f'components[{index}].designator must be nonempty')
        text_boxes = [_bbox(box, f'components[{index}].text_bboxes')
                      for box in item.get('text_bboxes') or []]
        pins = []
        for pin in item.get('pins') or []:
            fields(pin, ['number', 'x', 'y'], ['net', 'name', 'rotation'])
            pins.append({'number': str(pin['number']), 'x': _finite(pin['x']),
                         'y': _finite(pin['y']), 'net': pin.get('net') or '',
                         'name': pin.get('name') or ''})
        components.append({'id': identity, 'designator': designator,
                           'bbox': _bbox(item['bbox'], f'components[{index}].bbox'),
                           'text_bboxes': text_boxes, 'pins': pins,
                           'module': item.get('module')})

    wires = []
    for index, item in enumerate(data.get('wires') or []):
        fields(item, ['points'], ['net'])
        points = [_point(p) for p in item['points']]
        if len(points) < 2:
            raise ValueError(f'wires[{index}] needs at least two points')
        wires.append({'net': item.get('net') or '', 'points': points})

    flags = []
    for index, item in enumerate(data.get('flags') or []):
        fields(item, ['net', 'x', 'y'], ['kind'])
        flags.append({'net': item['net'], 'x': _finite(item['x']), 'y': _finite(item['y']),
                      'kind': item.get('kind') or 'unknown'})

    ports = []
    for index, item in enumerate(data.get('ports') or []):
        fields(item, ['net', 'x', 'y'])
        ports.append({'net': item['net'], 'x': _finite(item['x']), 'y': _finite(item['y'])})

    modules = []
    module_ids = set()
    for index, item in enumerate(data.get('modules') or []):
        fields(item, ['id', 'members'], ['title', 'core'])
        if not isinstance(item['id'], str) or not item['id'].strip() or item['id'] in module_ids:
            raise ValueError(f'modules[{index}].id must be unique and nonempty')
        module_ids.add(item['id'])
        members = item['members']
        if not isinstance(members, list) or not all(isinstance(x, str) and x for x in members):
            raise ValueError(f'modules[{index}].members must be designators')
        if len(set(members)) != len(members):
            raise ValueError(f'modules[{index}].members has duplicates')
        modules.append({'id': item['id'], 'title': item.get('title') or item['id'],
                        'core': item.get('core'), 'members': members})

    order = data.get('reading_order') or [m['id'] for m in modules]
    if not isinstance(order, list) or set(order) != {m['id'] for m in modules}:
        raise ValueError('reading_order must list every module exactly once')

    return {'units': data['coordinate_units'], 'page': page_box, 'title': title,
            'components': components, 'wires': wires, 'flags': flags, 'ports': ports,
            'modules': modules, 'reading_order': order,
            'has_wires': bool(data.get('wires')),
            'has_text': any(c['text_bboxes'] for c in components)}


def analyze(data):
    model = load(data)
    page, title = model['page'], model['title']
    components = model['components']
    findings = []

    def add(code, severity, message, **objects):
        findings.append({'code': code, 'severity': severity, 'message': message, **objects})

    by_designator = {}
    for component in components:
        if component['designator'] in by_designator:
            add('duplicate_designator', 'error', 'duplicate designator in snapshot',
                designators=[component['designator']])
        by_designator[component['designator']] = component
        if not _contains(page, component['bbox']):
            add('component_outside_page', 'error', 'component bbox leaves the sheet',
                designator=component['designator'], bbox=list(component['bbox']))
        if title is not None and _overlap(component['bbox'], title):
            add('component_over_title_block', 'error', 'component overlaps the title block',
                designator=component['designator'], bbox=list(component['bbox']))
        if not component['text_bboxes']:
            add('missing_text_bbox', 'warn', 'designator text bbox not measured',
                designator=component['designator'])
        for box in component['text_bboxes']:
            if not _contains(page, box):
                add('text_outside_page', 'warn', 'designator text leaves the sheet',
                    designator=component['designator'], bbox=list(box))

    # Symbol overlaps and designator text collisions.
    for index, first in enumerate(components):
        for second in components[index + 1:]:
            if _overlap(first['bbox'], second['bbox']):
                add('symbol_overlap', 'error', 'symbol bounding boxes overlap',
                    designators=[first['designator'], second['designator']])
        for box in first['text_bboxes']:
            for other in components:
                if other['id'] == first['id']:
                    continue
                if _overlap(box, other['bbox']):
                    add('text_over_symbol', 'warn', 'designator text overlaps another symbol',
                        designator=first['designator'], other=other['designator'])

    # Wire geometry and text crossings.
    total_length = 0.0
    segments = []
    for wire in model['wires']:
        total_length += _manhattan(wire['points'])
        for a, b in zip(wire['points'], wire['points'][1:]):
            segments.append((wire['net'], a, b))
            for component in components:
                for box in component['text_bboxes']:
                    if _segment_enters(a, b, box):
                        add('wire_over_text', 'warn', 'wire crosses a designator text box',
                            net=wire['net'], designator=component['designator'])
    crossings = 0
    for index, (net_a, a, b) in enumerate(segments):
        for net_b, c, d in segments[index + 1:]:
            if net_a and net_b and net_a != net_b and _segments_cross(a, b, c, d):
                crossings += 1
    if crossings:
        add('wire_crossings', 'info', 'different-net wire segments cross',
            count=crossings)

    # Page balance: occupancy by a coarse grid avoids one large symbol hiding
    # the fact that a whole half of the sheet is empty.
    usable_w = page[2] - page[0]
    usable_h = page[3] - page[1]
    cols, rows = 12, 8
    occupied = [[0.0 for _ in range(cols)] for _ in range(rows)]
    for component in components:
        box = component['bbox']
        c0 = max(0, min(cols - 1, int((box[0] - page[0]) / usable_w * cols)))
        c1 = max(0, min(cols - 1, int((box[2] - page[0]) / usable_w * cols)))
        r0 = max(0, min(rows - 1, int((box[1] - page[1]) / usable_h * rows)))
        r1 = max(0, min(rows - 1, int((box[3] - page[1]) / usable_h * rows)))
        for row in range(r0, r1 + 1):
            for col in range(c0, c1 + 1):
                occupied[row][col] += _area(box) / ((c1 - c0 + 1) * (r1 - r0 + 1))
    empty_cells = sum(1 for row in occupied for value in row if value == 0.0)
    empty_ratio = empty_cells / (cols * rows)

    # Mass balance: a bbox can hug both margins while the middle of the sheet
    # is empty (the original failure). Count and area on each side of the
    # sheet centre make that visible.
    page_cx = (page[0] + page[2]) / 2
    page_cy = (page[1] + page[3]) / 2
    total_component_area = sum(_area(c['bbox']) for c in components) or 1.0
    left_count = sum(1 for c in components if _center(c['bbox'])[0] < page_cx)
    top_count = sum(1 for c in components if _center(c['bbox'])[1] >= page_cy)
    left_area = sum(_area(c['bbox']) for c in components if _center(c['bbox'])[0] < page_cx)
    top_area = sum(_area(c['bbox']) for c in components if _center(c['bbox'])[1] >= page_cy)
    centroid_x = sum(_center(c['bbox'])[0] * _area(c['bbox']) for c in components) / total_component_area
    centroid_y = sum(_center(c['bbox'])[1] * _area(c['bbox']) for c in components) / total_component_area
    side_balance = {
        'left': left_count, 'right': len(components) - left_count,
        'top': top_count, 'bottom': len(components) - top_count,
        'left_area_ratio': round(left_area / total_component_area, 3),
        'top_area_ratio': round(top_area / total_component_area, 3),
        'centroid': [round(centroid_x, 1), round(centroid_y, 1)],
    }
    if components:
        if min(left_count, len(components) - left_count) / len(components) <= 0.2:
            add('mass_asymmetry', 'warn',
                'at least four fifths of the parts sit on one side of the sheet',
                axis='x', left=left_count, right=len(components) - left_count)
        if min(top_count, len(components) - top_count) / len(components) <= 0.2:
            add('mass_asymmetry', 'warn',
                'at least four fifths of the parts sit on one vertical half',
                axis='y', top=top_count, bottom=len(components) - top_count)
        dx = abs(centroid_x - page_cx) / max(1.0, usable_w)
        dy = abs(centroid_y - page_cy) / max(1.0, usable_h)
        if dx > 0.12 or dy > 0.12:
            add('centroid_offset', 'warn',
                'area-weighted component centroid is far from the sheet centre',
                offset=[round(centroid_x - page_cx, 1), round(centroid_y - page_cy, 1)])

    content_box = _union([c['bbox'] for c in components]
                         + [box for c in components for box in c['text_bboxes']])
    if content_box is not None:
        left = content_box[0] - page[0]
        right = page[2] - content_box[2]
        bottom = content_box[1] - page[1]
        top = page[3] - content_box[3]
        horizontal = max(left, right) / max(1.0, usable_w)
        vertical = max(top, bottom) / max(1.0, usable_h)
        if horizontal > 0.22 and min(left, right) / max(1.0, usable_w) < 0.08:
            add('page_imbalance', 'warn',
                'content is pressed to one side while the opposite margin is empty',
                left_margin=round(left, 1), right_margin=round(right, 1))
        if vertical > 0.22 and min(top, bottom) / max(1.0, usable_h) < 0.08:
            add('page_imbalance', 'warn',
                'content is pressed to one vertical edge',
                top_margin=round(top, 1), bottom_margin=round(bottom, 1))
        if empty_ratio > 0.55:
            add('sparse_page', 'warn', 'more than half of the coarse sheet cells are empty',
                empty_ratio=round(empty_ratio, 3))
        if empty_ratio < 0.12:
            add('crowded_page', 'warn', 'almost every coarse sheet cell holds content',
                empty_ratio=round(empty_ratio, 3))
        fill = _area(content_box) / max(1.0, _area(page) - _area(title))
        if fill < 0.12:
            add('low_page_utilization', 'warn', 'content envelope uses little of the usable sheet',
                fill_ratio=round(fill, 3))
    else:
        add('empty_page', 'error', 'no component geometry found')

    # Module geometry: the unit the reader actually follows.
    module_boxes = {}
    module_findings = []
    if model['modules']:
        covered = set()
        for module in model['modules']:
            for member in module['members']:
                if member not in by_designator:
                    add('module_member_missing', 'error',
                        'module references a designator absent from the page',
                        module=module['id'], designator=member)
                elif member in covered:
                    add('module_member_duplicate', 'error',
                        'designator belongs to more than one module',
                        module=module['id'], designator=member)
                covered.add(member)
            boxes = [by_designator[m]['bbox'] for m in module['members']
                     if m in by_designator]
            if not boxes:
                continue
            box = _union(boxes)
            module_boxes[module['id']] = box
            if not _contains(page, box):
                add('module_outside_page', 'error', 'module envelope leaves the sheet',
                    module=module['id'], bbox=list(box))
            if title is not None and _overlap(box, title):
                add('module_over_title_block', 'error',
                    'module envelope overlaps the title block',
                    module=module['id'], bbox=list(box))
            elif title is not None and _distance(box, title) < 20:
                add('module_near_title_block', 'warn',
                    'module envelope is within 20 raw of the title block',
                    module=module['id'], distance=round(_distance(box, title), 1))
            core = module.get('core')
            if core and core in by_designator:
                members = [by_designator[m]['bbox'] for m in module['members']
                           if m in by_designator]
                distances = [_distance(by_designator[core]['bbox'], box) for box in members
                             if box is not by_designator[core]['bbox']]
                if distances:
                    spread = round(max(distances), 1)
                    module_findings.append({'module': module['id'],
                                            'max_member_distance': spread})
                    if spread > 200:
                        add('module_fragmented', 'warn',
                            'a module member sits more than 200 raw from its core',
                            module=module['id'], max_distance=spread)
        missing = sorted(set(by_designator) - covered)
        if missing:
            add('component_not_in_module', 'warn',
                'components are not assigned to any module', designators=missing)

        ordered = [m for m in model['modules'] if m['id'] in module_boxes]
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                a, b = module_boxes[first['id']], module_boxes[second['id']]
                if _overlap(a, b):
                    add('module_overlap', 'error', 'module envelopes overlap',
                        modules=[first['id'], second['id']])
                elif _distance(a, b) < 10:
                    add('module_too_close', 'warn',
                        'module envelopes are closer than 10 raw',
                        modules=[first['id'], second['id']],
                        distance=round(_distance(a, b), 1))

        # Reading order: compare declared order with a Z-order sort of module
        # centers, row by row (higher y first, then left to right).
        def z_key(module_id):
            center = _center(module_boxes[module_id])
            row = int((page[3] - center[1]) / max(1.0, usable_h) * 3)
            return (row, center[0])
        actual = sorted(module_boxes, key=z_key)
        declared = [m for m in model['reading_order'] if m in module_boxes]
        if actual != declared:
            add('reading_order_mismatch', 'info',
                'declared module order differs from the page Z-reading order',
                declared=declared, observed=actual)

    # Net fragmentation: labels are not evil, but a page where most connections
    # are labels is hard to follow. Count markers and nets that mix wires/labels.
    flag_nets = {}
    for flag in model['flags']:
        flag_nets[flag['net']] = flag_nets.get(flag['net'], 0) + 1
    port_nets = {}
    for port in model['ports']:
        port_nets[port['net']] = port_nets.get(port['net'], 0) + 1
    wire_nets = {}
    for wire in model['wires']:
        if wire['net']:
            wire_nets[wire['net']] = wire_nets.get(wire['net'], 0) + 1
    label_only = sorted(net for net in port_nets if net not in wire_nets)
    if label_only:
        add('label_only_nets', 'info', 'nets connected only through ports/labels',
            nets=label_only)
    repeated = sorted((net for net, count in flag_nets.items() if count > 2))
    if repeated:
        add('repeated_power_markers', 'info',
            'a net uses more than two same-name power markers',
            nets=repeated)

    summary = {
        'components': len(components),
        'modules': len(model['modules']),
        'wires': len(model['wires']),
        'wire_length': round(total_length, 1),
        'different_net_crossings': crossings,
        'empty_cell_ratio': round(empty_ratio, 3),
        'side_balance': side_balance,
        'content_bbox': list(content_box) if content_box else None,
        'module_bboxes': {k: list(v) for k, v in module_boxes.items()},
        'module_findings': module_findings,
    }
    severities = [f['severity'] for f in findings]
    return {
        'status': 'review_required' if findings else 'clean',
        'summary': summary,
        'findings': findings,
        'counts': {s: severities.count(s) for s in ('error', 'warn', 'info')},
        'coverage': {
            'text_bboxes': model['has_text'],
            'wire_geometry': model['has_wires'],
            'modules': bool(model['modules']),
            'page_and_title_block': True,
        },
        'not_checked': [
            'electrical connectivity and pin-net equivalence',
            'symbol outline accuracy beyond the measured bbox',
            'fonts and text width beyond the measured designator bbox',
            'parameters/values text (not measured by designator-geometry)',
            'PCB placement, routing and manufacturing rules',
        ],
    }


def compare(before, after):
    """Delta report for two analyze() results; never a synthetic pass."""
    keys = ('empty_cell_ratio', 'wire_length', 'different_net_crossings', 'components')
    deltas = {}
    for key in keys:
        deltas[key] = round(after['summary'].get(key, 0) - before['summary'].get(key, 0), 3)
    codes_before = {f['code'] for f in before['findings']}
    codes_after = {f['code'] for f in after['findings']}
    return {
        'deltas': deltas,
        'resolved': sorted(codes_before - codes_after),
        'introduced': sorted(codes_after - codes_before),
        'remaining_errors': [f for f in after['findings'] if f['severity'] == 'error'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('layout', help='measured page snapshot JSON')
    parser.add_argument('--compare', help='earlier snapshot JSON for a delta report')
    parser.add_argument('--json', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        after = analyze(read_json(args.layout))
        result = after
        if args.compare:
            before = analyze(read_json(args.compare))
            result = {'before': before, 'after': after,
                      'comparison': compare(before, after)}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if after['findings'] else 0
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as error:
        print(json.dumps({'status': 'invalid_input', 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
