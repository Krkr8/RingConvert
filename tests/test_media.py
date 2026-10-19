# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess

import pytest

from ringconvert.core import convert, metadata
from ringconvert.core.compress import compress
from ringconvert.core.detect import detect

pytestmark = pytest.mark.slow


def streams(path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", str(path)], capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    return {
        "video": [s["codec_name"] for s in info["streams"] if s["codec_type"] == "video"],
        "audio": [s["codec_name"] for s in info["streams"] if s["codec_type"] == "audio"],
        "tags": info["format"].get("tags", {}),
        "duration": float(info["format"].get("duration", 0)),
    }


@pytest.mark.parametrize("target, video, audio", [
    ("mkv", ["h264"], ["aac"]),      # stream copy
    ("mov", ["h264"], ["aac"]),      # stream copy
    ("webm", ["vp9"], ["opus"]),     # re-encode
    ("gif", ["gif"], []),
    ("mp3", [], ["mp3"]),
    ("m4a", [], ["aac"]),
    ("wav", [], ["pcm_s16le"]),
])
def test_video(mp4_file, target, video, audio):
    seen = []
    [out] = convert(mp4_file, target, progress=seen.append).outputs
    assert out.name == f"Clip.{target}"
    info = streams(out)
    assert info["video"] == video and info["audio"] == audio
    assert seen and seen[-1] == 1.0


@pytest.mark.parametrize("target, codec", [
    ("mp3", "mp3"), ("m4a", "aac"), ("ogg", "vorbis"), ("flac", "flac"),
])
def test_audio(wav_file, target, codec):
    [out] = convert(wav_file, target).outputs
    info = streams(out)
    assert info["audio"] == [codec]
    assert abs(info["duration"] - 2.0) < 0.2


def test_compress_video(mp4_file):
    [out] = compress(detect(mp4_file)).outputs
    assert out.name == "Clip Compressed.mp4"
    info = streams(out)
    assert info["video"] == ["h264"] and info["audio"] == ["aac"]


def test_compress_audio(wav_file):
    [mp3] = convert(wav_file, "mp3").outputs
    [out] = compress(detect(mp3)).outputs
    assert out.name == "Tone Compressed.mp3"


def test_wav_has_no_compress(wav_file):
    from ringconvert.core.detect import tools

    assert "compress" not in tools(detect(wav_file))


def test_metadata_strip(mp4_file):
    info = detect(mp4_file)
    assert streams(mp4_file)["tags"].get("title") == "Test Clip"
    [out] = metadata.strip(info).outputs
    assert out.name == "Clip Clean.mp4"
    assert "title" not in streams(out)["tags"]
    assert any("Test Clip" in value for _, _, value in metadata.read(info))
