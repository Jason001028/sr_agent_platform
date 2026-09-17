"""POST /api/scenes/resolve —— 打开盘阵上任意一个合法场景目录（盘阵任意目录）。

覆盖：Windows 形态 `W:\\...` 与 POSIX 形态都吃；`{name, date}` 反推；四种
错误码分工（400 形态 / 403 白名单 / 404 不是场景目录 / 422 读不到尺寸）；
`~` 形态的场景 id 能走 /preview；**绝不列举目录**（把 rglob/glob/iterdir/
listdir/scandir/walk 全部打桩成抛错，resolve 仍须 200）。

夹具用真机布局 `<根>/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<编号>/<编号>.tif` +
`<编号>_meta.xml`，并把 W: 映射到临时根 —— 这样测试里写的 `W:\\...` 就是
用户在客户端真正会粘的那一串。
"""

import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.paths import scene_id, scene_id_abs
from backend.services import slurm
from backend.tests import allowed_roots_env

_ENVS = ("SR_AGENT_DB", "SR_SCENES_ROOT", "SR_PREVIEWS_ROOT", "SR_LLM_MOCK",
         "SR_SLURM_FAKE", "SR_EXECUTOR", "SR_BUNDLE_DIR",
         "SR_DRIVE_MAP", "SR_ALLOWED_ROOTS", "SR_SCENE_PATH_TEMPLATE")

#: 生产编号 —— 真机形态，14 位成像时间嵌在中间（反推靠它取日期）。
SCENE_NAME = "JL1KF02B03_PMS02_20260917124710_200536960_101_0005_001_L1_PAN"
SCENE_DATE = "2026-09-17"
#: 生产树里它上面那一层（段级目录）= 去掉景号段（`0005`）。
PROD_MID = "JL1KF02B03_PMS02_20260917124710_200536960_101_001_L1_PAN"
#: 合成样本名（同样符合生产命名规则）：喂 Windows 260 路径上限的场景用。
SHORT_NAME = "A_B_20260917124710_200536960_102_0025_001_L1_PAN"


def make_scene(dirp, name, w=320, h=640, meta=True, tif=True):
    """造一个真机形态的场景目录，返回目录路径。"""
    d = Path(dirp)
    d.mkdir(parents=True, exist_ok=True)
    if tif:
        arr = (np.arange(w * h).reshape(h, w) % 65535).astype(np.uint16)
        tifffile.imwrite(d / f"{name}.tif", arr, photometric="minisblack")
    if meta:
        (d / f"{name}_meta.xml").write_text(
            '<?xml version="1.0"?><SolarAzimuth>181.79</SolarAzimuth>',
            encoding="utf-8")
    return d


class ResolveBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._apps: list = []
        self._saved = {k: os.environ.get(k) for k in _ENVS}
        slurm._fake_reset()
        for k in _ENVS:
            os.environ.pop(k, None)
        self.root = Path(self._tmp.name).resolve()
        # 盘阵根 = 临时根；W: 映射到它 —— 测试里写 `W:\GSHC2IMPS\...` 就等于
        # 临时根下的真实目录。白名单只用 W: 形态表达，两种平台都成立。
        # X: 映射到临时根的**父目录**，给"越白名单"用例造一个前缀外的真路径。
        maps = (f"W:={self.root.as_posix()};"
                f"X:={self.root.parent.as_posix()}")
        # 本机绝对路径（POSIX 写法 / 开发机的 `C:\...`）也要能表达：临时目录
        # 所在的盘符补一条"映射到自身"的规则。
        extra = allowed_roots_env(self.root).get("SR_DRIVE_MAP")
        if extra:
            maps += f";{extra}"
        os.environ["SR_DRIVE_MAP"] = maps
        os.environ["SR_ALLOWED_ROOTS"] = "W:\\"
        os.environ["SR_SLURM_FAKE"] = "1"

    def tearDown(self):
        for a in self._apps:
            try:
                a.state.store.close()
            except Exception:  # noqa: BLE001
                pass
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def client(self):
        app = create_app()
        self._apps.append(app)
        return TestClient(app)

    # -- 夹具 ---------------------------------------------------------------
    @property
    def scene_dir(self) -> Path:
        return (self.root / "GSHC2IMPS" / "PRODUCT" / "2026" / "09" / "17"
                / SCENE_NAME)

    def win_path(self, dirp: Path) -> str:
        """把临时根下的目录写成用户会粘的 Windows 形态。"""
        rel = dirp.resolve().relative_to(self.root).as_posix()
        return "W:\\" + rel.replace("/", "\\")

    def make_scene(self, **kw) -> Path:
        return make_scene(self.scene_dir, SCENE_NAME, **kw)


