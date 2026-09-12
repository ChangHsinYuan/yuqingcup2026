#!/usr/bin/env python3
"""Headless mesh (.glb/.obj/.ply) 渲染器 — pyrender + EGL 后端

用法:
  # 单视角渲染
  from utils.mesh_render import render_mesh_at_angle
  path = render_mesh_at_angle('character.glb', yaw=45, pitch=10,
                              output_path='renders/char_045.png',
                              bg_image='background.jpg')

  # 批量多角度渲染
  from utils.mesh_render import render_mesh_angles
  paths = render_mesh_angles('character.glb', angles=8, output_dir='renders/')

  # camera dict 高级接口
  from utils.mesh_render import render_mesh_view
  path = render_mesh_view('character.glb',
                          camera={'yaw': 30, 'pitch': 5, 'fov': 35},
                          output_path='view.png')
"""
import os
os.environ['PYOPENGL_PLATFORM'] = 'egl'

import math
import numpy as np
import trimesh
import pyrender
from PIL import Image


def load_mesh(glb_path, normalize=True, gray_fallback=False, texture_image=None):
    """Load mesh file (GLB/GLTF/OBJ/PLY), optionally center+scale to unit bbox.

    Args:
        glb_path: mesh 文件路径
        normalize: 居中并缩放到单位 bounding box（max dim = 1.0）
        gray_fallback: 覆盖材质为灰色（预览用，丢弃 PBR 材质）
        texture_image: 参考图路径，投影到 mesh 顶点作为颜色（解决 Hunyuan3Dv2 无贴图问题）
    Returns:
        trimesh.Trimesh
    """
    mesh = trimesh.load(glb_path, force='mesh')
    if normalize:
        mesh.apply_translation(-mesh.bounding_box.centroid)
        scale = 1.0 / mesh.bounding_box.extents.max()
        mesh.apply_scale(scale)
    if texture_image:
        _project_texture(mesh, texture_image)
    elif gray_fallback:
        mesh.visual = trimesh.visual.ColorVisuals(
            mesh, vertex_colors=np.full((len(mesh.vertices), 4), [200, 200, 200, 255], dtype=np.uint8)
        )
    return mesh


def _project_texture(mesh, image_path):
    """将参考图平面投影到 mesh 顶点作为顶点颜色。

    使用 XY 平面投影（正面相机视角）：mesh 的 XY 坐标映射到图像 UV。
    正面和背面顶点获得相同颜色（适合 Hunyuan3Dv2 的扁平 mesh）。
    参考图中的纯白背景会被裁剪掉，只保留角色区域。
    """
    img = Image.open(image_path).convert('RGB')
    img_arr = np.array(img)
    img_h, img_w = img_arr.shape[:2]

    # 裁剪白色背景，只保留角色区域
    non_white = img_arr.sum(axis=2) < 700
    rows = np.where(non_white.any(axis=1))[0]
    cols = np.where(non_white.any(axis=0))[0]
    if len(rows) > 0 and len(cols) > 0:
        pad = 10
        r0, r1 = max(0, rows[0] - pad), min(img_h, rows[-1] + pad + 1)
        c0, c1 = max(0, cols[0] - pad), min(img_w, cols[-1] + pad + 1)
        img_arr = img_arr[r0:r1, c0:c1]
        img_h, img_w = img_arr.shape[:2]

    verts = mesh.vertices
    bbox = mesh.bounding_box
    x_min, x_max = bbox.bounds[0][0], bbox.bounds[1][0]
    y_min, y_max = bbox.bounds[0][1], bbox.bounds[1][1]

    u = (verts[:, 0] - x_min) / max(x_max - x_min, 1e-6)
    v = 1.0 - (verts[:, 1] - y_min) / max(y_max - y_min, 1e-6)
    u = np.clip(u, 0, 0.999)
    v = np.clip(v, 0, 0.999)

    px = (u * (img_w - 1)).astype(int)
    py = (v * (img_h - 1)).astype(int)
    colors = img_arr[py, px]

    vertex_colors = np.column_stack([colors, np.full(len(verts), 255, dtype=np.uint8)])
    mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=vertex_colors)


def auto_frame_distance(mesh, fov=35.0, margin=1.8):
    """Compute camera distance for auto-framing based on mesh bbox and fov."""
    extent = mesh.bounding_box.extents.max()
    return float(extent / (2 * math.tan(math.radians(fov) / 2)) * margin)


