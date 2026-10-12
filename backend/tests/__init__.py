"""测试包共享夹具。"""

from __future__ import annotations

from pathlib import Path


def make_scene_dir(root, name, rc=False, source=True):
    """一个真机上那样的场景目录：meta.xml + 输入影像 + 掩码（+ 未超分那一份）。

    `rc=True` 用 RC 布局（输入叫 `PAN.tif`），默认 SC（输入 = `<目录名>.tif`）。
    `source=False` 用来造出"没有未超分那一份"的场景 —— 提交侧要拦下的那种。
    提交侧会去 stat 这个目录（services/run_sr.nosr_swap_plan），所以走到服务层的
    用例必须有一个真的。
    """
    scene = Path(root) / name
    scene.mkdir(parents=True, exist_ok=True)
    azimuth = "" if rc else "123.4"
    (scene / f"{name}_meta.xml").write_text(
        f"<meta><SolarAzimuth>{azimuth}</SolarAzimuth></meta>", encoding="utf-8")
    stem = "PAN" if rc else name
    (scene / f"{stem}.tif").write_bytes(b"body\n")
    (scene / f"{stem}_mask.tif").write_bytes(b"mask\n")
    if source:
        (scene / f"{stem}_NOSR.tif").write_bytes(b"un-superresolved\n")
    return scene


def allowed_roots_env(*roots) -> dict:
    """把给定目录配成盘阵前缀白名单，返回要注入的环境变量。

    开发机是 Windows：临时目录带盘符（`C:\\Users\\...`），而盘阵路径一律经
    `pathguard.to_posix_array_path` 归一 —— 不补一条"该盘符映射到自身"的规则，
    本机绝对路径会因「未知盘符」被拒。Linux 上 `Path.drive` 为空，这条例外
    自然不生效。

    返回 dict 而不是直接改 `os.environ`：调用方可能是 `mock.patch.dict`，
    那种用法下事后改的键不会被还原。
    """
    env = {"SR_ALLOWED_ROOTS": ";".join(str(r) for r in roots)}
    drives = {Path(r).drive for r in roots}
    drives.discard("")
    if drives:
        env["SR_DRIVE_MAP"] = ";".join(f"{d}={d}" for d in sorted(drives))
    return env
