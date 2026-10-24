#!/usr/bin/env python3
"""Build a validated layout-plan input from real EasyEDA measurements.

This is the bridge between an EasyEDA page and the agent's offline layout
planner. It does not place anything and does not call EDA. It reads the
measured objects already captured by:

  easyeda sch list --include-device-identity --include-bbox --include-pins \
      --include-wires --include-page-primitives
  easyeda sch connectivity --page <uuid>
  easyeda sch designator-geometry --out designators.json
  easyeda sch sheet-geometry --from-svg <page.svg> --json

and a functional module plan (roles, core, members, net policies). It refuses
to emit a planner input when a component is unassigned, a pin/net disagrees with
the canonical connectivity IR, or a measured field is missing. Anchor poses
that are not on the 5-raw grid are normalised only with an explicit warning and
an apply-time blocker; they are never silently accepted.

Usage:
  python sch_layout_model.py --snapshot page.json --connectivity conn.json \
      --designators designators.json --sheet sheet.json --module-plan plan.json \
      --out-dir out

Exit: 0 ready, 1 blockers, 2 invalid input.
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

from power_budget import fields


GRID = 5.0


def _load(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'{path}: {error}')
    return data.get('result', data) if isinstance(data, dict) else data


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    return float(value)


def _bbox(value, name):
    if isinstance(value, dict):
        value = [value.get('minX'), value.get('minY'), value.get('maxX'), value.get('maxY')]
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f'{name} must be [left,bottom,right,top]')
    box = [_finite(v, name) for v in value]
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError(f'{name} must have positive extent')
    return box


def _on_grid(value):
    return abs(value / GRID - round(value / GRID)) < 1e-9


def _designator_parts(snapshot):
    result = {}
    for item in snapshot.get('components') or []:
        if item.get('componentType') != 'part':
            continue
        ref = item.get('designator')
        if not ref:
            raise ValueError('part without a designator')
        if ref in result:
            raise ValueError(f'duplicate designator {ref}')
        result[ref] = item
    return result


def _net_names(connectivity):
    return {net['id']: net.get('name') or '' for net in connectivity.get('nets') or []}


def _pin_net_map(connectivity):
    names = _net_names(connectivity)
    result = {}
    for row in connectivity.get('connections') or []:
        if row.get('kind') not in (None, 'netlist'):
            continue
        result[(row['componentId'], str(row['pinNumber']))] = names.get(row.get('netId'), '')
    return result


def _wire_points(wire):
    """Accept the measured flat rawLine or an explicit points array."""
    if isinstance(wire.get('points'), list):
        return [[_finite(p[0], 'wire x'), _finite(p[1], 'wire y')]
                for p in wire['points']]
    line = wire.get('rawLine') or wire.get('line') or []
    if len(line) < 4:
        return []
    points = [[_finite(line[i], 'wire x'), _finite(line[i + 1], 'wire y')]
              for i in range(0, len(line) - 1, 2)]
    compact = [points[0]]
    for point in points[1:]:
        if point != compact[-1]:
            compact.append(point)
    return compact


def _normalise_anchor(component, pins):
    """Return (anchor, warnings) if pin offsets are off the 5-raw grid.

    The planner requires every measured pin offset from the part anchor to sit
    on its 5-raw grid. A symbol whose stored anchor is off-grid cannot be
    planned at all. We move only the anchor reference to the nearest grid point
    that makes every offset integral; the measured pins, bbox and text boxes
    stay untouched. The original pose is recorded so a writer can prove that
    nothing but the reference moved.
    """
    anchor = (float(component['x']), float(component['y']))
    warnings = []
    offsets_ok = all(_on_grid(pin['x'] - anchor[0]) and _on_grid(pin['y'] - anchor[1])
                     for pin in pins)
    if offsets_ok:
        return anchor, warnings

    candidates = []
    for dx in (-2, -1, 0, 1, 2):
        for dy in (-2, -1, 0, 1, 2):
            candidate = (anchor[0] + dx, anchor[1] + dy)
            if all(_on_grid(pin['x'] - candidate[0]) and _on_grid(pin['y'] - candidate[1])
                   for pin in pins):
                candidates.append((abs(dx) + abs(dy), candidate))
    if not candidates:
        pin_x = sorted({pin['x'] for pin in pins})
        pin_y = sorted({pin['y'] for pin in pins})
        candidate = (pin_x[len(pin_x) // 2], pin_y[len(pin_y) // 2])
        if not all(_on_grid(pin['x'] - candidate[0]) and _on_grid(pin['y'] - candidate[1])
                   for pin in pins):
            return anchor, warnings
        candidates.append((99, candidate))
    _, chosen = min(candidates)
    warnings.append({
        'code': 'anchor_off_grid',
        'designator': component['designator'],
        'measured_anchor': list(anchor),
        'normalised_anchor': list(chosen),
        'reason': 'pin offsets from the stored anchor are not on the 5-raw grid',
        'apply_blocker': True,
    })
    return chosen, warnings


def build(snapshot_path, connectivity_path, designators_path, sheet_path,
          module_plan_path, spacing=10, max_candidates=30000):
    snapshot = _load(snapshot_path)
    connectivity = _load(connectivity_path)
    designators = _load(designators_path)
    sheet = _load(sheet_path)
    plan = _load(module_plan_path)

    for label, data, key in (
            ('snapshot', snapshot, 'components'),
            ('connectivity', connectivity, 'components'),
            ('designators', designators, 'designators'),
            ('module plan', plan, 'zones')):
        if not isinstance(data, dict) or key not in data:
            raise ValueError(f'{label} is missing {key}')

    parts = _designator_parts(snapshot)
    if not parts:
        raise ValueError('snapshot has no parts')
    pin_nets = _pin_net_map(connectivity)
    conn_by_ref = {item.get('ref'): item for item in connectivity.get('components') or []}
    if set(conn_by_ref) != set(parts):
        raise ValueError('snapshot and connectivity designator sets differ: '
                         f'missing={sorted(set(parts) - set(conn_by_ref))} '
                         f'extra={sorted(set(conn_by_ref) - set(parts))}')

    text_by_parent = {}
    for item in designators.get('designators') or []:
        parent = item.get('parentId')
        box = item.get('bbox')
        if parent and box:
            text_by_parent[parent] = _bbox(box, f'designator {item.get("value")}')
    if designators.get('count') is not None and designators['count'] != len(text_by_parent):
        raise ValueError('designator-geometry count does not match usable bboxes')

    sheet_result = sheet
    if 'sheet' in sheet_result:
        sheet_bbox = _bbox(sheet_result['sheet'].get('bbox'), 'sheet.bbox')
        inner = sheet_result['sheet'].get('innerBBox')
        title = (sheet_result.get('titleBlock') or {}).get('bbox')
        keepouts = sheet_result.get('keepouts') or []
    else:
        sheet_bbox = _bbox(sheet_result.get('bbox'), 'sheet bbox')
        inner = sheet_result.get('innerBBox')
        title = (sheet_result.get('titleBlock') or {}).get('bbox')
        keepouts = sheet_result.get('keepouts') or []
    inner_bbox = _bbox(inner, 'sheet.innerBBox') if inner else None
    title_bbox = _bbox(title, 'titleBlock.bbox') if title else None

    zones = plan['zones']
    if not isinstance(zones, list) or not zones:
        raise ValueError('module plan needs at least one zone')
    seen_members = {}
    zone_ids = set()
    blockers, warnings = [], []
    for index, zone in enumerate(zones):
        fields(zone, ['id', 'title', 'core', 'members'], ['netPolicies'])
        zone_id = zone['id']
        if not isinstance(zone_id, str) or not zone_id.strip() or zone_id in zone_ids:
            raise ValueError(f'zones[{index}].id must be unique and nonempty')
        zone_ids.add(zone_id)
        members = zone['members']
        if not isinstance(members, list) or not members or len(set(members)) != len(members):
            raise ValueError(f'zones[{index}].members must be a nonempty unique list')
        if zone['core'] not in members:
            blockers.append({'code': 'core_not_member', 'zone': zone_id,
                             'core': zone['core']})
        for member in members:
            if member not in parts:
                blockers.append({'code': 'zone_member_missing', 'zone': zone_id,
                                 'designator': member})
                continue
            if member in seen_members:
                blockers.append({'code': 'member_in_two_zones', 'designator': member,
                                 'zones': [seen_members[member], zone_id]})
            seen_members[member] = zone_id
    missing = sorted(set(parts) - set(seen_members))
    if missing:
        blockers.append({'code': 'component_not_in_any_zone', 'designators': missing})
    extra = sorted(set(seen_members) - set(parts))
    if extra:
        blockers.append({'code': 'zone_member_unknown', 'designators': extra})

    default_policies = plan.get('defaultNetPolicies') or {'+5V': 'local_power',
                                                          'GND': 'local_ground'}
    declared = {}
    explicitly_declared = set()
    for zone in zones:
        for name, policy in (zone.get('netPolicies') or {}).items():
            declared[name] = policy
            explicitly_declared.add(name)
    defaulted = set()
    for name, policy in default_policies.items():
        if name not in declared:
            declared[name] = policy
            defaulted.add(name)
    allowed = {'direct', 'module_port', 'local_power', 'local_ground'}
    for name, policy in declared.items():
        if policy not in allowed:
            raise ValueError(f'net policy for {name!r} must be one of {sorted(allowed)}')

    components = []
    used_nets = set()
    for ref, part in parts.items():
        pins = []
        for pin in part.get('pins') or []:
            number = str(pin.get('pinNumber'))
            net = pin.get('net') or ''
            x = pin.get('x')
            y = pin.get('y')
            if x is None or y is None:
                blockers.append({'code': 'pin_without_geometry', 'designator': ref,
                                 'pin': number})
                continue
            if not _on_grid(_finite(x, 'pin x')) or not _on_grid(_finite(y, 'pin y')):
                blockers.append({'code': 'pin_off_grid', 'designator': ref,
                                 'pin': number, 'x': x, 'y': y})
            canonical = pin_nets.get((conn_by_ref[ref]['id'], number))
            no_connect = bool(pin.get('noConnected'))
            # The canonical netlist only lists connected pins. An NC or an
            # explicitly unconnected pin is absent by design, not a mismatch.
            if canonical is None and net:
                blockers.append({'code': 'pin_missing_from_ir', 'designator': ref,
                                 'pin': number})
            elif canonical is not None and canonical != net:
                blockers.append({'code': 'pin_net_mismatch', 'designator': ref,
                                 'pin': number, 'snapshot': net, 'ir': canonical})
            if no_connect and net:
                blockers.append({'code': 'nc_pin_has_net', 'designator': ref,
                                 'pin': number, 'net': net})
            if net:
                used_nets.add(net)
            pins.append({'number': number, 'name': pin.get('pinName') or '',
                         'net': net, 'x': float(x), 'y': float(y),
                         'rotation': float(pin.get('rotation') or 0)})
        if not pins:
            blockers.append({'code': 'part_without_pins', 'designator': ref})
        bbox = _bbox(part.get('bbox'), f'{ref}.bbox')
        if 'x' not in part or 'y' not in part:
            raise ValueError(f'{ref} has no stored anchor')
        anchor, anchor_warnings = _normalise_anchor(part, pins)
        warnings.extend(anchor_warnings)
        if anchor_warnings:
            blockers.append({'code': 'anchor_requires_review', 'designator': ref,
                             'detail': 'planner anchor normalised; verify apply translation'})
        measurement = {
            'designator': ref,
            'primitiveId': part.get('primitiveId') or '',
            'x': anchor[0], 'y': anchor[1],
            'rotation': float(part.get('rotation') or 0),
            'mirror': bool(part.get('mirror')),
            'bbox': {'minX': bbox[0], 'minY': bbox[1], 'maxX': bbox[2], 'maxY': bbox[3]},
            'pins': pins,
        }
        text = text_by_parent.get(part.get('primitiveId'))
        if text:
            measurement['textBboxes'] = [{'minX': text[0], 'minY': text[1],
                                          'maxX': text[2], 'maxY': text[3]}]
        else:
            warnings.append({'code': 'missing_designator_bbox', 'designator': ref})
        pin_states = {}
        for pin in part.get('pins') or []:
            if pin.get('net'):
                continue
            pin_states[str(pin.get('pinNumber'))] = 'nc' if pin.get('noConnected') else 'unconnected'
        item = {'id': conn_by_ref[ref]['id'], 'measurement': measurement,
                'allowedRotations': [0, 90, 180, 270]}
        if pin_states:
            item['pinStates'] = pin_states
        components.append(item)

    # Infer a policy for signal nets the plan did not name. A net confined to
    # one zone can be drawn directly; a net crossing zones must use a port.
    net_zones = {}
    for zone in zones:
        for member in zone['members']:
            part = parts.get(member)
            for pin in (part or {}).get('pins') or []:
                if pin.get('net'):
                    net_zones.setdefault(pin['net'], set()).add(zone['id'])
    for net, owners in net_zones.items():
        if net in declared:
            continue
        declared[net] = 'direct' if len(owners) == 1 else 'module_port'
        warnings.append({'code': 'net_policy_inferred', 'net': net,
                         'policy': declared[net], 'zones': sorted(owners)})
    for net in list(declared):
        if net in used_nets:
            continue
        if net in defaulted and net not in explicitly_declared:
            del declared[net]
            warnings.append({'code': 'unused_default_policy_dropped', 'net': net})
        else:
            blockers.append({'code': 'unused_net_policy', 'net': net,
                             'policy': declared[net]})

    planner_zones = []
    for zone in zones:
        planner_zones.append({
            'id': zone['id'], 'title': zone['title'],
            'coreComponentId': conn_by_ref[zone['core']]['id'],
            'componentIds': [conn_by_ref[m]['id'] for m in zone['members']],
        })

    source = {
        'schemaVersion': 1,
        'components': components,
        'netPolicies': declared,
        'zones': planner_zones,
        'spacing': int(spacing),
        'maxCandidates': int(max_candidates),
    }

    # Quality input uses the same measured geometry but page-level rectangles.
    quality_components = []
    for ref, part in parts.items():
        bbox = _bbox(part.get('bbox'), f'{ref}.bbox')
        text = text_by_parent.get(part.get('primitiveId'))
        pins = [{'number': str(p.get('pinNumber')), 'name': p.get('pinName') or '',
                 'net': p.get('net') or '', 'x': p.get('x'), 'y': p.get('y')}
                for p in part.get('pins') or [] if p.get('x') is not None]
        quality_components.append({
            'id': part.get('primitiveId') or ref, 'designator': ref,
            'bbox': bbox, 'pins': pins,
            'text_bboxes': [text] if text else [],
            'module': seen_members.get(ref),
        })
    wires = []
    for wire in snapshot.get('wires') or []:
        points = _wire_points(wire)
        if len(points) >= 2:
            wires.append({'net': wire.get('net') or '', 'points': points})
    flags, ports = [], []
    for item in snapshot.get('components') or []:
        if item.get('componentType') == 'netflag':
            flags.append({'net': item.get('net') or '', 'kind': item.get('flagType') or 'unknown',
                          'x': item.get('x'), 'y': item.get('y')})
        elif item.get('componentType') == 'netport':
            ports.append({'net': item.get('net') or '', 'x': item.get('x'), 'y': item.get('y')})
    quality = {
        'coordinate_units': 'schematic raw (0.01 inch), y-up',
        'page': {'bbox': sheet_bbox, 'title_block': title_bbox},
        'components': quality_components,
        'wires': wires, 'flags': flags, 'ports': ports,
        'modules': [{'id': z['id'], 'title': z['title'], 'core': z['core'],
                     'members': z['members']} for z in zones],
        'reading_order': plan.get('readingOrder') or [z['id'] for z in zones],
    }

    report = {
        'status': 'blocked' if blockers else 'ready',
        'counts': {'parts': len(parts), 'zones': len(zones), 'nets': len(used_nets),
                   'components_with_text_bbox': len(text_by_parent)},
        'blockers': blockers,
        'warnings': warnings,
        'page': {'bbox': sheet_bbox, 'innerBBox': inner_bbox,
                 'titleBlock': title_bbox, 'keepouts': keepouts},
        'provenance': {
            'snapshot_sha256': _sha(snapshot_path),
            'connectivity_sha256': _sha(connectivity_path),
            'designators_sha256': _sha(designators_path),
            'sheet_sha256': _sha(sheet_path),
            'module_plan_sha256': _sha(module_plan_path),
        },
        'source_hashes': {
            'snapshot': _sha(snapshot_path),
            'connectivity': _sha(connectivity_path),
        },
    }
    return source, quality, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--connectivity', required=True)
    parser.add_argument('--designators', required=True)
    parser.add_argument('--sheet', required=True)
    parser.add_argument('--module-plan', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--spacing', type=int, default=10)
    parser.add_argument('--max-candidates', type=int, default=30000)
    args = parser.parse_args(argv)
    try:
        source, quality, report = build(
            args.snapshot, args.connectivity, args.designators, args.sheet,
            args.module_plan, spacing=args.spacing, max_candidates=args.max_candidates)
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'layout-source.json').write_text(
            json.dumps(source, ensure_ascii=False, indent=1), encoding='utf-8')
        (out / 'quality-input.json').write_text(
            json.dumps(quality, ensure_ascii=False, indent=1), encoding='utf-8')
        (out / 'model-report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps({'status': report['status'],
                          'out_dir': str(out.resolve()),
                          'blockers': len(report['blockers']),
                          'warnings': len(report['warnings'])},
                         ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'ready' else 1
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({'status': 'invalid_input', 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
