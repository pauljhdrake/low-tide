from __future__ import annotations

from lowtide.widgets.now_playing import _quality_label


def test_effective_flac_format_is_visible():
    label = _quality_label(
        "HI_RES_LOSSLESS",
        bpm=120,
        explicit=False,
        bit_depth=24,
        sample_rate=48_000,
    )
    assert "FLAC MAX 24-bit/48 kHz" in label


def test_effective_aac_fallback_is_visible():
    label = _quality_label(
        "HIGH",
        bpm=None,
        explicit=False,
        bit_depth=16,
        sample_rate=44_100,
    )
    assert label == "AAC 320k"
