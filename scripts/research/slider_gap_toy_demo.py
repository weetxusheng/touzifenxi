"""Local toy demo for slider-gap detection and trajectory visualization.

This script is intentionally self-contained and does not connect to any
website, browser, captcha service, or automation driver. It generates a
synthetic landscape-like image, cuts a puzzle-shaped gap, detects the gap with
OpenCV template matching, and draws a human-like motion curve for study.
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import cv2
import numpy as np


CANVAS_W = 680
CANVAS_H = 360
PIECE_W = 86
PIECE_H = 86


def make_output_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_landscape(width: int = CANVAS_W, height: int = CANVAS_H) -> np.ndarray:
    """Create a synthetic lake/mountain scene so the demo has texture."""

    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    blue = 210 - 65 * y + 12 * np.sin(8 * x)
    green = 230 - 90 * y + 8 * np.cos(5 * x)
    red = np.broadcast_to(245 - 110 * y, (height, width))
    sky = np.dstack(
        [
            blue,
            green,
            red,
        ]
    )
    image = np.clip(sky, 0, 255).astype(np.uint8)

    horizon = int(height * 0.46)
    lake = image[horizon:].copy()
    lake[:, :, 0] = np.clip(lake[:, :, 0] - 40, 0, 255)
    lake[:, :, 1] = np.clip(lake[:, :, 1] - 18, 0, 255)
    lake[:, :, 2] = np.clip(lake[:, :, 2] + 12, 0, 255)
    image[horizon:] = lake

    rng = np.random.default_rng(7)
    for _ in range(5):
        peak_x = int(rng.integers(40, width - 40))
        peak_y = int(rng.integers(35, horizon - 20))
        base_y = horizon + int(rng.integers(-12, 20))
        half = int(rng.integers(90, 190))
        color = tuple(int(v) for v in rng.integers([70, 95, 105], [125, 150, 165]))
        pts = np.array([[peak_x - half, base_y], [peak_x, peak_y], [peak_x + half, base_y]], dtype=np.int32)
        cv2.fillPoly(image, [pts], color)

    for _ in range(65):
        center = (int(rng.integers(0, width)), int(rng.integers(horizon + 10, height)))
        radius = int(rng.integers(8, 24))
        color = tuple(int(v) for v in rng.integers([45, 95, 45], [95, 160, 90]))
        cv2.circle(image, center, radius, color, -1, lineType=cv2.LINE_AA)

    noise = rng.normal(0, 5, image.shape).astype(np.int16)
    return np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def puzzle_mask(width: int = PIECE_W, height: int = PIECE_H) -> np.ndarray:
    """Build a puzzle-like alpha mask with one knob and one bite."""

    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.rectangle(mask, (12, 12), (width - 12, height - 12), 255, -1)
    cv2.circle(mask, (width // 2, 12), 16, 255, -1)
    cv2.circle(mask, (width - 12, height // 2), 15, 0, -1)
    cv2.circle(mask, (width // 2, height - 12), 14, 255, -1)
    cv2.GaussianBlur(mask, (3, 3), 0, dst=mask)
    return mask


def apply_gap(background: np.ndarray, x: int, y: int, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return a gapped background and the puzzle piece cropped from it."""

    h, w = mask.shape
    piece = background[y : y + h, x : x + w].copy()
    alpha = mask.astype(np.float32) / 255.0
    gapped = background.copy()
    region = gapped[y : y + h, x : x + w].astype(np.float32)
    shadow = np.zeros_like(region)
    shadow[:] = (34, 44, 52)
    region = region * (1 - alpha[..., None] * 0.62) + shadow * (alpha[..., None] * 0.62)
    gapped[y : y + h, x : x + w] = np.clip(region, 0, 255).astype(np.uint8)

    piece_rgba = np.dstack([piece, mask])
    return gapped, piece_rgba


