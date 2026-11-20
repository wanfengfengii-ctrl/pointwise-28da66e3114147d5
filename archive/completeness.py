def missing_positions(profile, admitted):
    present = {segment.position for segment in admitted}
    return tuple(position for position in profile.positions if position not in present)


def complete_segments(profile, admitted):
    if len(admitted) != profile.segment_count:
        return None
    ordered = tuple(sorted(admitted, key=lambda segment: segment.position))
    if any(segment.position != position for position, segment in enumerate(ordered, 1)):
        return None
    return ordered
