#!/usr/bin/env python3
"""资产库管理 — 角色/场景 3D 资产注册、检索、复用

用法:
  from utils.asset_registry import AssetRegistry
  reg = AssetRegistry()

  # 注册角色 mesh
  reg.register_character(
      id='red_cloak_boy', desc='红斗篷短发少年',
      path='mesh', glb_path='assets/mesh/red_cloak_boy.glb',
      quality_score=8,
  )

  # 检索可复用角色
  match = reg.find_character('红斗篷少年')
  if match:
      print(f'复用: {match["id"]} → {match["glb"]}')
"""
import json
import os
import time
import shutil
from difflib import SequenceMatcher

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')


def _load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


class AssetRegistry:
    """资产库管理器（registry.json + 文件存储）。"""

    def __init__(self, config=None, base_dir=None):
        if config is None:
            config = _load_config()
        output_dir = config.get('output_dir', '/mnt/dataset/zxy/vidance/output')
        self.base_dir = base_dir or os.path.join(output_dir, 'assets')
        self.mesh_dir = os.path.join(self.base_dir, 'mesh')
        self.registry_path = os.path.join(self.base_dir, 'registry.json')
        os.makedirs(self.mesh_dir, exist_ok=True)
        self._registry = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.registry_path):
            with open(self.registry_path, 'r') as f:
                return json.load(f)
        return {'characters': [], 'scenes': []}

    def _save(self):
        with open(self.registry_path, 'w') as f:
            json.dump(self._registry, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _desc_similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    # ── Character ──

    def register_character(self, id: str, desc: str, path: str,
                           glb_path: str = None, splat_path: str = None,
                           ref_image: str = None,
                           quality_score: int = None) -> dict:
        """注册角色资产。自动复制 mesh/splat/ref 文件到 assets 目录。

        path: 'mesh' | '3dgs'
        glb_path: GLB 文件路径（mesh 路径）
        splat_path: PLY 文件路径（3dgs 路径）
        ref_image: 角色参考图路径
        quality_score: 审查评分（0-10）
        """
        entry = {
            'id': id,
            'desc': desc,
            'path': path,
            'glb': None,
            'splat': None,
            'ref_image': None,
            'created': time.strftime('%Y-%m-%d'),
            'quality_score': quality_score,
        }

        if glb_path and os.path.exists(glb_path):
            dst = os.path.join(self.mesh_dir, f'{id}.glb')
            shutil.copy2(glb_path, dst)
            entry['glb'] = dst

        if splat_path and os.path.exists(splat_path):
            dst = os.path.join(self.mesh_dir, f'{id}.ply')
            shutil.copy2(splat_path, dst)
            entry['splat'] = dst

        if ref_image and os.path.exists(ref_image):
            dst = os.path.join(self.mesh_dir, f'{id}_ref.png')
            shutil.copy2(ref_image, dst)
            entry['ref_image'] = dst

        existing = self._find_by_id(self._registry['characters'], id)
        if existing:
            existing.update(entry)
        else:
            self._registry['characters'].append(entry)
        self._save()
        return entry

    def find_character(self, desc: str, min_similarity: float = 0.5,
                       path_filter: str = None) -> dict | None:
        """按描述模糊检索角色资产。返回最匹配的条目或 None。"""
        best = None
        best_score = 0.0
        for entry in self._registry['characters']:
            if path_filter and entry.get('path') != path_filter:
                continue
            score = self._desc_similarity(desc, entry.get('desc', ''))
            if score > best_score:
                best_score = score
                best = entry
        if best and best_score >= min_similarity:
            return best
        return None

    def get_character(self, id: str) -> dict | None:
        return self._find_by_id(self._registry['characters'], id)

    def list_characters(self) -> list:
        return self._registry['characters']

    # ── Scene ──

    def register_scene(self, id: str, desc: str, glb_path: str,
                       ref_image: str = None) -> dict:
        """注册场景资产。"""
        entry = {
            'id': id,
            'desc': desc,
            'glb': None,
            'ref_image': None,
            'created': time.strftime('%Y-%m-%d'),
        }

        if glb_path and os.path.exists(glb_path):
            dst = os.path.join(self.mesh_dir, f'scene_{id}.glb')
            shutil.copy2(glb_path, dst)
            entry['glb'] = dst

        if ref_image and os.path.exists(ref_image):
            dst = os.path.join(self.mesh_dir, f'scene_{id}_ref.png')
            shutil.copy2(ref_image, dst)
            entry['ref_image'] = dst

        existing = self._find_by_id(self._registry['scenes'], id)
        if existing:
            existing.update(entry)
        else:
            self._registry['scenes'].append(entry)
        self._save()
        return entry

    def find_scene(self, desc: str, min_similarity: float = 0.5) -> dict | None:
        """按描述模糊检索场景资产。"""
        best = None
        best_score = 0.0
        for entry in self._registry['scenes']:
            score = self._desc_similarity(desc, entry.get('desc', ''))
            if score > best_score:
                best_score = score
                best = entry
        if best and best_score >= min_similarity:
            return best
        return None

    def get_scene(self, id: str) -> dict | None:
        return self._find_by_id(self._registry['scenes'], id)

    def list_scenes(self) -> list:
        return self._registry['scenes']

    # ── utils ──

    @staticmethod
    def _find_by_id(items: list, id: str) -> dict | None:
        for item in items:
            if item.get('id') == id:
                return item
        return None

    def stats(self) -> dict:
        return {
            'characters': len(self._registry['characters']),
            'scenes': len(self._registry['scenes']),
            'registry_path': self.registry_path,
        }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Asset registry CLI')
    sub = parser.add_subparsers(dest='cmd')

    p_list = sub.add_parser('list', help='List all assets')
    p_list.add_argument('--type', choices=['characters', 'scenes'], default='characters')

    p_find = sub.add_parser('find', help='Find asset by description')
    p_find.add_argument('desc', help='Description to search')
    p_find.add_argument('--type', choices=['characters', 'scenes'], default='characters')
    p_find.add_argument('--min-sim', type=float, default=0.5)

    p_stats = sub.add_parser('stats', help='Show registry stats')

    args = parser.parse_args()
    reg = AssetRegistry()

    if args.cmd == 'list':
        items = reg._registry.get(args.type, [])
        if not items:
            print(f'(no {args.type})')
        for item in items:
            print(f"  {item['id']}: {item.get('desc', '')} "
                  f"[{item.get('path', '?')}] score={item.get('quality_score', '?')}")
    elif args.cmd == 'find':
        if args.type == 'characters':
            match = reg.find_character(args.desc, min_similarity=args.min_sim)
        else:
            match = reg.find_scene(args.desc, min_similarity=args.min_sim)
        if match:
            print(json.dumps(match, indent=2, ensure_ascii=False))
        else:
            print('(no match)')
    elif args.cmd == 'stats':
        print(json.dumps(reg.stats(), indent=2))
    else:
        parser.print_help()