def _look_at_pose(eye, target, up=(0, 1, 0)):
    """Compute camera-to-world 4x4 pose matrix (OpenGL: camera looks down -Z)."""
    eye = np.array(eye, dtype=np.float64)
    target = np.array(target, dtype=np.float64)
    up = np.array(up, dtype=np.float64)

    forward = target - eye
    forward /= np.linalg.norm(forward)

    right = np.cross(forward, up)
    right /= np.linalg.norm(right)

    up_orth = np.cross(right, forward)

    return np.array([
        [right[0],   up_orth[0],  -forward[0],  eye[0]],
        [right[1],   up_orth[1],  -forward[1],  eye[1]],
        [right[2],   up_orth[2],  -forward[2],  eye[2]],
        [0,          0,           0,            1]
    ], dtype=np.float32)


def compute_camera_pose(yaw=0.0, pitch=0.0, distance=2.0, look_at=(0, 0, 0)):
    """Compute camera pose for orbiting camera around look_at point.

    Args:
        yaw: horizontal angle in degrees (0=front, 90=left, 180=back, 270=right)
        pitch: vertical angle in degrees (positive=looking down at target)
        distance: camera distance from look_at
        look_at: camera target point
    Returns:
        4x4 camera-to-world pose matrix
    """
    yaw_rad = math.radians(yaw)
    pitch_rad = math.radians(pitch)

    eye = np.array([
        distance * math.cos(pitch_rad) * math.sin(yaw_rad),
        distance * math.sin(pitch_rad),
        distance * math.cos(pitch_rad) * math.cos(yaw_rad),
    ], dtype=np.float64)

    eye += np.array(look_at, dtype=np.float64)

    return _look_at_pose(eye, look_at)


def _setup_lighting(scene, cam_pose,
                    key_intensity=3.0, fill_intensity=1.0, ambient=0.3):
    """Add 3-point lighting: key at camera, fill from opposite, ambient."""
    key = pyrender.DirectionalLight(color=[1.0, 1.0, 1.0], intensity=key_intensity)
    scene.add(key, pose=cam_pose)

    fill_pose = cam_pose.copy()
    fill_pose[0, 3] *= -1
    fill_pose[2, 3] *= -1
    fill = pyrender.DirectionalLight(color=[0.5, 0.5, 0.6], intensity=fill_intensity)
    scene.add(fill, pose=fill_pose)


def _render_mesh(mesh, camera_pose, fov, width, height,
                 key_intensity=3.0, fill_intensity=1.0, ambient=0.3):
    """Render mesh from given camera pose. Returns (color_rgb, depth, alpha)."""
    scene = pyrender.Scene(bg_color=[0, 0, 0, 0], ambient_light=[ambient, ambient, ambient])

    pyrender_mesh = pyrender.Mesh.from_trimesh(mesh, smooth=False)
    scene.add(pyrender_mesh)

    cam = pyrender.PerspectiveCamera(yfov=math.radians(fov),
                                     aspectRatio=width / height)
    scene.add(cam, pose=camera_pose)

    _setup_lighting(scene, camera_pose, key_intensity, fill_intensity, ambient)

    renderer = pyrender.OffscreenRenderer(width, height)
    color, depth = renderer.render(scene)
    renderer.delete()

    alpha = (depth > 0).astype(np.uint8) * 255
    return color, depth, alpha


def composite_bg(char_color, char_alpha, bg_path, width, height,
                 char_height_ratio=0.55, ground_ratio=0.88):
    """Composite character render onto background using alpha mask.

    Args:
        char_color: (H, W, 3) uint8 array from render
        char_alpha: (H, W) uint8 alpha mask from depth buffer
        bg_path: background image file path
        width, height: output resolution
        char_height_ratio: character height as fraction of output height
        ground_ratio: vertical position of character feet (0=top, 1=bottom)
    Returns:
        PIL.Image RGB
    """
    mask_arr = char_alpha
    rows = np.where(mask_arr.any(axis=1))[0]
    cols = np.where(mask_arr.any(axis=0))[0]

    if len(rows) == 0:
        return Image.open(bg_path).convert('RGB').resize((width, height), Image.LANCZOS)

    r0, r1 = rows[0], rows[-1] + 1
    c0, c1 = cols[0], cols[-1] + 1

    char_crop = Image.fromarray(char_color[r0:r1, c0:c1])
    mask_crop = Image.fromarray(char_alpha[r0:r1, c0:c1])

    target_h = int(height * char_height_ratio)
    src_h, src_w = char_crop.size[1], char_crop.size[0]
    scale_factor = target_h / src_h
    target_w = max(1, int(src_w * scale_factor))

    char_resized = char_crop.resize((target_w, target_h), Image.LANCZOS)
    mask_resized = mask_crop.resize((target_w, target_h), Image.LANCZOS)

    paste_x = (width - target_w) // 2
    paste_y = int(height * ground_ratio) - target_h

    bg = Image.open(bg_path).convert('RGB').resize((width, height), Image.LANCZOS)
    bg.paste(char_resized, (paste_x, paste_y), mask_resized)
    return bg


