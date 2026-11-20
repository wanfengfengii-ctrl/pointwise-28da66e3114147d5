from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileVersion:
    line: str
    revision: str
    segment_count: int
    raw_min: int
    raw_max: int
    slope: int
    intercept: int

    @property
    def key(self):
        return self.line, self.revision

    def validate(self):
        for label in self.key:
            if not isinstance(label, str) or not label.strip():
                raise ValueError('line and revision must be nonblank strings')
        parameters = (
            self.segment_count, self.raw_min, self.raw_max, self.slope, self.intercept
        )
        if any(type(value) is not int for value in parameters):
            raise ValueError('profile parameters must be integers')
        if self.segment_count <= 0 or self.raw_min > self.raw_max:
            raise ValueError('invalid segment count or raw interval')
        return self

    @property
    def positions(self):
        return range(1, self.segment_count + 1)

    def allows_position(self, position):
        return type(position) is int and 1 <= position <= self.segment_count

    def allows_raw_code(self, raw_code):
        return type(raw_code) is int and self.raw_min <= raw_code <= self.raw_max
