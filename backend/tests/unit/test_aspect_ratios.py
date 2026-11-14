from pathlib import Path

import pytest

from src.clip_editor import EXPORT_PRESETS
from src.media import aspect
from src.media.common import VALID_OUTPUT_FORMATS
from src.video_utils import aspect_font_kwargs


def test_formats_are_valid_output_formats():
    assert {"square", "landscape"} <= VALID_OUTPUT_FORMATS


@pytest.mark.parametrize("fmt,dims", [("square", (1080, 1080)), ("landscape", (1920, 1080))])
def test_dimensions_are_even(fmt, dims):
    assert aspect.aspect_dimensions(fmt) == dims
    assert all(d % 2 == 0 for d in dims)


@pytest.mark.parametrize("src", [(1920, 1080), (1280, 720), (3840, 2160), (1080, 1920), (1000, 777), (721, 1281)])
@pytest.mark.parametrize("fmt", ["square", "landscape"])
def test_crop_box_fits_source_with_even_sides(src, fmt):
    w, h = aspect.aspect_crop_box(*src, fmt)
    assert w % 2 == 0 and h % 2 == 0
    assert w <= src[0] + 1 and h <= src[1] + 1
    out_w, out_h = aspect.ASPECT_DIMENSIONS[fmt]
    assert abs(w / h - out_w / out_h) < 0.02


def test_portrait_source_is_not_cropped_to_landscape():
    assert not aspect.source_fills_ratio(1080, 1920, "landscape")
    assert aspect.source_fills_ratio(1080, 1920, "square")
    assert aspect.source_fills_ratio(1920, 1080, "square")


def test_matching_ratio_is_scaled_not_cropped():
    video_filter, mode = aspect.build_aspect_filter_plan(Path("x.mp4"), 1920, 1080, "landscape")
    assert mode == "vf" and video_filter.startswith("scale=1920:1080") and "crop" not in video_filter


def test_portrait_to_landscape_uses_blurred_fit():
    video_filter, mode = aspect.build_aspect_filter_plan(Path("x.mp4"), 1080, 1920, "landscape")
    assert mode == "complex" and "gblur" in video_filter and video_filter.endswith("[vout]")


def test_square_crop_follows_face_track():
    track = [(0.0, 400.0, 0.1), (1.0, 400.0, 0.1), (2.0, 1500.0, 0.1), (3.0, 1500.0, 0.1), (4.0, 1500.0, 0.1)]
    video_filter, mode = aspect.build_aspect_filter_plan(Path("x.mp4"), 1920, 1080, "square", track=track)
    assert mode == "vf"
    assert video_filter.startswith("crop=1080:1080:x='")
    assert "if(lt(t" in video_filter and video_filter.endswith("scale=1080:1080:flags=lanczos," + aspect.UPSCALE_SHARPEN_FILTER + ",setsar=1")


def test_square_without_face_track_centres_the_crop():
    video_filter, _ = aspect.build_aspect_filter_plan(Path("x.mp4"), 1920, 1080, "square", track=[])
    assert video_filter.startswith("crop=1080:1080:x='420':y='0'")


def test_wider_than_landscape_source_is_cropped():
    video_filter, mode = aspect.build_aspect_filter_plan(Path("x.mp4"), 2560, 1080, "landscape", track=[])
    assert mode == "vf" and video_filter.startswith("crop=1920:1080:")


def test_caption_font_scales_by_short_side():
    # Same pixel size as vertical (1080 wide), not 1.8x larger on a 1920-wide frame.
    square = aspect_font_kwargs("square", 20, "default")["font_px_override"]
    landscape = aspect_font_kwargs("landscape", 20, "default")["font_px_override"]
    assert square == landscape == round(20 * 1080 / 560)
    assert aspect_font_kwargs("vertical", 20, "default") == {}


def test_caption_sits_inside_the_frame():
    assert all(0.5 < y < 0.9 for y in aspect.ASPECT_CAPTION_Y.values())


def test_export_presets():
    assert (EXPORT_PRESETS["square"].width, EXPORT_PRESETS["square"].height) == (1080, 1080)
    assert (EXPORT_PRESETS["landscape"].width, EXPORT_PRESETS["landscape"].height) == (1920, 1080)
    assert EXPORT_PRESETS["square"].blur_fit and EXPORT_PRESETS["landscape"].blur_fit
    assert not EXPORT_PRESETS["tiktok"].blur_fit