def render_mesh_view(mesh_path, camera, output_path,
                     width=1280, height=704, bg_image=None,
                     normalize=True, gray_fallback=False, texture_image=None,
                     key_intensity=3.0, fill_intensity=1.0,
                     char_height_ratio=0.55, ground_ratio=0.88):
    """渲染 mesh 指定视角 → IMAGE，可选合成背景.

    Args:
        mesh_path: GLB/GLTF/OBJ/PLY 文件路径
        camera: dict, 可选键:
            yaw (float, °): 水平角度, 0=正面
            pitch (float, °): 俯仰角, 正=俯视
            fov (float, °): 视场角, 默认 35
            distance (float): 相机距离, 省略则自动计算
            look_at (tuple): 目标点, 默认原点
            position (tuple): 显式相机位置, 覆盖 yaw/pitch/distance
        output_path: 输出 PNG 路径
        width, height: 输出分辨率
        bg_image: 背景图路径, 传入则合成
        normalize: 居中缩放到单位 bbox
        gray_fallback: 灰色材质覆盖（预览用）
        texture_image: 参考图路径，投影到 mesh 顶点作为颜色
    Returns:
        output_path (str)
    """
    mesh = load_mesh(mesh_path, normalize=normalize,
                     gray_fallback=gray_fallback, texture_image=texture_image)

    fov = camera.get('fov', 35.0)
    look_at = camera.get('look_at', (0, 0, 0))

    if 'position' in camera:
        cam_pose = _look_at_pose(camera['position'], look_at)
    else:
        yaw = camera.get('yaw', 0.0)
        pitch = camera.get('pitch', 0.0)
        distance = camera.get('distance', auto_frame_distance(mesh, fov))
        cam_pose = compute_camera_pose(yaw, pitch, distance, look_at)

    color, depth, alpha = _render_mesh(mesh, cam_pose, fov, width, height,
                                       key_intensity=key_intensity,
                                       fill_intensity=fill_intensity)

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    if bg_image:
        result = composite_bg(color, alpha, bg_image, width, height,
                              char_height_ratio, ground_ratio)
        result.save(output_path)
    else:
        rgba = np.dstack([color, alpha])
        Image.fromarray(rgba, 'RGBA').save(output_path)

    return output_path


def render_mesh_at_angle(glb_path, yaw, pitch, output_path, bg_image=None,
                         width=832, height=480, fov=35.0,
                         normalize=True, gray_fallback=False, texture_image=None,
                         key_intensity=3.0, fill_intensity=1.0,
                         char_height_ratio=0.55, ground_ratio=0.88):
    """简化接口：按 yaw/pitch 角度渲染 mesh → PNG.

    与 splat_renderer.render_splat_at_angle API 对齐，便于 pipeline 切换.
    """
    return render_mesh_view(
        glb_path,
        {'yaw': yaw, 'pitch': pitch, 'fov': fov},
        output_path, width, height, bg_image,
        normalize, gray_fallback, texture_image,
        key_intensity, fill_intensity,
        char_height_ratio, ground_ratio
    )


def render_mesh_angles(glb_path, angles=8, output_dir='.', size=1024,
                       bg_image=None, pitch=15.0, fov=35.0,
                       normalize=True, gray_fallback=False, texture_image=None,
                       filename_prefix='render', **kwargs):
    """批量多角度渲染 mesh → PNG 列表.

    与 splat_renderer.render_splat_angles API 对齐.
    """
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for i in range(angles):
        yaw = 360.0 * i / angles
        path = os.path.join(output_dir, f'{filename_prefix}_{i:03d}.png')
        render_mesh_at_angle(glb_path, yaw, pitch, path, bg_image,
                             size, size, fov, normalize, gray_fallback,
                             texture_image=texture_image, **kwargs)
        paths.append(path)
    return paths


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Headless mesh renderer (pyrender+EGL)')
    parser.add_argument('mesh', help='Path to mesh file (GLB/GLTF/OBJ/PLY)')
    parser.add_argument('-o', '--output', default='./renders', help='Output directory')
    parser.add_argument('-n', '--angles', type=int, default=8, help='Number of angles')
    parser.add_argument('--size', type=int, default=1024, help='Image size')
    parser.add_argument('--bg', help='Background image path (optional)')
    parser.add_argument('--pitch', type=float, default=15.0, help='Camera pitch (degrees)')
    parser.add_argument('--fov', type=float, default=35.0, help='Camera FOV (degrees)')
    parser.add_argument('--gray', action='store_true', help='Use gray material (discard PBR)')
    args = parser.parse_args()

    paths = render_mesh_angles(args.mesh, args.angles, args.output,
                               args.size, args.bg, args.pitch, args.fov,
                               gray_fallback=args.gray)
    print(f'Rendered {len(paths)} images to {args.output}')
    for p in paths:
        print(f'  {p}')
