"""Square (1:1) and landscape (16:9) outputs.

Both reuse the face track that drives the vertical crop: ``analyze_vertical_clip``
feeds ``build_crop_trajectory``, so the crop follows the speaker the same way. A
source that cannot fill the frame without losing the subject (a portrait source
for 16:9) is shown whole over a blurred copy of itself instead.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import UPSCALE_SHARPEN_FILTER, logger
from .ffmpeg import (
    build_audio_output_args,
    build_final_video_encode_args,
    ffprobe_has_audio,
    ffprobe_video_size,
    round_to_even,
    run_ffmpeg_command,
    subtitles_filter_fragment,
)

SQUARE = "square"
LANDSCAPE = "landscape"
ASPECT_FORMATS = {SQUARE, LANDSCAPE}

# Output size per format. The short side matches the vertical output (1080) so
# a plan's resolution cap and the caption scale stay the same across ratios.
ASPECT_DIMENSIONS: Dict[str, Tuple[int, int]] = {
    SQUARE: (1080, 1080),
    LANDSCAPE: (1920, 1080),
}

# Caption centre as a fraction of frame height; the vertical template default
# (0.80) would sit on the subject's chest in the shorter frames.
ASPECT_CAPTION_Y: Dict[str, float] = {SQUARE: 0.82, LANDSCAPE: 0.86}

# A source within this ratio error of the target is scaled, not cropped.
RATIO_TOLERANCE = 0.02


def is_aspect_format(output_format: str) -> bool:
    return output_format in ASPECT_FORMATS


def aspect_dimensions(output_format: str) -> Tuple[int, int]:
    width, height = ASPECT_DIMENSIONS[output_format]
    return round_to_even(width), round_to_even(height)


def caption_font_width(output_format: str) -> int:
    """Width the caption font is scaled against.

    ``get_scaled_font_size`` scales by frame width, tuned for 1080-wide
    vertical frames. A 1920-wide landscape frame would draw captions 1.8x too
    large, so every format scales against the 1080 short side.
    """
    return min(ASPECT_DIMENSIONS[output_format])


def aspect_crop_box(width: int, height: int, output_format: str) -> Tuple[int, int]:
    """Even crop box of the target ratio that fits inside the source frame."""
    out_w, out_h = ASPECT_DIMENSIONS[output_format]
    ratio = out_w / out_h
    if width <= 0 or height <= 0:
        return width, height
    if width / height > ratio:
        crop_w, crop_h = round_to_even(int(height * ratio)), round_to_even(height)
    else:
        crop_w, crop_h = round_to_even(width), round_to_even(int(width / ratio))
    return (
        max(2, min(crop_w, round_to_even(width))),
        max(2, min(crop_h, round_to_even(height))),
    )


def source_fills_ratio(width: int, height: int, output_format: str) -> bool:
    """Whether a crop keeps enough of the frame to be worth taking.

    A crop is refused when it would throw away more than half the source
    (a 9:16 portrait source for 16:9 keeps 32% of its pixels).
    """
    crop_w, crop_h = aspect_crop_box(width, height, output_format)
    return width > 0 and height > 0 and (crop_w * crop_h) / (width * height) >= 0.5


def blurred_fit_graph(out_w: int, out_h: int, source: str = "[0:v]") -> str:
    """The whole source, letterboxed over a blurred copy of itself."""
    bg_w, bg_h = round_to_even(out_w // 2), round_to_even(out_h // 2)
    return (
        f"{source}split=2[bgsrc][ftsrc];"
        f"[bgsrc]scale={bg_w}:{bg_h}:force_original_aspect_ratio=increase,crop={bg_w}:{bg_h},"
        f"gblur=sigma=14:steps=2,scale={out_w}:{out_h}:flags=bilinear,setsar=1[bg];"
        f"[ftsrc]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"{UPSCALE_SHARPEN_FILTER},setsar=1[fit];"
        "[bg][fit]overlay=(W-w)/2:(H-h)/2[vout]"
    )


def build_aspect_filter_plan(
    input_path: Path,
    width: int,
    height: int,
    output_format: str,
    *,
    track=None,
) -> Tuple[str, str]:
    """(filter, mode) for a 1:1 or 16:9 frame; mode is "vf" or "complex".

    "vf" filters read ``[0:v]`` implicitly; "complex" ones end in ``[vout]``.
    ``track`` is a precomputed face track (see ``analyze_vertical_clip``).
    """
    from .reframing import (
        analyze_vertical_clip,
        build_crop_trajectory,
        build_smooth_pan_expression,
        trajectory_has_movement,
    )

    out_w, out_h = aspect_dimensions(output_format)
    scale = f"scale={out_w}:{out_h}:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1"
    ratio = out_w / out_h

    if width > 0 and height > 0 and abs(width / height - ratio) / ratio <= RATIO_TOLERANCE:
        return scale, "vf"
    if not source_fills_ratio(width, height, output_format):
        return blurred_fit_graph(out_w, out_h), "complex"

    crop_w, crop_h = aspect_crop_box(width, height, output_format)
    if track is None:
        try:
            track = analyze_vertical_clip(input_path, analyze_context=False)[0]
        except Exception as exc:
            logger.warning("Face track for %s crop failed: %s", output_format, exc)
            track = []

    # The crop slides along whichever axis the source is longer on; a face
    # track only exists for the horizontal one.
    x_expr = str((width - crop_w) // 2 // 2 * 2)
    y_expr = str((height - crop_h) // 2 // 2 * 2)
    if width - crop_w > 0 and track:
        keys = build_crop_trajectory(track, width, crop_w)
        if trajectory_has_movement(keys, crop_w):
            x_expr = build_smooth_pan_expression(keys)
        elif keys:
            x_expr = str(int(keys[0][1]) // 2 * 2)
    return f"crop={crop_w}:{crop_h}:x='{x_expr}':y='{y_expr}',{scale}", "vf"


def render_aspect_clip_ffmpeg(
    input_path: Path,
    output_path: Path,
    output_format: str,
    subtitle_ass_path: Optional[Path] = None,
    fonts_dir: Optional[Path] = None,
    plan: Optional[Tuple[str, str]] = None,
    broll_overlays: Optional[List[dict]] = None,
) -> Tuple[bool, int, int]:
    """Render a 1:1 or 16:9 clip and burn subtitles in the same pass."""
    from .broll_overlay import (
        broll_input_args,
        broll_overlay_graph,
        normalize_broll_overlays,
    )
    from .ffmpeg import ffprobe_duration

    out_w, out_h = aspect_dimensions(output_format)
    width, height = ffprobe_video_size(input_path)
    if plan is None:
        plan = build_aspect_filter_plan(input_path, width, height, output_format)
    video_filter, mode = plan
    head = video_filter if mode == "complex" else f"[0:v]{video_filter}[vout]"
    subs = (
        subtitles_filter_fragment(subtitle_ass_path, fonts_dir)
        if subtitle_ass_path
        else None
    )
    overlays = (
        normalize_broll_overlays(broll_overlays, ffprobe_duration(input_path))
        if broll_overlays
        else []
    )
    overlay_graph, label = broll_overlay_graph("[vout]", overlays, out_w, out_h)
    parts = [head]
    if overlay_graph:
        parts.append(overlay_graph)
    if subs:
        parts.append(f"{label}{subs}[v]")
        label = "[v]"
    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        *broll_input_args(overlays),
        "-filter_complex", ";".join(parts),
        "-map", label, "-map", "0:a?",
        *build_final_video_encode_args(),
        *build_audio_output_args(ffprobe_has_audio(input_path)),
        "-movflags", "+faststart",
        str(output_path),
    ]
    return run_ffmpeg_command(command).returncode == 0, out_w, out_h
