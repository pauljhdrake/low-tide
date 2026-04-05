from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class LyricLine:
    timestamp: float
    text: str


_LRC_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")


def parse_lrc(lrc_text: str) -> list[LyricLine]:
    """Parse LRC-format timed lyrics into a sorted list of LyricLine."""
    lines = []
    for raw in lrc_text.splitlines():
        m = _LRC_RE.match(raw.strip())
        if m:
            ts = int(m.group(1)) * 60 + float(m.group(2))
            text = m.group(3).strip()
            if text:
                lines.append(LyricLine(ts, text))
    return sorted(lines, key=lambda l: l.timestamp)


def current_line_index(lines: list[LyricLine], position: float) -> int:
    """Return the index of the current lyric line for the given playback position."""
    idx = 0
    for i, line in enumerate(lines):
        if line.timestamp <= position:
            idx = i
        else:
            break
    return idx
