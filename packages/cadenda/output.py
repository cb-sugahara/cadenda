"""Bounded diagnostic excerpts, separate from complete structured responses."""


class LogExcerpt:
    def __init__(self, limit=1_048_576):
        self.limit = limit
        self.total = 0
        self.head = bytearray()
        self.tail = bytearray()

    def append(self, data):
        self.total += len(data)
        head_size = self.limit // 2
        take = min(len(data), head_size - len(self.head))
        self.head.extend(data[:take])
        self.tail.extend(data[take:])
        tail_size = self.limit - head_size
        if len(self.tail) > tail_size:
            del self.tail[:-tail_size]

    @property
    def truncated(self):
        return self.total > self.limit

    def render(self):
        if not self.truncated:
            return bytes(self.head + self.tail)
        omitted = self.total - len(self.head) - len(self.tail)
        marker = f'\n[...TRUNCATED: {omitted} bytes omitted...]\n'.encode()
        return bytes(self.head) + marker + bytes(self.tail)
