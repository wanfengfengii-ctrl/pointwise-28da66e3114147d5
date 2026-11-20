def to_pressure(profile, raw_code):
    if not profile.allows_raw_code(raw_code):
        raise ValueError('raw code outside the calibrated interval')
    return profile.slope * raw_code + profile.intercept
