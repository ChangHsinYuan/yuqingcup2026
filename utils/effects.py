#!/usr/bin/env python3
"""v5 剪辑特效库 — 镜头内特效 + xfade 转场（纯 ffmpeg，CPU）

镜头内特效（输入单个视频片段 → 输出加特效片段，尺寸/时长/yuv420p 收敛）：
  flash         闪白/闪黑（闪回起点脉冲）
  punch_in      快速推进强调（放大强调，瞬推）
  glitch        数字故障脉冲（色偏移+抖动，短促）
  grain_vignette 胶片颗粒 + 暗角（电影感）
  speed_ramp    变速（加快/放慢某段）
  freeze_zoom   定格放大（停帧+推进强调）
  wipe          甩镜（快速平移扫过）
  zoom          整体推拉镜头

镜头间转场：
  transition(a, b, kind)  用 xfade 内置转场拼接两 clip

用法:
  python utils/effects.py apply in.mp4 --effect punch_in -o out.mp4
  python utils/effects.py apply in.mp4 --effect flash --kind white -o out.mp4
  python utils/effects.py transition a.mp4 b.mp4 --kind slide -o out.mp4
"""
import os
import subprocess
import sys
import tempfile
import shutil

FFMPEG = 'ffmpeg'
FFPROBE = 'ffprobe'

# 安全转场种类（xfade 官方支持）
XFADE_KINDS = [
    'fade', 'fadeblack', 'fadewhite', 'slideleft', 'slideright', 'slideup',
    'slidedown', 'smoothleft', 'smoothright', 'wipeleft', 'wiperight',
    'circleopen', 'circleclose', 'radial', 'pixelize', 'hblur', 'vblur',
    'dissolve', 'fadegrays', 'zoomin', 'hlslice', 'hrslice', 'distance',
    'diagtl', 'diagtr', 'diagbl', 'diagbr', 'smoothup', 'smoothdown',
    'wipetl', 'wipetr', 'wipebl', 'wipebr', 'rectopen', 'rectclose',
]


def _run(cmd, timeout=300):
    subprocess.run(cmd, capture_output=True, check=True, timeout=timeout)


