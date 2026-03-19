import subprocess
import os
import json
import tempfile
from pathlib import Path


def run_ffmpeg(args: list[str], description: str = "") -> str:
    cmd = ["ffmpeg", "-y"] + args
    print(f"FFmpeg: {description}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FFmpeg FULL ERROR:\n{result.stderr}")  # full log
        raise Exception(f"FFmpeg failed ({description}): {result.stderr[-1000:]}")
    return result.stdout


def extract_audio(video_path: str, audio_path: str) -> str:
    """Extract audio track from video for transcription."""
    run_ffmpeg(
        ["-i", video_path, "-vn", "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", audio_path],
        "extract audio"
    )
    return audio_path


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", video_path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def detect_silence(video_path: str, noise_db: float = -35, min_silence: float = 0.8) -> list[dict]:
    """
    Detect silent segments in video.
    Returns list of {start, end} dicts for silent periods.
    """
    result = subprocess.run(
        ["ffmpeg", "-i", video_path,
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True
    )
    stderr = result.stderr
    silences = []
    current_start = None

    for line in stderr.split("\n"):
        if "silence_start" in line:
            try:
                current_start = float(line.split("silence_start: ")[1])
            except Exception:
                pass
        elif "silence_end" in line and current_start is not None:
            try:
                end = float(line.split("silence_end: ")[1].split(" ")[0])
                silences.append({"start": current_start, "end": end})
                current_start = None
            except Exception:
                pass

    return silences


def cut_silences(video_path: str, output_path: str, min_silence: float = 0.8) -> str:
    """
    Remove silent segments from video.
    Keeps a 0.2s buffer around speech for natural feel.
    """
    silences = detect_silence(video_path, min_silence=min_silence)
    duration = get_video_duration(video_path)

    if not silences:
        # No silences found — just copy
        run_ffmpeg(["-i", video_path, "-c", "copy", output_path], "copy (no silences)")
        return output_path

    # Build list of segments to KEEP (inverse of silences)
    keep_segments = []
    prev_end = 0.0
    buffer = 0.2

    for silence in silences:
        seg_end = max(0.0, silence["start"] + buffer)
        if seg_end > prev_end + 0.1:
            keep_segments.append((prev_end, seg_end))
        prev_end = max(prev_end, silence["end"] - buffer)

    if prev_end < duration - 0.1:
        keep_segments.append((prev_end, duration))

    if not keep_segments:
        run_ffmpeg(["-i", video_path, "-c", "copy", output_path], "copy fallback")
        return output_path

    # Build filter_complex for concatenation
    inputs = []
    filter_parts = []

    for i, (start, end) in enumerate(keep_segments):
        inputs += ["-ss", str(start), "-to", str(end), "-i", video_path]
        filter_parts.append(f"[{i}:v][{i}:a]")

    concat_filter = (
        "".join(filter_parts) +
        f"concat=n={len(keep_segments)}:v=1:a=1[outv][outa]"
    )

    run_ffmpeg(
        inputs + [
            "-filter_complex", concat_filter,
            "-map", "[outv]", "-map", "[outa]",
            "-vf", "format=yuv420p",      # add this line
            "-c:v", "libx264", "-c:a", "aac",
            output_path
        ],
        f"cut {len(silences)} silences"
    )
    return output_path


def burn_captions(
    video_path: str,
    srt_path: str,
    output_path: str,
    style: str = "minimal",
) -> str:
    """
    Burn subtitles into video using FFmpeg.
    Styles: minimal | bold | colour_pop
    Uses DejaVu Sans (available on Railway) instead of Arial.
    """
    style_config = {
        "minimal":    "FontName=DejaVu Sans,FontSize=16,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=1,Bold=0,Alignment=2",
        "bold":       "FontName=DejaVu Sans,FontSize=20,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=2,Bold=1,Alignment=2",
        "colour_pop": "FontName=DejaVu Sans,FontSize=20,PrimaryColour=&H00ffff,OutlineColour=&H000000,Outline=2,Bold=1,Alignment=2",
    }
    force_style = style_config.get(style, style_config["minimal"])

    # Escape srt path for FFmpeg subtitle filter
    escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")

    run_ffmpeg(
        ["-i", video_path,
         "-vf", f"subtitles={escaped_srt}:force_style='{force_style}'",
         "-c:v", "libx264",
         "-c:a", "aac",
         "-preset", "fast",
         output_path],
        f"burn captions ({style})"
    )
    return output_path


def export_multi_format(
    video_path: str,
    output_dir: str,
    base_name: str = "output",
) -> dict[str, str]:
    """
    Export video in 3 formats from one source:
    - 9:16 vertical (Reels, Shorts, TikTok)
    - 1:1 square (Instagram feed)
    - 16:9 horizontal (YouTube)
    Returns dict of format → output path.
    """
    os.makedirs(output_dir, exist_ok=True)
    outputs = {}

    formats = {
        "9x16": {
            "suffix": "vertical",
            "filter": "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        },
        "1x1": {
            "suffix": "square",
            "filter": "scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2",
        },
        "16x9": {
            "suffix": "horizontal",
            "filter": "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        },
    }

    for fmt, config in formats.items():
        out_path = os.path.join(output_dir, f"{base_name}_{config['suffix']}.mp4")
        run_ffmpeg(
            ["-i", video_path,
            "-vf", config["filter"],          # scale/pad only
            "-pix_fmt", "yuv420p",            # separate flag — no conflict
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            out_path],
            f"export {fmt}"
        )
        outputs[fmt] = out_path

    return outputs


def trim_clip(video_path: str, start: float, end: float, output_path: str) -> str:
    run_ffmpeg(
        ["-ss", str(start), "-to", str(end),
         "-i", video_path,
         "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-crf", "23", "-preset", "fast",
         "-c:a", "aac", "-b:a", "128k",
         output_path],
        f"trim {start:.1f}s-{end:.1f}s"
    )
    return output_path


def extract_best_frame(video_path: str, output_path: str, time_offset: float = 3.0) -> str:
    """Extract a single frame for thumbnail generation."""
    run_ffmpeg(
        ["-ss", str(time_offset), "-i", video_path,
         "-vframes", "1", "-q:v", "2",
         output_path],
        "extract thumbnail frame"
    )
    return output_path


def timestamps_to_srt(segments: list[dict]) -> str:
    """
    Convert Whisper word-level segments to SRT subtitle format.
    Groups words into lines of ~8 words max.
    """
    def format_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    srt_lines = []
    idx = 1
    words_per_line = 8
    buffer = []

    for seg in segments:
        buffer.append(seg)
        if len(buffer) >= words_per_line or seg == segments[-1]:
            if buffer:
                start = buffer[0].get("start", 0)
                end = buffer[-1].get("end", start + 1)
                text = " ".join(w.get("word", "").strip() for w in buffer)
                srt_lines.append(
                    f"{idx}\n{format_time(start)} --> {format_time(end)}\n{text}\n"
                )
                idx += 1
                buffer = []

    return "\n".join(srt_lines)