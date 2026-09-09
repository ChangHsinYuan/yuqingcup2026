#!/usr/bin/env python3
"""3DGS (.ply) 多角度渲染器 — PCA 对齐 + 裁剪接地合成

用法:
  from utils.splat_renderer import render_splat_angles
  paths = render_splat_angles('character.ply', angles=8, output_dir='renders/',
                              bg_image='background.jpg', size=1024)
"""
import sys
import os
import math
import numpy as np
from PIL import Image

sys.path.insert(0, '/mnt/disk_sdb/zxy/ComfyUI')
from comfy.ldm.triposplat.preview import render_splat

_C0 = 0.28209479177387814


def load_ply(ply_path):
    """Load a PLY gaussian splat file, PCA-align upright, return (xyz, rgb, scale, opacity).

    TripoSplat's 3D reconstruction is not axis-aligned. PCA finds the character's
    principal axes; the longest dimension is mapped to Z (up), ensuring the
    character stands vertically regardless of the original orientation.
    """
    with open(ply_path, 'rb') as f:
        header = b''
        while True:
            line = f.readline()
            header += line
            if b'end_header' in line:
                break
        n = 0
        props = 0
        for line in header.split(b'\n'):
            if line.startswith(b'element vertex'):
                n = int(line.split()[-1])
            if line.startswith(b'property'):
                props += 1
        data = f.read(props * 4 * n)
        arr = np.frombuffer(data, dtype=np.float32).reshape(n, props)

    xyz = arr[:, :3].copy().astype(np.float32)
    rgb = np.clip(0.5 + arr[:, 6:9] * _C0, 0, 1)
    scale = np.exp(arr[:, 10:13]).max(axis=1)
    opacity = 1.0 / (1.0 + np.exp(-arr[:, 9]))
    xyz = _align_upright(xyz, rgb)
    xyz, rgb, scale, opacity = _filter_floaters(xyz, rgb, scale, opacity)
    return xyz, rgb, scale, opacity


def _align_upright(xyz, rgb):
    """Rotate point cloud so the longest extent axis = Z (up), head at +Z."""
    center = xyz.mean(axis=0)
    xyz_c = (xyz - center).astype(np.float32)
    eigenvalues, eigenvectors = np.linalg.eigh(np.cov(xyz_c.T))
    idx = np.argsort(eigenvalues)[::-1]
    eigenvectors = eigenvectors[:, idx].astype(np.float32)
    xyz_pca = xyz_c @ eigenvectors
    # largest variance -> Z (up), 2nd -> X (width), 3rd -> Y (depth)
    xyz_out = np.column_stack([xyz_pca[:, 1], xyz_pca[:, 2], xyz_pca[:, 0]]).astype(np.float32)
    # Ensure head (brightest) at +Z
    brightness = rgb.mean(axis=1)
    top = xyz_out[:, 2] > xyz_out[:, 2].mean()
    if brightness[top].mean() < brightness[~top].mean():
        xyz_out[:, 2] = -xyz_out[:, 2]
    xyz_out -= xyz_out.mean(axis=0)
    return xyz_out


def _filter_floaters(xyz, rgb, scale, opacity,
                     dist_percentile=99.0, min_opacity=0.03):
    """Remove isolated low-opacity gaussians (floaters) that cause sparse noise."""
    center = xyz.mean(axis=0)
    dists = np.linalg.norm(xyz - center, axis=1)
    dist_thresh = np.percentile(dists, dist_percentile)
    keep = (dists <= dist_thresh) & (opacity >= min_opacity)
    return xyz[keep], rgb[keep], scale[keep], opacity[keep]


def auto_frame_distance(xyz, fov=35.0):
    """Calculate camera distance for auto-framing based on 99th percentile extent."""
    center = xyz.mean(axis=0)
    dists = np.linalg.norm(xyz - center, axis=1)
    extent = np.percentile(dists, 99)
    return float(extent / (math.tan(math.radians(fov) / 2) * 0.9)), center


