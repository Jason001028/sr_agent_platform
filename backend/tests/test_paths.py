"""Tests for backend/pathguard.py — 盘阵路径归一化与前缀白名单。

这些函数是纯词法的（不 stat、不列举），所以用临时目录 + 注入环境变量就能
在开发机上跑，不需要真的 /DiskArray。唯一需要真实文件系统的几条
（`ensure_allowed(kind=...)`）靠 `allow_tmp()` 把临时目录自身挂成白名单根。
"""

import os
import tempfile
import unittest
from pathlib import Path

from backend.pathguard import (
    PathDeniedError, allowed_roots, drive_map, ensure_allowed,
    flat_scene_layout, infer_scene_paths, is_allowed, is_within,
    looks_like_scene_name, parse_scene_date, production_tree_depth,
    scene_name_layers, strip_raster_ext, to_posix_array_path,
)

_ENV_KEYS = ("SR_DRIVE_MAP", "SR_ALLOWED_ROOTS", "SR_SCENE_PATH_TEMPLATE")


class EnvMixin(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @property
    def tmp(self) -> Path:
        return Path(self._tmp.name)

    @property
    def outside(self) -> Path:
        """白名单根之外的绝对路径（不要求存在，只是为了过 is_absolute）。"""
        return self.tmp.parent / "sr-pathguard-outside"

    def setenv(self, **kw):
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def map_tmp_drive_identity(self):
        """Windows 开发机上临时目录带盘符（`C:\\...`）。把该盘符映射成它
        自己，归一化即恒等，`ensure_allowed` 的 is_file/is_dir 才落在真实
        文件上。Linux/macOS 上 `Path.drive` 为空，走默认映射。"""
        drive = self.tmp.drive
        self.setenv(SR_DRIVE_MAP=f"{drive}={drive}" if drive else None)

    def allow_tmp(self):
        """把临时目录设成白名单根（并保证归一化后仍指向同一处）。"""
        self.map_tmp_drive_identity()
        self.setenv(SR_ALLOWED_ROOTS=self.tmp.as_posix())


class TestDriveMap(EnvMixin):
    def test_default_maps_w_to_diskarray(self):
        self.setenv(SR_DRIVE_MAP=None)
        self.assertEqual(drive_map(), {"W:": "/DiskArray"})

    def test_multiple_entries_skips_malformed(self):
        # 键统一大写；无 `=` / 空键的条目忽略
        self.setenv(SR_DRIVE_MAP="w:=/DiskArray; X:=/data/x ;bad;  =/x")
        self.assertEqual(drive_map()["W:"], "/DiskArray")
        self.assertEqual(drive_map()["X:"], "/data/x")
        self.assertNotIn(":", drive_map())

    def test_value_backslashes_and_trailing_slash(self):
        self.setenv(SR_DRIVE_MAP="Y:=/mnt\\prod\\")
        self.assertEqual(drive_map()["Y:"], "/mnt/prod")


class TestToPosixArrayPath(EnvMixin):
    def setUp(self):
        super().setUp()
        self.setenv(SR_DRIVE_MAP=None)          # 默认 W:=/DiskArray

    def test_windows_form(self):
        self.assertEqual(to_posix_array_path(r"W:\a\b"), "/DiskArray/a/b")

    def test_windows_form_forward_slashes_and_trailing(self):
        self.assertEqual(to_posix_array_path("w:/a/b/"), "/DiskArray/a/b")

    def test_bare_drive_is_root(self):
        self.assertEqual(to_posix_array_path("W:"), "/DiskArray")
        self.assertEqual(to_posix_array_path("W:\\"), "/DiskArray")

    def test_quoted_input(self):
        # 资源管理器"复制路径"常带引号
        self.assertEqual(to_posix_array_path(r'"W:\a\b"'), "/DiskArray/a/b")

    def test_posix_form_passthrough(self):
        self.assertEqual(to_posix_array_path("/DiskArray/a/b/"), "/DiskArray/a/b")

    def test_dot_segments_collapsed(self):
        self.assertEqual(to_posix_array_path("/DiskArray/./a"), "/DiskArray/a")

    def test_custom_drive_map(self):
        self.setenv(SR_DRIVE_MAP="Y:=/mnt/prod")
        self.assertEqual(to_posix_array_path(r"Y:\x"), "/mnt/prod/x")

    def test_unknown_drive_lists_configured(self):
        with self.assertRaises(PathDeniedError) as cm:
            to_posix_array_path(r"Z:\a")
        self.assertIn("Z:", str(cm.exception))
        self.assertIn("W:", str(cm.exception))

    def test_rejects_unc(self):
        for bad in (r"\\srv\share\a", "//srv/share/a"):
            with self.subTest(bad=bad), self.assertRaises(PathDeniedError):
                to_posix_array_path(bad)

    def test_rejects_relative(self):
        with self.assertRaises(PathDeniedError):
            to_posix_array_path("a/b")

    def test_rejects_parent_traversal(self):
        with self.assertRaises(PathDeniedError):
            to_posix_array_path(r"W:\a\..\..\etc")
        with self.assertRaises(PathDeniedError):
            to_posix_array_path("/DiskArray/../etc")

    def test_rejects_control_chars_and_empty(self):
        for bad in ("", "   ", "/DiskArray/a\x00b", "/DiskArray/a\nb"):
            with self.subTest(bad=bad), self.assertRaises(PathDeniedError):
                to_posix_array_path(bad)


class TestWhitelist(EnvMixin):
    def test_default_root_is_diskarray(self):
        self.setenv(SR_ALLOWED_ROOTS=None)
        self.assertEqual(allowed_roots(), [Path("/DiskArray")])

    def test_multiple_roots(self):
        self.setenv(SR_ALLOWED_ROOTS="/DiskArray;/mnt/other")
        self.assertEqual(allowed_roots(), [Path("/DiskArray"), Path("/mnt/other")])

    def test_invalid_root_entry_rejected(self):
        self.setenv(SR_ALLOWED_ROOTS="relative/path")
        with self.assertRaises(PathDeniedError) as cm:
            allowed_roots()
        self.assertIn("SR_ALLOWED_ROOTS", str(cm.exception))

    def test_sibling_prefix_is_not_within(self):
        # 段级比较：/DiskArrayX 不属于 /DiskArray
        self.assertFalse(is_within("/DiskArrayX/a", "/DiskArray"))
        self.assertTrue(is_within("/DiskArray/a", "/DiskArray"))
        self.assertTrue(is_within("/DiskArray", "/DiskArray"))

    def test_allowed_accepts_windows_form_root(self):
        self.setenv(SR_ALLOWED_ROOTS="W:\\GSHC2IMPS\\PRODUCT")
        self.assertEqual(allowed_roots(), [Path("/DiskArray/GSHC2IMPS/PRODUCT")])

    def test_ensure_allowed_any_permits_missing(self):
        self.allow_tmp()
        missing = self.tmp / "nope"
        self.assertEqual(ensure_allowed(missing), missing)   # 不要求存在

    def test_ensure_allowed_dir_does_not_require_file(self):
        # 回归：老 ensure_within 最后一条 is_file() 会把目录挡在门外
        self.allow_tmp()
        d = self.tmp / "scene"
        d.mkdir()
        self.assertEqual(ensure_allowed(d, kind="dir"), d)

    def test_ensure_allowed_file_rejects_dir(self):
        self.allow_tmp()
        with self.assertRaises(PathDeniedError):
            ensure_allowed(self.tmp, kind="file")

    def test_ensure_allowed_rejects_outside_root(self):
        self.allow_tmp()
        with self.assertRaises(PathDeniedError) as cm:
            ensure_allowed(self.outside)
        self.assertIn("SR_ALLOWED_ROOTS", str(cm.exception))

    def test_ensure_allowed_rejects_fake_placeholder(self):
        self.allow_tmp()
        with self.assertRaises(PathDeniedError):
            ensure_allowed(self.tmp / "<fake>" / "x.tif")

    def test_empty_roots_is_denied(self):
        self.setenv(SR_ALLOWED_ROOTS="   ;  ")
        self.assertEqual(allowed_roots(), [])
        with self.assertRaises(PathDeniedError):
            ensure_allowed(self.tmp)

    def test_is_allowed(self):
        self.allow_tmp()
        self.assertTrue(is_allowed(self.tmp / "a"))
        self.assertFalse(is_allowed(self.outside))


class TestParseSceneDate(EnvMixin):
    def test_fourteen_digit_timestamp(self):
        self.assertEqual(
            parse_scene_date("JL1KF02B03_PMS02_20260910124710_200536960_101_L1_PAN"),
            "2026-09-10")

    def test_eight_digit_only(self):
        self.assertEqual(parse_scene_date("GF07A03_20260722"), "2026-07-22")

    def test_no_date_returns_none(self):
        self.assertIsNone(parse_scene_date("PAN"))
        self.assertIsNone(parse_scene_date("scene_a.tif"))

    def test_impossible_date_returns_none(self):
        self.assertIsNone(parse_scene_date("X_20261332999999"))   # 13 月

    def test_embedded_digits_do_not_match(self):
        # 与前后数字相连的 8 位不算（避免截断出半个时间戳）
        self.assertIsNone(parse_scene_date("X_1202609101"))


class TestSceneNameLayers(EnvMixin):
    """生产命名规则 → （卫星型号, 段级目录名）。样本取自真机实际路径。"""

    #: 真机场景目录名（景级）：.../2026/09/02/JL1KF02B03/<段级>/<这个名字>
    PROD = "JL1KF02B03_PMS09_20260902120156_200535158_102_0025_001_L1_PAN"

    def test_production_name(self):
        self.assertEqual(
            scene_name_layers(self.PROD),
            ("JL1KF02B03",
             "JL1KF02B03_PMS09_20260902120156_200535158_102_001_L1_PAN"))

    def test_user_spec_example(self):
        # 用户给的命名规则样本：段号 101 / 景号 0006 → 段级目录去掉景号段
        self.assertEqual(
            scene_name_layers(
                "JXGF07D03_PMS_20260622052600_200516571_101_0006_001_L1_MSS"),
            ("JXGF07D03",
             "JXGF07D03_PMS_20260622052600_200516571_101_001_L1_MSS"))

    def test_non_production_name_returns_none(self):
        for bad in ("PAN", "SC1", "a/b",
                    "JL1KF02B03_PMS02_20260910124710_L1_PAN",   # 段数不够
                    "A_B_C_D_E_F_G"):                           # 段号/景号非数字
            with self.subTest(bad=bad):
                self.assertIsNone(scene_name_layers(bad))

    def test_space_separated_name(self):
        """用户口径里的空格形态（`JXGF07D03 PMS … MSS`）也要拆得出来。

        段级目录名必须**沿用原文的分隔符** —— 空格名拼出下划线的段级目录必然
        在盘阵上 stat 不到。
        """
        self.assertEqual(
            scene_name_layers(
                "JXGF07D03 PMS 20260622052600 200516571 101 0006 001 L1 MSS"),
            ("JXGF07D03",
             "JXGF07D03 PMS 20260622052600 200516571 101 001 L1 MSS"))

    def test_leading_or_trailing_separator_returns_none(self):
        # 首尾带分隔符 → 段下标整体错位，宁可判不合规则也不猜
        for bad in ("_A_B_C_D_E_0006_F", "A_B_C_D_E_0006_F_"):
            with self.subTest(bad=bad):
                self.assertIsNone(scene_name_layers(bad))


class TestProductionTreeDepth(EnvMixin):
    """目录在生产树的第几层 —— 只给 404 的措辞用（纯词法，不 stat）。"""

    def test_levels_of_six_layer_tree(self):
        base = "/DiskArray/GSHC2IMPS/PRODUCT/2026/09/18"
        self.assertEqual(production_tree_depth(base), 0)                 # 日期目录
        self.assertEqual(production_tree_depth(base + "/JL1KF02B03"), 1)  # 卫星型号
        self.assertEqual(production_tree_depth(base + "/JL1KF02B03/MID"), 2)   # 段级
        self.assertEqual(production_tree_depth(base + "/JL1KF02B03/MID/SC"), 3)  # 景级
        self.assertEqual(
            production_tree_depth(base + "/JL1KF02B03/MID/SC/Debug"), 4)  # 场景目录里的子目录

    def test_windows_form_and_trailing_slash(self):
        self.assertEqual(
            production_tree_depth("W:\\GSHC2IMPS\\PRODUCT\\2026\\09\\18"), 0)
        self.assertEqual(
            production_tree_depth("/DiskArray/GSHC2IMPS/PRODUCT/2026/09/18/"), 0)

    def test_no_date_segment_returns_none(self):
        for path in ("/DiskArray/GSHC2IMPS/PRODUCT", "/DiskArray/GSHC2IMPS/PRODUCT/scratch",
                     "/DiskArray/2026/13/40", "/DiskArray/2026/09"):
            with self.subTest(path=path):
                self.assertIsNone(production_tree_depth(path))

    def test_takes_rightmost_date_run(self):
        # 路径里出现两段日期形态时取最靠右的那段（离被粘的目录最近）
        self.assertEqual(
            production_tree_depth("/x/2020/01/02/2026/09/18/JL1KF02B03"), 1)


class TestFlatSceneLayout(EnvMixin):
    """两种拓扑都支持，且**日期目录下面那段含义不同** —— 这是 404 措辞的分水岭。"""

    PROD = TestSceneNameLayers.PROD

    def test_six_layer_tree_is_not_flat(self):
        base = f"/DiskArray/GSHC2IMPS/PRODUCT/2026/09/18/JL1KF02B03/{self.PROD}"
        self.assertFalse(flat_scene_layout(base))
        self.assertFalse(flat_scene_layout("/DiskArray/GSHC2IMPS/PRODUCT/2026/09/18"))
        self.assertFalse(
            flat_scene_layout("/DiskArray/GSHC2IMPS/PRODUCT/2026/09/18/JL1KF02B03"))

    def test_flat_layout_recognised(self):
        self.assertTrue(flat_scene_layout(
            f"/DiskArray/GSHC2IMPS/PRODUCT/2026/09/18/{self.PROD}"))
        # 扁平形态下再深一层是场景目录**内部**，仍然算扁平拓扑
        self.assertTrue(flat_scene_layout(
            f"/DiskArray/GSHC2IMPS/PRODUCT/2026/09/18/{self.PROD}/Debug"))

    def test_no_date_run_is_not_flat(self):
        self.assertFalse(flat_scene_layout("/DiskArray/GSHC2IMPS/PRODUCT/scratch"))


class TestLooksLikeSceneName(EnvMixin):
    def test_recognises_full_production_name(self):
        for name in (TestSceneNameLayers.PROD,
                     "JXGF07D03_PMS_20260622052600_200516571_101_0006_001_L1_MSS",
                     "GF07A03_20260722"):
            with self.subTest(name=name):
                self.assertTrue(looks_like_scene_name(name))

    def test_rejects_truncated_names(self):
        # 日期目录的 `18`、卫星型号层的 `JXGF07D03`：拿不出 14/8 位成像时刻
        for name in ("18", "JXGF07D03", "JL1KF02B03", "scratch", "PAN", "2026"):
            with self.subTest(name=name):
                self.assertFalse(looks_like_scene_name(name))


class TestInferScenePaths(EnvMixin):
    PROD = TestSceneNameLayers.PROD

    def test_default_two_days(self):
        """默认按生产树渲染**两条**：成像日在前，次日在后。

        盘阵按生产日建目录，深夜成像的景记在第二天 —— 名字里的 14 位只当
        下界用（用户 2026-09-18 报的 bug：名含 0917 的图大半在 0918 目录下）。
        """
        self.setenv(SR_SCENE_PATH_TEMPLATE=None, SR_DRIVE_MAP=None)
        got = infer_scene_paths(self.PROD, "2026-09-02")
        self.assertEqual(got, [
            "/DiskArray/GSHC2IMPS/PRODUCT/2026/09/02/JL1KF02B03/"
            "JL1KF02B03_PMS09_20260902120156_200535158_102_001_L1_PAN/"
            + self.PROD,
            "/DiskArray/GSHC2IMPS/PRODUCT/2026/09/03/JL1KF02B03/"
            "JL1KF02B03_PMS09_20260902120156_200535158_102_001_L1_PAN/"
            + self.PROD,
        ])

    def test_default_non_production_name_no_candidate(self):
        """名字拆不出那两层 → 不硬拼，一条候选也不构造。"""
        self.setenv(SR_SCENE_PATH_TEMPLATE=None, SR_DRIVE_MAP=None)
        got = infer_scene_paths("JL1KF02B03_PMS02_20260910124710_L1_PAN",
                                "2026-09-10")
        self.assertEqual(got, [])

    def test_next_day_rolls_over_month(self):
        """次日跨月靠 timedelta 算，不字符串加一。"""
        self.setenv(SR_SCENE_PATH_TEMPLATE="/prod/{y}/{m}/{d}/{name}")
        self.assertEqual(infer_scene_paths("SC1", "2026-09-30"),
                         ["/prod/2026/09/30/SC1", "/prod/2026/10/01/SC1"])

    def test_strips_raster_ext(self):
        """前端发来的是**用户拖进来的文件名**（带后缀）；目录名从不带后缀。"""
        self.setenv(SR_SCENE_PATH_TEMPLATE=None, SR_DRIVE_MAP=None)
        want = infer_scene_paths(self.PROD, "2026-09-02")
        for suffixed in (self.PROD + ".tif", self.PROD + ".TIF",
                         self.PROD + ".jpg"):
            with self.subTest(name=suffixed):
                self.assertEqual(infer_scene_paths(suffixed, "2026-09-02"), want)
        self.assertEqual(strip_raster_ext("A_PAN.tiff"), "A_PAN")
        self.assertEqual(strip_raster_ext("A_PAN"), "A_PAN")
        self.assertEqual(strip_raster_ext("A.tif.bak"), "A.tif.bak")

    def test_env_template_with_layers(self):
        """自定模板同样给两天：模板里的 {d} 也按成像日/次日各渲染一遍。"""
        self.setenv(SR_SCENE_PATH_TEMPLATE="/prod/{y}/{m}/{d}/{sat}/{mid}/{name}")
        got = infer_scene_paths(self.PROD, "2026-09-02")
        self.assertEqual(got, [
            "/prod/2026/09/02/JL1KF02B03/"
            "JL1KF02B03_PMS09_20260902120156_200535158_102_001_L1_PAN/"
            + self.PROD,
            "/prod/2026/09/03/JL1KF02B03/"
            "JL1KF02B03_PMS09_20260902120156_200535158_102_001_L1_PAN/"
            + self.PROD])

    def test_env_template_with_layers_nonconforming_name(self):
        """模板要 {sat}/{mid} 而名字拆不出 → 一条候选都不构造（空列表）。"""
        self.setenv(SR_SCENE_PATH_TEMPLATE="/prod/{y}/{m}/{d}/{sat}/{name}")
        self.assertEqual(infer_scene_paths("SC1", "2026-01-02"), [])

    def test_env_template_without_date_placeholder(self):
        """模板不含日期占位符 → 两天渲染出同一条，去重后仍只有一条。"""
        self.setenv(SR_SCENE_PATH_TEMPLATE="/prod/scenes/{name}")
        self.assertEqual(infer_scene_paths("SC1", "2026-01-02"),
                         ["/prod/scenes/SC1"])

    def test_custom_template(self):
        self.setenv(SR_SCENE_PATH_TEMPLATE="/prod/{y}/{m}/{d}/{name}")
        got = infer_scene_paths("SC1", "2026-01-02")
        self.assertEqual(got, ["/prod/2026/01/02/SC1", "/prod/2026/01/03/SC1"])

    def test_pure_lexical_no_stat(self):
        # 反推只拼字符串：目录根本不存在也要照样返回
        self.setenv(SR_SCENE_PATH_TEMPLATE="/prod/{y}/{m}/{d}/{name}")
        got = infer_scene_paths("NOT_EXIST_AT_ALL", "2026-01-02")
        self.assertEqual(got, ["/prod/2026/01/02/NOT_EXIST_AT_ALL",
                              "/prod/2026/01/03/NOT_EXIST_AT_ALL"])

    def test_rejects_stem_with_separator(self):
        for bad in ("a/b", "a\\b", "a\x00b", ""):
            with self.subTest(bad=bad), self.assertRaises(PathDeniedError):
                infer_scene_paths(bad, "2026-09-10")

    def test_bare_dotdot_yields_no_candidate(self):
        """`..` 单独当名字：拆不出六段形态 → 一条候选都构造不出（空列表），不抛。

        空列表与 PathDeniedError 都是拒绝（调用方一律回 400），只是文案不同。
        """
        self.setenv(SR_SCENE_PATH_TEMPLATE=None, SR_DRIVE_MAP=None)
        self.assertEqual(infer_scene_paths("..", "2026-09-10"), [])

    def test_rejects_traversal_rendered_into_path(self):
        """`..` 混在名字里被拼进路径（卫星型号段）→ 渲染后按穿越段拒掉。"""
        self.setenv(SR_SCENE_PATH_TEMPLATE="/prod/{y}/{m}/{d}/{sat}/{mid}/{name}")
        name = ".._PMS02_20260910124710_200536960_101_0005_001_L1_PAN"
        with self.assertRaises(PathDeniedError):
            infer_scene_paths(name, "2026-09-10")

    def test_rejects_bad_date(self):
        for bad in ("2026/09/10", "2026-9-10", "today", "20260910"):
            with self.subTest(bad=bad), self.assertRaises(PathDeniedError):
                infer_scene_paths("SC1", bad)


if __name__ == "__main__":
    unittest.main()
