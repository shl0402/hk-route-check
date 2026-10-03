"""Use ETA-derived proportions only between preserved GTFS timing anchors.

These are experimental inferred timings, never measured journeys or live ETAs.
A missing/implausible span retains the existing distance interpolation intact.
"""
import math
from .feed import seconds, clock

POLICY = {
    'kind': 'eta_derived_weights_between_published_anchors',
    'min_segment_seconds': 5, 'max_segment_seconds': 3960,
    'raw_to_anchor_ratio': [2 / 3, 1.5],
    'maximum_projected_speed_kmh': 100,
    'minimum_projected_speed_kmh': 1,
    'scope': 'bus and green minibus only; all-day profile',
    'preserved': 'Every supplied arrival/departure and route total; no initial wait added',
    'limitations': 'ETA matching, smoothing and a 10% upstream margin; no sample counts, per-edge dates or vehicle identity. Stop-pair data can mix routes.',
}


def reweight(anchor_rows, current_rows, stop_ids, estimates, distances=None, conflicts=None):
    """Return rows, accepted spans and rejected reasons. Never partially fill a span."""
    if len(anchor_rows) != len(current_rows) or len(stop_ids) != len(current_rows):
        raise ValueError('Timing and identity sequences differ')
    if any((a['stop_id'], a['stop_sequence']) != (b['stop_id'], b['stop_sequence'])
           for a, b in zip(anchor_rows, current_rows)):
        raise ValueError('Timing anchor order changed')
    if distances is not None and (len(distances) != len(current_rows) or
                                 any(b < a for a, b in zip(distances, distances[1:]))):
        raise ValueError('Invalid matched route distances')
    result = [dict(r) for r in current_rows]
    anchors = [i for i, r in enumerate(anchor_rows) if r.get('arrival_time') and r.get('departure_time')]
    accepted, rejected = [], []
    for start, end in zip(anchors, anchors[1:]):
        if end - start < 2:
            continue
        pairs = list(zip(stop_ids[start:end], stop_ids[start+1:end+1]))
        reason = None
        if any(pair in (conflicts or set()) for pair in pairs):
            reason = 'same_stop_pair_has_different_route_paths'
        weights = [estimates.get(a, {}).get(b) for a, b in pairs]
        if not reason and any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
                              or not POLICY['min_segment_seconds'] <= v <= POLICY['max_segment_seconds'] for v in weights):
            reason = 'missing_or_invalid_segment_estimate'
        begin = seconds(anchor_rows[start]['departure_time'])
        finish = seconds(anchor_rows[end]['arrival_time'])
        duration = finish - begin
        if not reason and (duration < len(pairs) or not POLICY['raw_to_anchor_ratio'][0] <= sum(weights) / duration <= POLICY['raw_to_anchor_ratio'][1]):
            reason = 'inferred_total_disagrees_with_published_anchors'
        if not reason:
            total = sum(weights)
            boundaries = [begin]
            cumulative = 0.0
            for i, weight in enumerate(weights[:-1], 1):
                cumulative += weight
                boundaries.append(max(boundaries[-1] + 1, min(round(begin + duration * cumulative / total), finish - (len(pairs)-i))))
            boundaries.append(finish)
            if distances is None:
                reason = 'no_verified_road_geometry'
            else:
                for i, (a, b) in enumerate(zip(boundaries, boundaries[1:]), start):
                    metres = distances[i+1] - distances[i]
                    kmh = metres * 3.6 / (b-a)
                    if metres > 100 and not 1 <= kmh <= 100:
                        reason = 'implausible_projected_segment_speed'
                        break
        span = {'start_sequence': anchor_rows[start]['stop_sequence'],
                'end_sequence': anchor_rows[end]['stop_sequence']}
        valid_weights = all(isinstance(w, (int,float)) and not isinstance(w,bool) and math.isfinite(w) for w in weights)
        span.update(source_seconds=round(sum(weights),3) if valid_weights else None,
                    preserved_duration_seconds=duration,
                    total_conflict=bool(valid_weights and duration>0 and not POLICY['raw_to_anchor_ratio'][0] <= sum(weights)/duration <= POLICY['raw_to_anchor_ratio'][1]))
        if reason:
            rejected.append({**span, 'reason': reason})
            continue
        for i, time in enumerate(boundaries[1:-1], start + 1):
            # Supplied fields with a missing counterpart are protected too.
            if anchor_rows[i].get('arrival_time') or anchor_rows[i].get('departure_time'):
                reason = 'partial_source_timing_inside_span'
                break
        if reason:
            rejected.append({**span, 'reason': reason})
            continue
        for i, time in enumerate(boundaries[1:-1], start + 1):
            result[i].update(arrival_time=clock(time), departure_time=clock(time), timepoint='0')
        accepted.append({**span, 'segments': len(pairs), 'source_seconds': round(total, 3),
                         'preserved_duration_seconds': duration, 'scale': round(duration / total, 6)})
    return result, accepted, rejected