def composite_bg(character_img, bg_path, width, height,
                 char_height_ratio=0.55, ground_ratio=0.88):
    """Composite character render onto background: crop to bbox, scale, ground at bottom.

    char_height_ratio: character height as fraction of output height (default 0.55)
    ground_ratio: vertical position of character feet (0=top, 1=bottom, default 0.88)
    """
    char = np.array(character_img, dtype=np.float32)
    mask = char.sum(axis=2) > 30

    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if len(rows) == 0:
        return Image.open(bg_path).convert('RGB').resize((width, height), Image.LANCZOS)

    # Crop to character bounding box
    r0, r1 = rows[0], rows[-1] + 1
    c0, c1 = cols[0], cols[-1] + 1
    char_crop = char[r0:r1, c0:c1]
    mask_crop = mask[r0:r1, c0:c1]

    # Scale character to target height
    target_h = int(height * char_height_ratio)
    src_h, src_w = char_crop.shape[:2]
    scale_factor = target_h / src_h
    target_w = max(1, int(src_w * scale_factor))

    char_pil = Image.fromarray(char_crop.astype(np.uint8)).resize((target_w, target_h), Image.LANCZOS)
    mask_pil = Image.fromarray((mask_crop.astype(np.uint8) * 255)).resize((target_w, target_h), Image.LANCZOS)

    # Position: bottom-center, feet at ground_ratio
    paste_x = (width - target_w) // 2
    paste_y = int(height * ground_ratio) - target_h

    bg = Image.open(bg_path).convert('RGB').resize((width, height), Image.LANCZOS)
    result = bg.copy()
    result.paste(char_pil, (paste_x, paste_y), mask_pil)
    return result


def render_splat_at_angle(ply_path, yaw, pitch, output_path, bg_image=None,
                          width=832, height=480, fov=35.0,
                          min_px=5, max_px=20, gain=3.0,
                          supersample=2,
                          char_height_ratio=0.55, ground_ratio=0.88):
    """Render a .ply splat at a specific yaw/pitch, optionally composite over bg.

    When bg_image is provided, the character is cropped from the splat render,
    scaled to char_height_ratio of the output height, and grounded at ground_ratio.
    """
    xyz, rgb, scale, opacity = load_ply(ply_path)
    dist, _ = auto_frame_distance(xyz, fov)
    sq = max(width, height, 768)
    ssq = int(sq * supersample)
    img = render_splat(xyz, rgb, scale, opacity,
                       yaw=yaw, pitch=pitch, size=ssq,
                       min_px=min_px * supersample,
                       max_px=max_px * supersample,
                       gain=gain,
                       fov=fov, dist=dist)
    if supersample > 1:
        img = img.resize((sq, sq), Image.LANCZOS)
    if bg_image:
        img = composite_bg(img, bg_image, width, height,
                           char_height_ratio, ground_ratio)
    elif img.size != (width, height):
        img = img.resize((width, height), Image.LANCZOS)
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    img.save(output_path)
    return output_path


def render_splat_angles(ply_path, angles=8, output_dir='.', size=1024,
                        bg_image=None, pitch=15.0, fov=35.0,
                        min_px=5, max_px=20, gain=3.0,
                        supersample=2,
                        filename_prefix='render'):
    """Render a .ply splat from multiple yaw angles, save PNGs. Returns list of paths."""
    os.makedirs(output_dir, exist_ok=True)
    xyz, rgb, scale, opacity = load_ply(ply_path)
    dist, _ = auto_frame_distance(xyz, fov)

    paths = []
    for i in range(angles):
        yaw = 360.0 * i / angles
        ssq = int(size * supersample)
        img = render_splat(xyz, rgb, scale, opacity,
                           yaw=yaw, pitch=pitch, size=ssq,
                           min_px=min_px * supersample,
                           max_px=max_px * supersample,
                           gain=gain,
                           fov=fov, dist=dist)
        if supersample > 1:
            img = img.resize((size, size), Image.LANCZOS)
        if bg_image:
            img = composite_bg(img, bg_image, size, size)
        path = os.path.join(output_dir, f'{filename_prefix}_{i:03d}.png')
        img.save(path)
        paths.append(path)
    return paths


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='3DGS multi-angle renderer')
    parser.add_argument('ply', help='Path to .ply splat file')
    parser.add_argument('-o', '--output', default='./renders', help='Output directory')
    parser.add_argument('-n', '--angles', type=int, default=8, help='Number of angles')
    parser.add_argument('--size', type=int, default=1024, help='Image size')
    parser.add_argument('--bg', help='Background image path (optional)')
    parser.add_argument('--pitch', type=float, default=15.0, help='Camera pitch')
    args = parser.parse_args()

    paths = render_splat_angles(args.ply, args.angles, args.output,
                                args.size, args.bg, args.pitch)
    print(f'Rendered {len(paths)} images to {args.output}')
    for p in paths:
        print(f'  {p}')
