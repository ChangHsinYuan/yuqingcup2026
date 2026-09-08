#!/usr/bin/env python3
"""3DGS (.ply) 多角度渲染器 — 使用 TripoSplat preview 算法（min_px 保证可见性）

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
    """Load a PLY gaussian splat file, return (xyz, rgb, scale, opacity) numpy arrays."""
    with open(ply_path, 'rb') as f:
        header = b''
        while True:
            line = f.readline()
            header += line
            if b'end_header' in line:
                break
        # Parse vertex count
        n = 0
        props = 0
        for line in header.split(b'\n'):
            if line.startswith(b'element vertex'):
                n = int(line.split()[-1])
            if line.startswith(b'property'):
                props += 1
        data = f.read(props * 4 * n)
        arr = np.frombuffer(data, dtype=np.float32).reshape(n, props)

    xyz = arr[:, :3]
    rgb = np.clip(0.5 + arr[:, 6:9] * _C0, 0, 1)
    scale = np.exp(arr[:, 10:13]).max(axis=1)
    opacity = 1.0 / (1.0 + np.exp(-arr[:, 9]))
    return xyz, rgb, scale, opacity


def auto_frame_distance(xyz, fov=35.0):
    """Calculate camera distance for auto-framing based on 99th percentile extent."""
    center = xyz.mean(axis=0)
    dists = np.linalg.norm(xyz - center, axis=1)
    extent = np.percentile(dists, 99)
    return float(extent / (math.tan(math.radians(fov) / 2) * 0.9)), center


def composite_bg(character_img, bg_path, width, height):
    """Composite character (on black bg) over a background image."""
    char = np.array(character_img, dtype=np.float32)
    mask = (char.sum(axis=2) > 30).astype(np.float32)
    mask = mask[:, :, None]

    bg = Image.open(bg_path).convert('RGB').resize((width, height), Image.LANCZOS)
    bg_arr = np.array(bg, dtype=np.float32)

    result = char * mask + bg_arr * (1.0 - mask)
    return Image.fromarray(result.astype(np.uint8))


def render_splat_at_angle(ply_path, yaw, pitch, output_path, bg_image=None,
                          width=832, height=480, fov=35.0,
                          min_px=3, max_px=15, gain=2.0):
    """Render a .ply splat at a specific yaw/pitch, optionally composite over bg.
    Returns the output path.

    render_splat only supports square output; we render at max(w,h) then resize."""
    xyz, rgb, scale, opacity = load_ply(ply_path)
    dist, _ = auto_frame_distance(xyz, fov)
    sq = max(width, height)
    img = render_splat(xyz, rgb, scale, opacity,
                       yaw=yaw, pitch=pitch, size=sq,
                       min_px=min_px, max_px=max_px, gain=gain,
                       fov=fov, dist=dist)
    if img.size != (width, height):
        img = img.resize((width, height), Image.LANCZOS)
    if bg_image:
        img = composite_bg(img, bg_image, width, height)
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    img.save(output_path)
    return output_path


def render_splat_angles(ply_path, angles=8, output_dir='.', size=1024,
                        bg_image=None, pitch=15.0, fov=35.0,
                        min_px=3, max_px=15, gain=2.0,
                        filename_prefix='render'):
    """Render a .ply splat from multiple yaw angles, save PNGs. Returns list of paths."""
    os.makedirs(output_dir, exist_ok=True)
    xyz, rgb, scale, opacity = load_ply(ply_path)
    dist, _ = auto_frame_distance(xyz, fov)

    paths = []
    for i in range(angles):
        yaw = 360.0 * i / angles
        img = render_splat(xyz, rgb, scale, opacity,
                           yaw=yaw, pitch=pitch, size=size,
                           min_px=min_px, max_px=max_px, gain=gain,
                           fov=fov, dist=dist)
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
