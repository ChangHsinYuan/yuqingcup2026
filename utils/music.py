#!/usr/bin/env python3
"""生成开源环境配乐 — 纯 numpy 合成，无需下载

生成 5 种风格 BGM：
  calm       — 平静冥想（低频和声+慢呼吸）
  uplifting  — 振奋向上（大调琶音+明亮和声）
  mysterious — 神秘悬疑（小二度+低频drone）
  dramatic   — 戏剧紧张（低频脉冲+不协和音）
  playful    — 活泼俏皮（五声音阶+跳跃旋律）

用法:
  python utils/music.py --mood calm --duration 30 --o /tmp/bgm.wav
"""
import os
import numpy as np
from scipy.io import wavfile
import argparse


SR = 44100


def _adsr(n, attack, decay, sustain, release, sr=SR):
    """ADSR 包络"""
    a = int(attack * sr)
    d = int(decay * sr)
    r = int(release * sr)
    s = n - a - d - r
    if s < 0:
        # Not enough space: shrink release to fit
        r = max(0, n - a - d)
        s = 0
    env = np.zeros(n)
    if a > 0 and a <= n:
        env[:a] = np.linspace(0, 1, a)
    if d > 0 and a + d <= n:
        env[a:a+d] = np.linspace(1, sustain, d)
    if s > 0:
        env[a+d:a+d+s] = sustain
    if r > 0 and a + d + s + r <= n:
        env[a+d+s:a+d+s+r] = np.linspace(sustain, 0, r)
    elif r > 0:
        # Fill remaining with release
        remaining = n - a - d - s
        if remaining > 0:
            env[a+d+s:n] = np.linspace(sustain, 0, remaining)
    return env


def _pad(freqs, duration, sr=SR, amp=0.15):
    """生成和声 pad（多个正弦波叠加+慢呼吸调制）"""
    n = int(duration * sr)
    t = np.arange(n) / sr
    wave = np.zeros(n)
    for f in freqs:
        # 慢呼吸调制
        lfo = 0.7 + 0.3 * np.sin(2 * np.pi * (0.1 + 0.05 * np.random.rand()) * t)
        wave += amp * lfo * np.sin(2 * np.pi * f * t)
        # 加泛音
        wave += amp * 0.3 * lfo * np.sin(2 * np.pi * f * 2 * t)
    # 淡入淡出
    fade = int(min(2.0, duration * 0.3) * sr)
    wave[:fade] *= np.linspace(0, 1, fade)
    wave[-fade:] *= np.linspace(1, 0, fade)
    return wave / max(1, len(freqs))


def _arpeggio(notes, note_dur, sr=SR, amp=0.12):
    """琶音序列"""
    n = int(len(notes) * note_dur * sr)
    wave = np.zeros(n)
    for i, f in enumerate(notes):
        start = int(i * note_dur * sr)
        end = min(start + int(note_dur * sr), n)
        length = end - start
        if length <= 0:
            break
        env = _adsr(length, attack=0.02, decay=0.1, sustain=0.6, release=0.15, sr=sr)
        wave[start:end] += amp * env * np.sin(2 * np.pi * f * np.arange(length) / sr)
    return wave


def gen_calm(duration, sr=SR):
    """平静冥想：低频和声+慢呼吸"""
    wave = _pad([110, 165, 220, 277], duration, amp=0.12)
    # 加低频底噪
    t = np.arange(len(wave)) / sr
    wave += 0.05 * np.sin(2 * np.pi * 55 * t)
    return wave * 0.8


def gen_uplifting(duration, sr=SR):
    """振奋向上：大调琶音+明亮和声"""
    # C 大调琶音 C-E-G-C-E-G
    notes = [261.63, 329.63, 392.0, 523.25, 659.25, 783.99]
    pattern = notes * 2 + notes[::-1] * 2
    note_dur = 0.4
    arp = _arpeggio(pattern * max(1, int(duration / (len(pattern) * note_dur))), note_dur, amp=0.1)
    pad = _pad([261.63, 392.0, 523.25], duration, amp=0.08)
    n = min(len(arp), len(pad))
    result = np.zeros(int(duration * sr))
    result[:n] = arp[:n] + pad[:n]
    return result * 0.7


def gen_mysterious(duration, sr=SR):
    """神秘悬疑：小二度+低频drone"""
    wave = _pad([110, 116.54, 164.81], duration, amp=0.1)
    # 加不规则低频脉冲
    t = np.arange(len(wave)) / sr
    pulse = 0.05 * np.sin(2 * np.pi * 0.5 * t) * np.sin(2 * np.pi * 73.42 * t)
    wave += pulse
    return wave * 0.7


def gen_dramatic(duration, sr=SR):
    """戏剧紧张：低频脉冲+不协和音"""
    wave = _pad([55, 58.27, 82.41], duration, amp=0.15)
    # 低频心跳脉冲
    t = np.arange(len(wave)) / sr
    heartbeat = np.zeros(len(wave))
    for beat_t in np.arange(0, duration, 1.5):
        idx = int(beat_t * sr)
        if idx + int(0.3 * sr) < len(heartbeat):
            pulse_env = np.exp(-np.arange(int(0.3 * sr)) / (0.1 * sr))
            heartbeat[idx:idx+len(pulse_env)] += 0.1 * pulse_env * np.sin(2 * np.pi * 40 * np.arange(len(pulse_env)) / sr)
    wave += heartbeat
    return wave * 0.8


def gen_playful(duration, sr=SR):
    """活泼俏皮：五声音阶跳跃旋律"""
    # C 大调五声音阶 C-D-E-G-A
    scale = [523.25, 587.33, 659.25, 783.99, 880.0]
    np.random.seed(42)
    notes = []
    for _ in range(int(duration / 0.25)):
        notes.append(scale[np.random.randint(0, len(scale))])
    arp = _arpeggio(notes, 0.25, amp=0.1)
    pad = _pad([261.63, 392.0, 523.25], duration, amp=0.06)
    n = min(len(arp), len(pad))
    result = np.zeros(int(duration * sr))
    result[:n] = arp[:n] + pad[:n]
    return result * 0.7


MOOD_FUNCS = {
    'calm': gen_calm,
    'uplifting': gen_uplifting,
    'mysterious': gen_mysterious,
    'dramatic': gen_dramatic,
    'playful': gen_playful,
}


def generate_bgm(mood: str, duration: float, output_path: str, sr: int = SR) -> str:
    """生成指定风格的环境配乐 WAV 文件。

    Args:
        mood: 风格名 (calm/uplifting/mysterious/dramatic/playful)
        duration: 时长（秒）
        output_path: 输出 WAV 路径
        sr: 采样率

    Returns:
        输出文件路径
    """
    func = MOOD_FUNCS.get(mood, gen_calm)
    wave = func(duration, sr=sr)

    # 归一化 + 立体声
    wave = wave / max(1, np.max(np.abs(wave))) * 0.8
    stereo = np.column_stack([wave, wave])
    stereo = (stereo * 32767).astype(np.int16)

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    wavfile.write(output_path, sr, stereo)
    return output_path


def main():
    parser = argparse.ArgumentParser(description='Generate ambient BGM')
    parser.add_argument('--mood', default='calm', choices=list(MOOD_FUNCS.keys()))
    parser.add_argument('--duration', type=float, default=30)
    parser.add_argument('-o', '--output', required=True)
    args = parser.parse_args()

    generate_bgm(args.mood, args.duration, args.output)
    print(f'Generated: {args.output} ({args.duration}s, {args.mood})')


if __name__ == '__main__':
    main()
