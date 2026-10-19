# SPDX-License-Identifier: GPL-3.0-or-later
"""Audio/video conversion with ffmpeg."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from . import backends
from .errors import RingConvertError
from .naming import Result, discard, output_path

Progress = Callable[[float], None] | None

# Codecs each container takes as-is, so a remux can skip re-encoding.
_COPY_OK = {
    "mp4": ({"h264", "hevc", "av1", "mpeg4"}, {"aac", "mp3", "alac", "ac3", "eac3"}),
    "mov": ({"h264", "hevc", "prores", "mpeg4", "mjpeg"}, {"aac", "alac", "mp3", "pcm_s16le", "pcm_s24le"}),
    "webm": ({"vp8", "vp9", "av1"}, {"opus", "vorbis"}),
    "mkv": (None, None),  # Matroska holds anything
    "mp3": (set(), {"mp3"}),
    "m4a": (set(), {"aac", "alac"}),
    "ogg": (set(), {"vorbis", "opus"}),
    "flac": (set(), {"flac"}),
    "wav": (set(), {"pcm_s16le", "pcm_s24le", "pcm_f32le"}),
}
AUDIO_ONLY = {"mp3", "m4a", "wav", "ogg", "flac"}


def probe(src: Path) -> dict:
    exe = backends.require_program("ffprobe")
    proc = subprocess.run(
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(src)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RingConvertError(f"Can't read {src.name}: {proc.stderr.strip()[-300:]}")
    return json.loads(proc.stdout)


def _streams(info: dict, kind: str) -> list[dict]:
    return [
        s for s in info.get("streams", [])
        if s.get("codec_type") == kind and not s.get("disposition", {}).get("attached_pic")
    ]


def duration(info: dict) -> float:
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError):
        return 0.0


def h264_args(quality: int) -> list[str]:
    """H.264 encoder args; `quality` is an x264 CRF (lower = better)."""
    if backends.first_encoder("libx264"):
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(quality), "-pix_fmt", "yuv420p"]
    if backends.first_encoder("libopenh264"):
        # ffmpeg-free only ships OpenH264, which is bitrate-driven: map CRF to
        # a rough bits-per-pixel budget instead.
        return ["-c:v", "libopenh264", "-rc_mode", "quality", "-b:v", "6M" if quality <= 20 else "2M", "-pix_fmt", "yuv420p"]
    if backends.first_encoder("mpeg4"):
        return ["-c:v", "mpeg4", "-q:v", "4" if quality <= 23 else "8"]
    raise RingConvertError("ffmpeg has no H.264 encoder (install ffmpeg from RPM Fusion)")


def video_args(target: str, quality: int = 20) -> list[str]:
    if target == "webm":
        crf = str(min(63, quality + 13))  # VP9's CRF scale sits higher than x264's
        return ["-c:v", "libvpx-vp9", "-crf", crf, "-b:v", "0", "-row-mt", "1",
                "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p"]
    return h264_args(quality)


def audio_args(target: str, bitrate: str | None = None) -> list[str]:
    if target in ("webm", "ogg"):
        if target == "ogg":
            return ["-c:a", "libvorbis", "-q:a", "3" if bitrate else "5"]
        return ["-c:a", "libopus", "-b:a", bitrate or "128k"]
    if target == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", bitrate] if bitrate else ["-c:a", "libmp3lame", "-q:a", "2"]
    if target == "wav":
        return ["-c:a", "pcm_s16le"]
    if target == "flac":
        return ["-c:a", "flac", "-compression_level", "12" if bitrate else "8"]
    aac = backends.first_encoder("libfdk_aac", "aac") or "aac"
    return ["-c:a", aac, "-b:a", bitrate or "192k"]


def run_ffmpeg(args: list[str], dst: Path, total: float, progress: Progress) -> None:
    exe = backends.require_program("ffmpeg")
    cmd = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-progress", "pipe:1", "-nostats", *args, str(dst)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if progress and total > 0 and line.startswith("out_time_us="):
            try:
                progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / total))
            except ValueError:
                pass
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise RingConvertError(f"ffmpeg failed: {stderr.strip()[-400:]}")
    if progress:
        progress(1.0)


def _gif_args(src: Path) -> list[str]:
    graph = ("fps=12,scale='min(iw,640)':-2:flags=lanczos,split[a][b];"
             "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    return ["-i", str(src), "-filter_complex", graph, "-loop", "0"]


def build_args(src: Path, info: dict, target: str) -> list[str]:
    video = _streams(info, "video")
    audio = _streams(info, "audio")
    if target == "gif":
        if not video:
            raise RingConvertError(f"{src.name} has no video")
        return _gif_args(src)
    if target in AUDIO_ONLY and not audio:
        raise RingConvertError(f"{src.name} has no audio track")
    vcopy, acopy = _COPY_OK[target]
    if target in AUDIO_ONLY:
        args = ["-i", str(src), "-vn", "-map", "0:a:0", "-map_metadata", "0"]
        audio = audio[:1]
    else:
        args = ["-i", str(src), "-map", "0:v:0?", "-map", "0:a?", "-map_metadata", "0"]
    if target not in AUDIO_ONLY and video:
        if vcopy is None or video[0].get("codec_name") in vcopy:
            args += ["-c:v", "copy"]
        else:
            args += video_args(target)
    if audio:
        if acopy is None or all(a.get("codec_name") in acopy for a in audio):
            args += ["-c:a", "copy"]
        else:
            args += audio_args(target)
    if target in ("mp4", "mov", "m4a"):
        args += ["-movflags", "+faststart"]
    if target == "mkv":
        args += ["-map", "0:s?", "-c:s", "copy"]
    return args


def convert(src: Path, target: str, progress: Progress = None) -> Result:
    info = probe(src)
    args = build_args(src, info, target)
    result = Result()
    dst = output_path(src, target, result=result)
    try:
        run_ffmpeg(args, dst, duration(info), progress)
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result


def strip_metadata(src: Path, dst: Path) -> None:
    run_ffmpeg(["-i", str(src), "-map", "0", "-map_metadata", "-1", "-map_chapters", "-1",
                "-c", "copy", "-fflags", "+bitexact"], dst, 0, None)


def compress(src: Path, dst: Path, progress: Progress = None) -> None:
    info = probe(src)
    target = dst.suffix.lower().lstrip(".")
    target = {"oga": "ogg", "opus": "ogg"}.get(target, target)
    video = _streams(info, "video")
    audio = _streams(info, "audio")
    args = ["-i", str(src), "-map_metadata", "-1"]
    if target in AUDIO_ONLY:
        if not audio:
            raise RingConvertError(f"{src.name} has no audio")
        args += ["-vn", "-map", "0:a:0", *audio_args(target, bitrate="128k")]
    else:
        args += ["-map", "0:v:0?", "-map", "0:a?"]
        if video:
            args += video_args(target, quality=28)
        if audio:
            args += audio_args("webm" if target == "webm" else "m4a", bitrate="128k")
        if target in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]
    run_ffmpeg(args, dst, duration(info), progress)


def read_metadata(src: Path) -> list[tuple[str, str, str]]:
    info = probe(src)
    fmt = info.get("format", {})
    rows = [("Container", "format", fmt.get("format_long_name", "")),
            ("Container", "duration", f"{duration(info):.2f} s")]
    rows += [("Container", k, str(v)) for k, v in fmt.get("tags", {}).items()]
    for s in info.get("streams", []):
        group = f"Stream {s.get('index')} ({s.get('codec_type')})"
        rows.append((group, "codec", s.get("codec_name", "")))
        if s.get("width"):
            rows.append((group, "size", f"{s['width']}x{s['height']}"))
        rows += [(group, k, str(v)) for k, v in s.get("tags", {}).items()]
    return rows
