"""Deterministic synthetic FIT activities with no real route or athlete data."""

from datetime import UTC, datetime
from struct import calcsize, pack
from zlib import compress

START = int(datetime(2026, 1, 1, 22, 0, tzinfo=UTC).timestamp())
FIT_EPOCH = 631065600
BASE_TYPES = {"B": 0x02, "e": 0x00, "b": 0x01, "H": 0x84, "I": 0x86, "i": 0x85}


def crc(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


class FitWriter:
    def __init__(self, endian="<", reserved=False):
        self.endian = endian
        self.reserved_bit = 0x10 if reserved else 0
        self.body = bytearray()
        self.formats = {}

    def definition(self, local, global_id, fields):
        self.body.append(0x40 | local | self.reserved_bit)
        self.body.extend(pack(self.endian + "BBHB", 0, self.endian == ">", global_id, len(fields)))
        for number, shape in fields:
            base = BASE_TYPES[shape]
            self.body.extend((number, calcsize(self.endian + shape.replace("e", "B")), base))
        self.formats[local] = self.endian + "".join(shape.replace("e", "B") for _, shape in fields)

    def message(self, local, values, *, compressed=None):
        header = local | self.reserved_bit if compressed is None else 0x80 | (local << 5) | (compressed & 31)
        self.body.append(header)
        self.body.extend(pack(self.formats[local], *values))

    def finish(self, header_size):
        header = pack("<BBHI4s", header_size, 0x20, 2180, len(self.body), b".FIT")
        if header_size == 14:
            header += pack("<H", crc(header))
        data = header + self.body
        return data + pack("<H", crc(data))


def cycling_fit(
    *, start=START, elapsed=120, ranges=None, timer=None, samples=None,
    power=200, speed=5, header_size=14, endian="<", reserved=False,
    include_events=True, file_type=4, manufacturer=107, distance=600, compressed=False,
):
    if ranges is None:
        ranges = [(0, elapsed)]
    if timer is None:
        timer = sum(end - start for start, end in ranges)
    epoch = start - FIT_EPOCH

    writer = FitWriter(endian, reserved)
    writer.definition(0, 0, [(0, "e"), (1, "H"), (2, "H"), (4, "I")])
    writer.message(0, (file_type, manufacturer, 1, epoch))
    writer.definition(1, 21, [(253, "I"), (0, "e"), (1, "e"), (4, "B")])
    if include_events:
        for lower, upper in ranges:
            writer.message(1, (epoch + lower, 0, 0, 0))
            writer.message(1, (epoch + upper, 0, 4, 0))
    channels = [(7, "H"), (73, "I"), (3, "B"), (4, "B"), (78, "I"), (0, "i"), (1, "i"),
                (5, "I"), (13, "b"), (200, "H")]
    writer.definition(2, 20, [(253, "I"), *channels])
    if compressed:
        writer.definition(3, 20, channels)
    for second in range(elapsed) if samples is None else samples:
        watts = power(second) if callable(power) else power
        velocity = speed(second) if callable(speed) else speed
        fields = (
            0xFFFF if watts is None else watts, 0xFFFFFFFF if velocity is None else int(velocity * 1000),
            150, 88, 3000, 0, (1 << 30), second * 500, -3, 42,
        )
        if compressed and second not in (0,):
            writer.message(3, fields, compressed=epoch + second)
        else:
            writer.message(2, (epoch + second, *fields))

    writer.definition(4, 18, [
        (253, "I"), (2, "I"), (5, "e"), (7, "I"), (8, "I"), (9, "I"),
        (22, "H"), (23, "H"), (11, "H"),
    ])
    writer.message(4, (epoch + elapsed, epoch, 2, elapsed * 1000, timer * 1000, distance * 100, 29, 12, 15))
    writer.definition(5, 34, [(253, "I"), (0, "I"), (1, "H")])
    writer.message(5, (epoch + elapsed, timer * 1000, 1))
    return writer.finish(header_size)


def magene_wrapper(standard):
    header_size = standard[0]
    blocks = []
    body = standard[header_size:-2]
    for index in range(0, len(body), 530):
        block = compress(body[index:index + 530])
        blocks.append(pack("<H", len(block)) + block)
    return b".ZIP" + standard[:header_size] + b"".join(blocks) + standard[-2:]