def _ffprobe_duration(path):
    r = subprocess.run(
        [FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=15)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _ffprobe_size(path):
    r = subprocess.run(
        [FFPROBE, '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height', '-of', 'csv=p=0', path],
        capture_output=True, text=True, timeout=15)
    try:
        w, h = r.stdout.strip().split(',')
        return int(w), int(h)
    except ValueError:
        return 832, 480


def _encode(vf, in_path, out_path, timeout=300):
    """带 vf 滤镜链重编码，统一 yuv420p。返回 True 成功。"""
    try:
        cmd = [FFMPEG, '-y', '-i', in_path, '-vf', vf,
               '-c:a', 'copy', '-c:v', 'libx264', '-preset', 'medium',
               '-crf', '18', '-pix_fmt', 'yuv420p', out_path]
        subprocess.run(cmd, capture_output=True, timeout=timeout)
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 500
    except Exception:
        return False


# ══ 镜头内特效 ══

def apply_effect(in_path, out_path, effect='none', **kw):
    """对单个视频片段施加特效，输出 out_path（yuv420p）。"""
    if effect in (None, 'none'):
        shutil.copy(in_path, out_path)
        return out_path
    if effect not in EFFECTS:
        raise ValueError(f'未知特效: {effect}，可选 {list(EFFECTS)}')

    dur = kw.get('duration', _ffprobe_duration(in_path))
    w, h = _ffprobe_size(in_path)
    kind = kw.get('kind', 'white')

    if effect == 'flash':
        # 闪白/闪黑：片头 t 秒从纯色淡入到画面（flashback 起点脉冲）
        t = kw.get('flash_t', 0.12)
        color = 'white' if kind == 'white' else 'black'
        vf = f"fade=t=in:st=0:d={t}:color={color}"
        return _encode(vf, in_path, out_path)

    if effect == 'punch_in':
        # 快速推进强调（短促放大）
        strength = kw.get('strength', 1.25)
        vf = (f"crop=iw/{strength}:ih/{strength}:x='iw/{strength}*(1/2)':y='ih/{strength}*(1/2)',"
              f'scale={w}:{h}')
        return _encode(vf, in_path, out_path)

    if effect == 'glitch':
        # 数字故障：噪点脉冲 + 轻微色相抖动（兼容无 rgbashift 的 ffmpeg）
        seed = kw.get('seed', 0) % 2
        vf = (f"noise=alls=12:allf=t,"
              f"hue=h='if(lt(mod(t,0.4),0.08),6,0)'*{'1' if seed else '1'}:s=1.05")
        return _encode(vf, in_path, out_path)

    if effect == 'grain_vignette':
        # 胶片颗粒 + 暗角
        grain = kw.get('grain', 0.02)
        vig = kw.get('vig', 0.35)
        vf = f"noise=alls={int(grain*100)}:allf=t,vignette=PI/4.5"
        return _encode(vf, in_path, out_path)

    if effect == 'speed_ramp':
        # 变速：前 cut 段 1x，后段 factor 倍
        factor = kw.get('factor', 1.5)
        cut = kw.get('cut', 0.7)
        vf = (f"split[a][b];"
              f"[a]setpts=PTS/1,trim=duration={dur*cut:.3f}[a1];"
              f"[b]setpts=PTS/{factor},trim=duration={dur*(1-cut)*factor:.3f}[b1];"
              f"[a1][b1]concat=n=2:v=1:a=0,setpts=PTS-STARTPTS,scale={w}:{h}")
        return _encode(vf, in_path, out_path)

    if effect == 'freeze_zoom':
        # 定格放大：末 hold 秒停帧 + 放大
        hold = kw.get('hold', 0.5)
        strength = kw.get('strength', 1.3)
        vf = (f"zoompan=z='min(1.0+{strength-1:.2f}*on/{int(dur*24)},"
              f"{strength:.2f})':d=1:s={w}x{h}:fps=24,"
              f"loop=loop={int(hold*24)}:size={int(hold*24)}:start=0")
        return _encode(vf, in_path, out_path)

    if effect == 'wipe':
        # 甩镜：快速水平平移扫过（放大 1.3x 后按时间滑动窗口）
        sweep = kw.get('sweep', 0.3)
        sw = int(w * 1.3)
        vf = (f"scale={sw}:{int(h*1.3)},"
              f"crop={w}:{h}:x='min((iw-ow)*t/{sweep},(iw-ow))':y='(ih-oh)/2',scale={w}:{h}")
        return _encode(vf, in_path, out_path)

    if effect == 'zoom':
        # 整体推拉：zoom-in 默认
        direction = kw.get('direction', 'in')
        if direction == 'out':
            z = 'max(1.3-0.03*on,1.0)'
        else:
            z = 'min(1.0+0.03*on,1.3)'
        vf = (f"scale={w}:{h},zoompan=z='{z}':d=1:s={w}x{h}:fps=24")
        return _encode(vf, in_path, out_path)


def _duration_filter(in_path, dur):
    """返回保证片段不短于 dur 的 pre 滤镜（缺失音频时用 tpad）。"""
    return f"tpad=stop_mode=clone:stop_duration={max(0, dur-0.1)}"


def _duration_filter_clip(in_path, out_path, dur):
    """兜底：确保片段至少 dur 秒（tpad 克隆末帧）。"""
    ow, oh = _ffprobe_size(in_path)
    vf = (f"tpad=stop_mode=clone:stop_duration={max(0, dur-0.1)},"
          f"format=yuv420p")
    return _encode(vf, in_path, out_path) and out_path


# ══ xfade 镜头间转场 ══

def transition(a, b, out_path, kind='fade', duration=0.4, **kw):
    """用 xfade 拼接两视频 a,b → out_path（时长 = dur_a + dur_b - duration）。"""
    if kind not in XFADE_KINDS:
        kind = 'fade'
    da, db = _ffprobe_duration(a), _ffprobe_duration(b)
    offset = max(0.0, da - duration)
    vf = (f"[0:v][1:v]xfade=transition={kind}:duration={duration}:offset={offset}"
          f",format=yuv420p[v]")
    cmd = [FFMPEG, '-y', '-i', a, '-i', b,
           '-filter_complex', vf, '-map', '[v]',
           '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
           '-pix_fmt', 'yuv420p', '-an', out_path]
    try:
        subprocess.run(cmd, capture_output=True, timeout=300)
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 500
    except Exception:
        return False


def concat_with_transitions(clips, kinds, out_path, duration=0.4):
    """单条 filter_complex 链式 xfade 拼接 N 个片段。

    Args:
        clips: 片段路径列表（长度 N）
        kinds: 转场种类列表（长度 N-1）
        out_path: 输出（纯视频，无音轨）
    """
    n = len(clips)
    if n < 2:
        shutil.copy(clips[0], out_path)
        return True
    inputs = []
    for c in clips:
        inputs += ['-i', c]
    # 逐对累积 xfade（用 -shortest 处理不同长度）
    parts, chain = [], []
    for i in range(n):
        parts.append(f'[{i}:v]')
    cur = '0:v'
    offsets = []
    running = _ffprobe_duration(clips[0])
    for i in range(n - 1):
        kind = kinds[i] if kinds and i < len(kinds) and kinds[i] in XFADE_KINDS else 'fade'
        off = max(0.0, running - duration)
        offsets.append(off)
        if i < n - 2:
            chain.append(f"[{cur}][{i+1}:v]xfade=transition={kind}:duration={duration}:offset={off:.3f}[v{i}]")
            cur = f'v{i}'
        else:
            chain.append(f"[{cur}][{i+1}:v]xfade=transition={kind}:duration={duration}:offset={off:.3f}[vout]")
        running = running + _ffprobe_duration(clips[i+1]) - duration
    chain.append('[vout]format=yuv420p[v]')
    cmd = ([FFMPEG, '-y'] + inputs +
           ['-filter_complex', ';'.join(chain), '-map', '[v]',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
            '-pix_fmt', 'yuv420p', '-an', out_path])
    try:
        subprocess.run(cmd, capture_output=True, timeout=600)
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 500
    except Exception:
        return False



EFFECTS = {
    'flash', 'punch_in', 'glitch', 'grain_vignette', 'speed_ramp',
    'freeze_zoom', 'wipe', 'zoom',
}


def apply_effects_to_clips(clips, out_dir, effects):
    """批量：对每个 clip 施加对应特效。effects: list[str] 长度=len(clips)。"""
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, (clip, eff) in enumerate(zip(clips, effects), 1):
        if eff in (None, 'none'):
            out.append(clip)
            continue
        op = os.path.join(out_dir, f'fx_{i}.mp4')
        print(f'  [fx] clip{i} → {eff}')
        try:
            apply_effect(clip, op, eff)
            out.append(op if os.path.exists(op) else clip)
        except Exception as e:
            print(f'  ⚠ clip{i} 特效失败({e})，用原片')
            out.append(clip)
    return out


def main():
    import argparse
    parser = argparse.ArgumentParser(prog='effects', description='v5 剪辑特效库')
    sub = parser.add_subparsers(dest='cmd', required=True)

    pa = sub.add_parser('apply', help='单片段特效')
    pa.add_argument('video')
    pa.add_argument('--effect', choices=sorted(EFFECTS) + ['none'], default='none')
    pa.add_argument('--kind', default='white', help='flash: white/black')
    pa.add_argument('-o', '--output', default=None)
    pa.set_defaults(cmd='apply')

    pt = sub.add_parser('transition', help='xfade 拼接两段')
    pt.add_argument('a')
    pt.add_argument('b')
    pt.add_argument('-o', '--output', required=True)
    pt.add_argument('--kind', default='fade', choices=XFADE_KINDS)
    pt.add_argument('--duration', type=float, default=0.4)
    pt.set_defaults(cmd='transition')

    pl = sub.add_parser('list', help='列出特效')
    pl.set_defaults(cmd='list')

    args = parser.parse_args()

    if args.cmd == 'list':
        print('镜头内特效:', ' '.join(sorted(EFFECTS)))
        print('xfade 转场数:', len(XFADE_KINDS))
        return
    if args.cmd == 'apply':
        out = args.output or args.video.replace('.mp4', f'_{args.effect}.mp4')
        apply_effect(args.video, out, args.effect, kind=args.kind)
        print(f'→ {out}')
    elif args.cmd == 'transition':
        ok = transition(args.a, args.b, args.output, kind=args.kind,
                        duration=args.duration)
        print(f'→ {args.output} ({ok})')


if __name__ == '__main__':
    main()