class TestResolveOk(ResolveBase):
    def test_windows_form_opens_manual_scene(self):
        d = self.make_scene()
        c = self.client()
        r = c.post("/api/scenes/resolve", json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["source"], "manual")
        row = body["row"]
        self.assertTrue(row["manual"])
        self.assertTrue(row["id"].startswith("~"))       # 手工行 id 形态
        self.assertEqual(row["name"], SCENE_NAME)
        self.assertEqual(row["lq_path"], d.as_posix())   # 契约：盘阵 POSIX 形态
        self.assertEqual(row["date"], SCENE_DATE)
        self.assertEqual(row["satellite"], "JL1KF02B03")
        self.assertEqual(row["sensor"], "PMS02")
        self.assertFalse(row["fake"])
        self.assertEqual((row["W"], row["H"]), (320, 640))
        self.assertGreater(row["size_bytes"], 0)
        self.assertIsNone(row["jpgUrl"])                 # 库外：走 /preview
        self.assertFalse(row["hasPreview"])
        res = body["resolved"]
        self.assertEqual(res["input"], (d / f"{SCENE_NAME}.tif").as_posix())
        self.assertEqual(res["input_name"], f"{SCENE_NAME}.tif")
        # POSIX 形态（掩码路径要参与 task_fingerprint，不随宿主平台变）
        self.assertEqual(res["mask_path"],
                         (d / f"{SCENE_NAME}_mask.tif").as_posix())
        self.assertFalse(res["mask_exists"])
        self.assertTrue(res["writable"])

    def test_posix_form_also_accepted(self):
        # 服务端形态（盘阵上是 /DiskArray/...；开发机上是同一目录的 POSIX 写法）
        d = self.make_scene()
        c = self.client()
        r = c.post("/api/scenes/resolve", json={"path": d.as_posix()})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["lq_path"], d.as_posix())

    def test_quoted_windows_path(self):
        # 资源管理器"复制路径"带引号
        d = self.make_scene()
        c = self.client()
        r = c.post("/api/scenes/resolve", json={"path": f'"{self.win_path(d)}"'})
        self.assertEqual(r.status_code, 200, r.text)

    def test_works_without_scenes_root(self):
        # 盘阵场景库根未配置也要能开（手工路径不依赖它）
        self.assertNotIn("SR_SCENES_ROOT", os.environ)
        d = self.make_scene()
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 200, r.text)
        row = r.json()["row"]
        self.assertEqual(row["lq_path"], d.as_posix())   # 契约：盘阵 POSIX 形态
        self.assertIsNone(row["jpgUrl"])            # 无库根 → 无静态 URL
        self.assertIsNone(row["rel"])

    def test_reverse_lookup_by_name_and_date(self):
        d = self.make_scene()
        c = self.client()
        r = c.post("/api/scenes/resolve",
                   json={"name": SCENE_NAME, "date": SCENE_DATE})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["lq_path"], d.as_posix())

    def test_reverse_lookup_without_date(self):
        # 不给 date：后端自己从文件名的 14 位成像时间戳取（前端不再自解析）
        # 文件名带后缀 —— 前端发过来的就是用户拖的那个文件名，目录名不带后缀
        d = self.make_scene()
        r = self.client().post("/api/scenes/resolve",
                               json={"name": SCENE_NAME + ".tif"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["lq_path"], d.as_posix())

    def test_reverse_lookup_hits_production_tree(self):
        """生产树真形态：`<日>/<卫星型号>/<段级目录>/<景级目录>` 也要能命中。

        扁平形态那条候选故意不建目录 —— 命中只可能来自生产树候选。

        名字是**合成样本且刻意取短**：临时目录前缀已占 ~90 字符，真实的 57 字符
        场景名再加两层目录会超过 Windows 260 的路径上限（本机 >250 即
        FileNotFoundError）。本用例验的是层级与反推，不是名字长度。
        """
        name = SHORT_NAME
        mid = "A_B_20260917124710_200536960_102_001_L1_PAN"
        d = (self.root / "GSHC2IMPS" / "PRODUCT" / "2026" / "09" / "17"
             / "A" / mid / name)
        make_scene(d, name)
        self.assertFalse(self.scene_dir.exists())
        r = self.client().post("/api/scenes/resolve", json={"name": name})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["lq_path"], d.as_posix())

    def test_row_inside_scenes_root_gets_static_preview_url(self):
        d = self.make_scene()
        os.environ["SR_SCENES_ROOT"] = str(self.root)
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 200, r.text)
        row = r.json()["row"]
        # rel 与库行同义：相对 scenes 根、**含文件名**的路径（scene_id 靠它反解）
        self.assertEqual(
            row["rel"],
            f"GSHC2IMPS/PRODUCT/2026/09/17/{SCENE_NAME}/{SCENE_NAME}.tif")
        self.assertTrue(row["jpgUrl"].startswith("/disk-array/"))

    def test_pan_scene_input_is_pan_tif_and_mask_follows_input(self):
        """RC 步骤：输入是 PAN.tif，掩码名跟着**输入影像**走，不是目录名。

        掩码文件名纯粹是平台内部约定：`<MaskPath>` 由平台自己写进配置 XML
        （services/run_sr.py），SR 脚本只照读。所以只要 `/api/masks` 写出去的
        名字和提交时找的名字同源（同一个 mask_stem）就行 —— 这条用例钉住的就是
        这个同源：SC（`<目录名>.tif`）两者恰好相同，RC 不同，以前正是这里
        写进去叫 PAN_mask.tif、提交时却去找 <目录名>_mask.tif。
        """
        d = self.scene_dir
        make_scene(d, SCENE_NAME, tif=False)
        arr = (np.arange(320 * 640).reshape(640, 320) % 65535).astype(np.uint16)
        tifffile.imwrite(d / "PAN.tif", arr, photometric="minisblack")
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 200, r.text)
        res = r.json()["resolved"]
        self.assertEqual(res["input_name"], "PAN.tif")
        self.assertEqual(res["mask_path"], (d / "PAN_mask.tif").as_posix())
        self.assertEqual(r.json()["row"]["name"], "PAN")


