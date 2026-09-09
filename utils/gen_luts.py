#!/usr/bin/env python3
"""生成开源电影级 3D LUT (.cube) 文件 — 无需下载，可定制

生成 6 种风格 LUT：
  cinematic — 青橙色调（经典电影感）
  warm      — 暖金色调
  cool      — 冷蓝色调
  vintage   — 复古褪色（提亮暗部+降饱和）
  vivid     — 高饱和高对比
  soft      — 柔和 pastel

用法:
  python utils/gen_luts.py [--size 33] [--outdir utils/luts]
"""
import os
import math
import argparse


def _srgb_to_linear(x):
    return x ** 2.2 if x > 0 else 0


def _linear_to_srgb(x):
    return x ** (1 / 2.2) if x > 0 else 0


def _lerp(a, b, t):
    return a + (b - a) * t


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def apply_cinematic(r, g, b):
    """青橙色调：暗部偏青，高光偏橙，轻微降饱和"""
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    # 青橙色调分离
    shadows = max(0, 1 - luma * 2)
    highlights = max(0, luma * 2 - 1)
    r = r + highlights * 0.08 - shadows * 0.03
    g = g + highlights * 0.01 - shadows * 0.05
    b = b - highlights * 0.05 + shadows * 0.06
    # 轻微对比
    contrast = 1.08
    r = (r - 0.5) * contrast + 0.5
    g = (g - 0.5) * contrast + 0.5
    b = (b - 0.5) * contrast + 0.5
    return _clamp(r), _clamp(g), _clamp(b)


def apply_warm(r, g, b):
    """暖金色调：加暖色温，轻微提亮"""
    r = r * 1.06 + 0.01
    g = g * 1.02
    b = b * 0.94 - 0.01
    # 提亮中间调
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    mid = 1 - abs(luma - 0.5) * 2
    r += mid * 0.02
    g += mid * 0.01
    return _clamp(r), _clamp(g), _clamp(b)


def apply_cool(r, g, b):
    """冷蓝色调：加冷色温"""
    r = r * 0.94 - 0.01
    g = g * 0.98
    b = b * 1.06 + 0.01
    # 轻微对比
    contrast = 1.05
    r = (r - 0.5) * contrast + 0.5
    g = (g - 0.5) * contrast + 0.5
    b = (b - 0.5) * contrast + 0.5
    return _clamp(r), _clamp(g), _clamp(b)


def apply_vintage(r, g, b):
    """复古褪色：提亮暗部+降饱和+轻微偏黄"""
    # 提亮暗部（lift）
    lift = 0.06
    r = r + lift * (1 - r)
    g = g + lift * (1 - g)
    b = b + lift * (1 - b)
    # 降饱和
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    sat = 0.75
    r = luma + (r - luma) * sat
    g = luma + (g - luma) * sat
    b = luma + (b - luma) * sat
    # 偏黄
    r = r * 1.03
    g = g * 1.01
    b = b * 0.97
    # 降低对比
    contrast = 0.92
    r = (r - 0.5) * contrast + 0.5
    g = (g - 0.5) * contrast + 0.5
    b = (b - 0.5) * contrast + 0.5
    return _clamp(r), _clamp(g), _clamp(b)


def apply_vivid(r, g, b):
    """高饱和高对比"""
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    sat = 1.35
    r = luma + (r - luma) * sat
    g = luma + (g - luma) * sat
    b = luma + (b - luma) * sat
    contrast = 1.12
    r = (r - 0.5) * contrast + 0.5
    g = (g - 0.5) * contrast + 0.5
    b = (b - 0.5) * contrast + 0.5
    return _clamp(r), _clamp(g), _clamp(b)


def apply_soft(r, g, b):
    """柔和 pastel：降对比+降饱和+轻微提亮"""
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    sat = 0.85
    r = luma + (r - luma) * sat
    g = luma + (g - luma) * sat
    b = luma + (b - luma) * sat
    # 提亮
    r = r * 0.95 + 0.05
    g = g * 0.95 + 0.05
    b = b * 0.95 + 0.05
    # 降对比
    contrast = 0.9
    r = (r - 0.5) * contrast + 0.5
    g = (g - 0.5) * contrast + 0.5
    b = (b - 0.5) * contrast + 0.5
    return _clamp(r), _clamp(g), _clamp(b)


LUT_STYLES = {
    'cinematic': ('Cinematic Teal-Orange', apply_cinematic),
    'warm': ('Warm Golden', apply_warm),
    'cool': ('Cool Blue', apply_cool),
    'vintage': ('Vintage Faded', apply_vintage),
    'vivid': ('Vivid Saturated', apply_vivid),
    'soft': ('Soft Pastel', apply_soft),
}


def generate_cube(style_name, size, filepath):
    """生成单个 .cube LUT 文件"""
    title, func = LUT_STYLES[style_name]
    lines = [f'TITLE "{title}"', f'LUT_3D_SIZE {size}', '']

    for r_idx in range(size):
        for g_idx in range(size):
            for b_idx in range(size):
                r = r_idx / (size - 1)
                g = g_idx / (size - 1)
                b = b_idx / (size - 1)
                r_out, g_out, b_out = func(r, g, b)
                lines.append(f'{r_out:.6f} {g_out:.6f} {b_out:.6f}')

    with open(filepath, 'w') as f:
        f.write('\n'.join(lines) + '\n')

    return os.path.getsize(filepath)


def main():
    parser = argparse.ArgumentParser(description='Generate 3D LUT .cube files')
    parser.add_argument('--size', type=int, default=33, help='LUT size (default 33)')
    parser.add_argument('--outdir', default=os.path.join(os.path.dirname(__file__), 'luts'),
                        help='Output directory')
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    for style in LUT_STYLES:
        filepath = os.path.join(args.outdir, f'{style}.cube')
        size = generate_cube(style, args.size, filepath)
        print(f'  {style}.cube: {size/1024:.0f}KB')

    print(f'\nGenerated {len(LUT_STYLES)} LUTs in {args.outdir}')


if __name__ == '__main__':
    main()