def detect_gap(gapped_bgr: np.ndarray, mask: np.ndarray) -> tuple[int, int, float]:
    """Detect the darkened gap location using local darkness response."""

    gray = cv2.cvtColor(gapped_bgr, cv2.COLOR_BGR2GRAY)
    darkness = 255 - gray
    template = (mask.astype(np.float32) / 255.0)
    response = cv2.matchTemplate(darkness.astype(np.float32), template, cv2.TM_CCORR_NORMED)
    # Avoid the very dark foliage strip at the bottom; real demos usually know
    # the puzzle vertical band from the widget layout.
    response[int(CANVAS_H * 0.64) :, :] = 0
    result = cv2.GaussianBlur(response, (5, 5), 0)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return int(max_loc[0]), int(max_loc[1]), float(max_val)


def human_like_trajectory(distance: int, steps: int = 42, seed: int = 11) -> list[tuple[float, float, float]]:
    """Generate a visualization-only track: x, y, t_ms."""

    rng = random.Random(seed)
    points: list[tuple[float, float, float]] = []
    elapsed = 0.0
    overshoot = rng.uniform(4.0, 9.0)
    for i in range(steps):
        p = i / (steps - 1)
        ease = 1 - (1 - p) ** 3
        wobble = math.sin(p * math.pi * 3.5) * rng.uniform(0.2, 1.7)
        x = ease * (distance + overshoot)
        if p > 0.82:
            x -= overshoot * ((p - 0.82) / 0.18)
        y = wobble + rng.uniform(-0.9, 0.9)
        elapsed += rng.uniform(8, 24) if p < 0.78 else rng.uniform(18, 48)
        points.append((x, y, elapsed))
    return points


def draw_result(gapped: np.ndarray, detected: tuple[int, int], track: list[tuple[float, float, float]]) -> np.ndarray:
    canvas = gapped.copy()
    x, y = detected
    cv2.rectangle(canvas, (x, y), (x + PIECE_W, y + PIECE_H), (20, 230, 120), 3)
    base_y = CANVAS_H - 34
    start_x = 40
    prev: tuple[int, int] | None = None
    for tx, ty, _ in track:
        point = (int(start_x + tx), int(base_y + ty))
        if prev is not None:
            cv2.line(canvas, prev, point, (25, 115, 255), 2, lineType=cv2.LINE_AA)
        prev = point
    cv2.circle(canvas, (start_x, base_y), 6, (255, 255, 255), -1, lineType=cv2.LINE_AA)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description="Local OpenCV slider-gap toy demo.")
    parser.add_argument("--output-dir", default="output/research/slider_gap_toy", help="Directory for generated images.")
    parser.add_argument("--gap-x", type=int, default=385)
    parser.add_argument("--gap-y", type=int, default=116)
    args = parser.parse_args()

    out_dir = make_output_dir(Path(args.output_dir))
    background = build_landscape()
    mask = puzzle_mask()
    gapped, piece_rgba = apply_gap(background, args.gap_x, args.gap_y, mask)
    detected_x, detected_y, score = detect_gap(gapped, mask)
    track = human_like_trajectory(detected_x - 40)
    result = draw_result(gapped, (detected_x, detected_y), track)

    cv2.imwrite(str(out_dir / "01_background.png"), background)
    cv2.imwrite(str(out_dir / "02_gapped.png"), gapped)
    cv2.imwrite(str(out_dir / "03_piece.png"), piece_rgba)
    cv2.imwrite(str(out_dir / "04_detected_and_track.png"), result)
    (out_dir / "track.csv").write_text(
        "x,y,t_ms\n" + "\n".join(f"{x:.2f},{y:.2f},{t:.2f}" for x, y, t in track),
        encoding="utf-8",
    )
    print(f"expected=({args.gap_x},{args.gap_y}) detected=({detected_x},{detected_y}) score={score:.4f}")
    print(out_dir.resolve())


if __name__ == "__main__":
    main()
