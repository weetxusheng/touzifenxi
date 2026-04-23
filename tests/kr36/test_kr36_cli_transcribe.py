"""kr36-transcribe-audio 输出路径与专题落盘一致。"""

from __future__ import annotations

from pathlib import Path

from kr36.cli import _kr36_transcribe_output_paths


def test_transcribe_paths_strip_asr_mp3_suffix() -> None:
    audio = Path("d") / "于东来_36kr_topic_video_1.asr.mp3"
    tr, js = _kr36_transcribe_output_paths(audio)
    assert tr == Path("d") / "于东来_36kr_topic_video_1.transcript.txt"
    assert js == Path("d") / "于东来_36kr_topic_video_1.asr.json"


def test_transcribe_paths_other_ext_uses_stem() -> None:
    audio = Path("x") / "clip.mp3"
    tr, js = _kr36_transcribe_output_paths(audio)
    assert tr == Path("x") / "clip.transcript.txt"
    assert js == Path("x") / "clip.asr.json"