class TestResolveErrors(ResolveBase):
    def test_missing_dir_404_lists_candidate(self):
        d = self.scene_dir                      # 没建
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 404)
        detail = r.json()["detail"]
        self.assertIn(str(d), detail)
        self.assertIn("目录不存在", detail)

    def test_dir_without_meta_404_says_why(self):
        d = self.make_scene(meta=False)
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 404)
        detail = r.json()["detail"]
        self.assertIn("_meta.xml", detail)
        self.assertIn(str(d), detail)

    def test_dir_without_input_image_404_lists_tried_names(self):
        d = self.make_scene(tif=False)
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 404)
        detail = r.json()["detail"]
        self.assertIn("没有输入影像", detail)
        self.assertIn(f"{SCENE_NAME}.tif", detail)      # 试过哪些名字
        self.assertIn("PAN.tif", detail)

    def test_reverse_lookup_wrong_date_404(self):
        self.make_scene()
        r = self.client().post("/api/scenes/resolve",
                               json={"name": SCENE_NAME, "date": "2026-09-18"})
        self.assertEqual(r.status_code, 404)
        detail = r.json()["detail"]
        self.assertIn(SCENE_NAME, detail)
        self.assertIn("目录不存在", detail)
        # 两条候选都要列出来（生产树 + 旧扁平），缺一条就说不清"试过哪些"。
        # 只断言目录名：路径分隔符是宿主形态（Windows 上 str(Path) 是反斜杠）。
        self.assertIn(PROD_MID, detail)

    def test_reverse_lookup_without_timestamp_400(self):
        r = self.client().post("/api/scenes/resolve", json={"name": "PAN"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("时间戳", r.json()["detail"])

    def test_unreadable_image_422(self):
        # 场景成立（meta 在、输入在），但输入不是可解析的 TIFF → 读不到 W/H
        d = self.scene_dir
        make_scene(d, SCENE_NAME, tif=False)
        (d / f"{SCENE_NAME}.tif").write_bytes(b"not a tiff at all")
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("尺寸", r.json()["detail"])

    def test_outside_whitelist_403(self):
        # X: 映到临时根的父目录 —— 是个真实存在、但不在 W: 白名单前缀内的场景
        outside = self.root.parent / "sr-not-allowed" / SCENE_NAME
        make_scene(outside, SCENE_NAME)
        try:
            r = self.client().post(
                "/api/scenes/resolve",
                json={"path": "X:\\sr-not-allowed\\" + SCENE_NAME})
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("SR_ALLOWED_ROOTS", r.json()["detail"])
        finally:
            shutil.rmtree(outside.parent, ignore_errors=True)

    def test_bad_form_400(self):
        c = self.client()
        for bad in ("relative/path", "Z:\\nope", "\\\\srv\\share", "W:\\a\\..\\b"):
            r = c.post("/api/scenes/resolve", json={"path": bad})
            self.assertEqual(r.status_code, 400, (bad, r.text))
            self.assertIn("形态非法", r.json()["detail"])

    def test_missing_params_400(self):
        c = self.client()
        for body in ({}, {"date": SCENE_DATE}, {"name": ""},
                     {"name": SCENE_NAME, "date": "20260917"},
                     {"name": "a/b", "date": SCENE_DATE},
                     {"name": "local_nodate.tif"}):     # 名字里没时间戳
            r = c.post("/api/scenes/resolve", json=body)
            self.assertEqual(r.status_code, 400, (body, r.text))

    def test_invalid_json_body_400(self):
        r = self.client().post("/api/scenes/resolve",
                               content=b"not json",
                               headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)


class TestNeverListsDirectories(ResolveBase):
    """不扫盘是可执行的保证，不是注释里的承诺。

    `os.scandir` 也是 `shutil.rmtree` 清临时目录时要用的，所以打桩必须用
    try 块收在请求范围里 —— 用 `addCleanup` 会让打桩活到 tearDown 之后，
    临时目录删不掉（这是踩过的坑）。
    """

    BANNED = ("rglob", "glob", "iterdir")

    def test_resolve_and_preview_without_listing(self):
        d = self.make_scene()
        c = self.client()
        with ExitStack() as st:
            for name in self.BANNED:
                st.enter_context(mock.patch.object(
                    Path, name,
                    side_effect=AssertionError(f"禁止 Path.{name}（扫盘）")))
            for name in ("listdir", "scandir", "walk"):
                st.enter_context(mock.patch.object(
                    os, name,
                    side_effect=AssertionError(f"禁止 os.{name}（扫盘）")))
            r = c.post("/api/scenes/resolve", json={"path": self.win_path(d)})
            self.assertEqual(r.status_code, 200, r.text)
            pv = c.get(f"/api/scenes/{r.json()['row']['id']}/preview")
            self.assertEqual(pv.status_code, 200, pv.text)
            self.assertEqual(pv.headers["content-type"], "image/jpeg")

    def test_probes_a_bounded_number_of_files(self):
        """最多 6 次候选探测 + 若干 stat，绝不随目录内容增长。

        这条用 stat 调用次数兜底：目录里塞满噪声文件，探测次数也不变。
        """
        d = self.make_scene()
        for i in range(50):
            (d / f"noise_{i}.dat").write_bytes(b"")
        real_stat = Path.stat
        calls: list[str] = []

        def counting_stat(self, *a, **kw):
            calls.append(self.name)
            return real_stat(self, *a, **kw)

        c = self.client()
        with mock.patch.object(Path, "stat", counting_stat):
            r = c.post("/api/scenes/resolve", json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("noise_0.dat", calls)
        self.assertLessEqual(len(calls), 30, calls)


class TestManualSceneId(ResolveBase):
    def test_preview_of_manual_scene_writes_jpg_beside_source(self):
        d = self.make_scene()
        c = self.client()
        row = c.post("/api/scenes/resolve",
                     json={"path": self.win_path(d)}).json()["row"]
        r = c.get(f"/api/scenes/{row['id']}/preview")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertTrue((d / f"{SCENE_NAME}.preview.jpg").is_file())

    def test_abs_id_outside_whitelist_404(self):
        # 手工 id 也不许越白名单（解码后仍过 ensure_allowed）
        outside = Path("/etc/passwd") if os.name != "nt" else Path("C:/Windows/win.ini")
        r = self.client().get(f"/api/scenes/{scene_id_abs(outside)}/preview")
        self.assertEqual(r.status_code, 404)

    def test_abs_id_with_broken_base64_404(self):
        r = self.client().get("/api/scenes/~!!!not-base64!!!/preview")
        self.assertEqual(r.status_code, 404)

    def test_library_id_still_works_and_unknown_404(self):
        # 库行 id 语义零回归：rel 相对 SR_SCENES_ROOT
        d = self.make_scene()
        os.environ["SR_SCENES_ROOT"] = str(self.root)
        c = self.client()
        rel = f"GSHC2IMPS/PRODUCT/2026/09/17/{SCENE_NAME}/{SCENE_NAME}.tif"
        r = c.get(f"/api/scenes/{scene_id(rel)}/preview")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue((d / f"{SCENE_NAME}.preview.jpg").is_file())
        bad = c.get(f"/api/scenes/{scene_id('no/such.tif')}/preview")
        self.assertEqual(bad.status_code, 404)

    def test_library_id_without_root_404(self):
        r = self.client().get(f"/api/scenes/{scene_id('a.tif')}/preview")
        self.assertEqual(r.status_code, 404)
        self.assertIn("SR_SCENES_ROOT", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
