"""POST /api/scenes/resolve —— 打开盘阵上任意一个合法场景目录（盘阵任意目录）。

覆盖：Windows 形态 `W:\\...` 与 POSIX 形态都吃；`{name, date}` 反推；四种
错误码分工（400 形态 / 403 白名单 / 404 不是场景目录 / 422 读不到尺寸）；
`~` 形态的场景 id 能走 /preview；**绝不列举目录**（把 rglob/glob/iterdir/
listdir/scandir/walk 全部打桩成抛错，resolve 仍须 200）。

夹具就是真机布局（六层生产树）
`<根>/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<卫星型号>/<段级目录>/<景级目录>/<景级目录>.tif`
+ `_meta.xml`，并把 W: 映射到临时根 —— 这样测试里写的 `W:\\...` 就是用户在客户端
真正会粘的那一串。**没有平铺那条捷径**：`{name}` 反推必须真能拼出 `{sat}`/`{mid}`
两层才命中（平铺形态只在一个专门验诊断措辞的用例里现搭）。
"""

import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.app import create_app
from backend.api.paths import scene_id, scene_id_abs
from backend.services import slurm
from backend.tests import allowed_roots_env

_ENVS = ("SR_AGENT_DB", "SR_SCENES_ROOT", "SR_PREVIEWS_ROOT", "SR_LLM_MOCK",
         "SR_SLURM_FAKE", "SR_EXECUTOR", "SR_BUNDLE_DIR", "SR_TEMP_PREVIEWS_ROOT",
         "SR_DRIVE_MAP", "SR_ALLOWED_ROOTS", "SR_SCENE_PATH_TEMPLATE")

#: 生产编号 —— 真机形态，14 位成像时间嵌在中间（反推靠它取日期）。
#: **型号/传感器段刻意压成一个字母**：六层生产树要叠在临时目录前缀（~70 字符）
#: 之下，真机那种 57 字符全名会顶爆 Windows 260 的路径上限（>250 即
#: FileNotFoundError）；盘阵是 Linux，没这个限制。判据一个不少 —— 卫星型号段、
#: 3 位段号、4 位景号、14 位成像时刻都在。
SCENE_NAME = "A_B_20260917124710_200536960_101_0005_001_L1_PAN"
SCENE_DATE = "2026-09-17"
#: 生产树里它上面那一层（段级目录）= 去掉景号段（`0005`）。
PROD_MID = "A_B_20260917124710_200536960_101_001_L1_PAN"
#: 另一段（`101` → `102`）的段级目录名：造「另一个场景」用。
MID_OTHER = "A_B_20260917124710_200536960_102_001_L1_PAN"
#: 卫星型号层（生产名的第 0 段）。
SAT_NAME = "A"


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
    #: 六层生产树：`<根>/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<卫星型号>/<段级>/<景级>`
    #: —— 夹具就是真机形态，不存在「平铺」那条捷径，反推必须真能拼出这两层。
    @property
    def day_dir(self) -> Path:
        return self.root / "GSHC2IMPS" / "PRODUCT" / "2026" / "09" / "17"

    @property
    def sat_dir(self) -> Path:
        return self.day_dir / SAT_NAME

    @property
    def scene_dir(self) -> Path:
        return self.sat_dir / PROD_MID / SCENE_NAME

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
        self.assertEqual(row["satellite"], SAT_NAME)     # 生产名第 0 段
        self.assertEqual(row["sensor"], "B")             # 第 1 段
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

    def test_reverse_lookup_hits_next_day_dir(self):
        """名字含 0917 的景躺在 **0918** 目录下 —— 次日候选要能命中（用户报的 bug）。

        盘阵按**生产日**建目录，夜里成像的景记在第二天：`…_20260917124710_…`
        （23 时以后）落进 `…/09/18/…`。名字里的 14 位是**成像**时刻，只当得了
        下界；修复前只按这一天拼目录，于是大部分图都报「目录不存在」。

        用例只造次日的目录（夹具的真身没建），所以命中只可能来自次日那条候选。
        """
        d = (self.root / "GSHC2IMPS" / "PRODUCT" / "2026" / "09" / "18"
             / SAT_NAME / PROD_MID / SCENE_NAME)       # ← 18，名字里是 17
        make_scene(d, SCENE_NAME)
        r = self.client().post("/api/scenes/resolve", json={"name": SCENE_NAME})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["lq_path"], d.as_posix())

    def test_reverse_lookup_prefers_imaging_day(self):
        """两天都有同名目录时，**成像日**那条先命中（顺序即优先级）。"""
        for day in ("17", "18"):
            make_scene(self.root / "GSHC2IMPS" / "PRODUCT" / "2026" / "09"
                       / day / SAT_NAME / PROD_MID / SCENE_NAME, SCENE_NAME)
        r = self.client().post("/api/scenes/resolve", json={"name": SCENE_NAME})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("/2026/09/17/", r.json()["row"]["lq_path"])

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
            f"GSHC2IMPS/PRODUCT/2026/09/17/{SAT_NAME}/{PROD_MID}/"
            f"{SCENE_NAME}/{SCENE_NAME}.tif")
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

    # -- 粘错了层：404 要说清「差在哪一层」-----------------------------------
    def paste_404(self, dirp: Path) -> str:
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(dirp)})
        self.assertEqual(r.status_code, 404, r.text)
        return r.json()["detail"]

    def test_day_dir_404_points_three_levels_down(self):
        """场景栏预填的就是这一层：日期目录 `…/PRODUCT/2026/09/17`，本身不是场景
        目录。以前报「缺 17_meta.xml」—— `<目录名>_meta.xml` 的前缀是**完整生产
        名**（含 14 位成像时刻），`17` 这种前缀不可能构成它，用户读完也不知道
        该粘哪一层。"""
        self.make_scene()
        d = self.day_dir                        # …/2026/09/17
        detail = self.paste_404(d)
        self.assertIn("日期目录", detail)
        self.assertIn(str(d), detail)
        self.assertNotIn("17_meta.xml", detail)     # 不许再编这种 meta 名
        self.assertIn("<景级目录>", detail)          # 说清该粘哪一层

    def test_satellite_dir_404_points_two_levels_down(self):
        sat = self.sat_dir
        sat.mkdir(parents=True)
        detail = self.paste_404(sat)
        self.assertIn("卫星型号层", detail)
        self.assertIn("<段级目录>/<景级目录>", detail)

    def test_mid_dir_404_points_one_level_down(self):
        """段级目录（= 景级目录去掉景号那段）。它的名字**也**带 14 位成像时刻，
        光看名字会误报「缺 <段级名>_meta.xml」—— 得按层数认下来。"""
        mid = self.sat_dir / PROD_MID
        mid.mkdir(parents=True)                 # 只判层，不必造影像
        detail = self.paste_404(mid)
        self.assertIn("段级", detail)
        self.assertIn("<景级目录>", detail)
        self.assertNotIn(f"{PROD_MID}_meta.xml", detail)

    def test_scene_subdir_404_says_too_deep(self):
        """六层树：景级目录内部再深一层。用第 102 景（段级名跟着换）。"""
        scene = self.sat_dir / MID_OTHER / SCENE_NAME
        (scene / "Debug").mkdir(parents=True)
        detail = self.paste_404(scene / "Debug")
        self.assertIn("场景目录内部的子目录", detail)

    def test_flat_topology_scene_subdir_404_says_too_deep(self):
        """扁平拓扑（`<年>/<月>/<日>/<场景名>`，非生产树部署仍有）里，场景目录
        再深一层是它**内部**，不是段级层 —— 这两种拓扑得分开认。"""
        flat = self.day_dir / SCENE_NAME
        d = make_scene(flat, SCENE_NAME, tif=False) / "Debug"
        d.mkdir(parents=True, exist_ok=True)
        detail = self.paste_404(d)
        self.assertIn("子目录", detail)
        self.assertNotIn("段级", detail)

    def test_non_production_dir_404_says_name_shape(self):
        d = self.root / "GSHC2IMPS" / "PRODUCT" / "scratch"
        d.mkdir(parents=True)
        detail = self.paste_404(d)
        self.assertIn("不是完整生产名", detail)
        self.assertNotIn("scratch_meta.xml", detail)

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
        """用户手填了个错日期：两天候选都落空，404 要说清试过哪些、试的是哪两天。"""
        self.make_scene()                       # 真身在 09/17
        r = self.client().post("/api/scenes/resolve",
                               json={"name": SCENE_NAME, "date": "2026-09-18"})
        self.assertEqual(r.status_code, 404)
        detail = r.json()["detail"]
        self.assertIn(SCENE_NAME, detail)
        self.assertIn("目录不存在", detail)
        # 两条候选都要列出来（09/18 与 09/19），缺一条就说不清"试过哪些"。
        # 只断言目录名：路径分隔符是宿主形态（Windows 上 str(Path) 是反斜杠）。
        self.assertIn(PROD_MID, detail)
        self.assertEqual(detail.count("目录不存在"), 2)
        self.assertIn("成像日与次日都找过", detail)

    def test_404_without_two_days_has_no_day_note(self):
        """自定模板不含日期占位符 → 只有一条候选，别硬塞「两天都找过」那句。"""
        os.environ["SR_SCENE_PATH_TEMPLATE"] = \
            (self.root / "nowhere").as_posix() + "/{name}"
        r = self.client().post("/api/scenes/resolve", json={"name": SCENE_NAME})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("目录不存在", r.json()["detail"])
        self.assertNotIn("次日", r.json()["detail"])

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


class TestBareTifPath(ResolveBase):
    """`{path}` 也可以是一张**裸 .tif**：用户只想看张图，不关心它是不是场景。

    这条入口最大的风险是**把裸 tif 误当成可提交场景** —— 能不能提交 SR 只看
    lqPath / sr_capable，判错就会让用户提交出一个在盘阵上根本跑不起来的作业。
    所以「可提交」和「不可提交」两种情形这里都要钉死。
    """

    def scratch_tif(self, name="random.tif", w=64, h=32) -> Path:
        """非场景目录（没有 meta.xml）里的一张 tif。"""
        d = (self.root / "GSHC2IMPS" / "PRODUCT" / "2026" / "09" / "17"
             / "scratch")
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        tifffile.imwrite(p, (np.arange(w * h).reshape(h, w) % 4000)
                         .astype(np.uint16), photometric="minisblack")
        return p

    def test_bare_tif_inside_scene_dir_is_sr_capable(self):
        """父目录确实是场景目录、且这个文件就是它的输入影像 → 可提交。"""
        d = self.make_scene()
        src = d / f"{SCENE_NAME}.tif"
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(src)})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["resolved"]["sr_capable"])
        self.assertEqual(body["resolved"]["dir"], d.as_posix())
        self.assertEqual(body["resolved"]["input_name"], f"{SCENE_NAME}.tif")
        self.assertEqual(body["row"]["lq_path"], d.as_posix())
        self.assertEqual((body["row"]["W"], body["row"]["H"]), (320, 640))
        # mask_path 一律盘阵 POSIX 形态（提交侧与它对字），不是宿主 str()
        self.assertEqual(body["resolved"]["mask_path"],
                         (d / f"{SCENE_NAME}_mask.tif").as_posix())

    def test_bare_tif_in_non_scene_dir_is_not_sr_capable(self):
        """关键防线：随手粘的一张 tif 不能变成可提交场景。"""
        p = self.scratch_tif()
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(p)})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body["resolved"]["sr_capable"])
        self.assertIsNone(body["resolved"]["mask_path"])
        self.assertIsNone(body["row"]["lq_path"],
                          "lq_path 非空即等于给前端开了 SR 提交入口")
        self.assertEqual(body["row"]["name"], "random")

    def test_bare_tif_preview_bakes_half_size_beside_source(self):
        """裸 tif 也能出图，且产物落在**源同目录**、尺寸是源的一半。"""
        p = self.scratch_tif(w=64, h=32)
        c = self.client()
        row = c.post("/api/scenes/resolve",
                     json={"path": self.win_path(p)}).json()["row"]
        self.assertIsNone(row["jpgUrl"], "库外没有静态 URL，走 /preview 回字节")
        self.assertFalse(row["hasPreview"])
        r = c.get(f"/api/scenes/{row['id']}/preview")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        jpg = p.with_suffix(".preview.jpg")
        self.assertTrue(jpg.is_file(), "预览要落在源文件同目录")
        with Image.open(jpg) as im:
            self.assertEqual(im.size, (32, 16))          # 64×32 → 各 1/2

    def test_hasPreview_becomes_true_after_baking(self):
        """hasPreview 是「缓存到底在不在」的真值（库外同样要准）。

        前端靠它决定要不要提示「首次烘焙较慢」；库外一律 False 的话，第二次
        打开（其实命中缓存、秒开）还会吓唬用户说第一次很慢。"""
        p = self.scratch_tif()
        c = self.client()
        win = self.win_path(p)
        rid = c.post("/api/scenes/resolve",
                     json={"path": win}).json()["row"]["id"]
        c.get(f"/api/scenes/{rid}/preview")
        row = c.post("/api/scenes/resolve", json={"path": win}).json()["row"]
        self.assertTrue(row["hasPreview"])

    def test_tiff_extension_also_accepted(self):
        p = self.scratch_tif(name="upper.tiff")
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(p)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["name"], "upper")

    # -- 错误码分工 --------------------------------------------------------
    def test_non_tif_file_400(self):
        d = self.make_scene()
        meta = d / f"{SCENE_NAME}_meta.xml"
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(meta)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不是 .tif", r.json()["detail"])

    def test_missing_tif_404(self):
        d = self.make_scene()
        r = self.client().post(
            "/api/scenes/resolve",
            json={"path": self.win_path(d / "nope.tif")})
        self.assertEqual(r.status_code, 404)

    def test_tif_outside_whitelist_403(self):
        # 先过白名单才 stat，所以这个文件不必真的存在
        r = self.client().post("/api/scenes/resolve",
                               json={"path": "X:\\nope.tif"})
        self.assertEqual(r.status_code, 403)

    def test_broken_tif_422(self):
        p = self.scratch_tif(name="broken.tif")
        p.write_bytes(b"not a tiff at all")
        r = self.client().post("/api/scenes/resolve",
                               json={"path": self.win_path(p)})
        self.assertEqual(r.status_code, 422)


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

    def test_bare_tif_resolve_and_preview_without_listing(self):
        """裸 tif 入口同样不扫盘：判「父目录是不是场景目录」只试 6 个固定
        候选名（input_scene_path），绝不列举。"""
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
            r = c.post("/api/scenes/resolve",
                       json={"path": self.win_path(d / f"{SCENE_NAME}.tif")})
            self.assertEqual(r.status_code, 200, r.text)
            pv = c.get(f"/api/scenes/{r.json()['row']['id']}/preview")
            self.assertEqual(pv.status_code, 200, pv.text)

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


class TestResolveFingerprint(ResolveBase):
    """拖拽入口的双指纹（`size_bytes`）：**名字 + 字节数**都吻合才认。

    缺了名字那一半，「目录名 .tif 与 PAN.tif 共存」的 RC 场景会被冒名顶替 ——
    见 `_fingerprint_mismatch` 的 docstring。拖 jpg 时比的对象不同（名字比**场景
    目录名**、字节数不比）：jpg 是显示件，与 SR 跑的那份栅格输入不是同一个文件。
    """

    def setUp(self):
        super().setUp()
        self.d = self.make_scene()
        self.tif = self.d / f"{SCENE_NAME}.tif"
        self.size = self.tif.stat().st_size

    def resolve(self, **body):
        return self.client().post("/api/scenes/resolve", json=body)

    def test_matching_size_and_name_ok(self):
        r = self.resolve(name=SCENE_NAME + ".tif", size_bytes=self.size)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["lq_path"], self.d.as_posix())

    def test_missing_size_bytes_still_ok(self):
        # 向后兼容：不带指纹的老调用（粘路径、第三方）一切照旧
        r = self.resolve(name=SCENE_NAME)
        self.assertEqual(r.status_code, 200, r.text)

    def test_wrong_size_404_with_reason_and_candidates(self):
        r = self.resolve(name=SCENE_NAME, size_bytes=self.size + 1)
        self.assertEqual(r.status_code, 404)
        detail = r.json()["detail"]
        self.assertIn("字节数", detail)
        self.assertIn(str(self.size), detail)       # 报错要给出盘阵那一侧的数
        self.assertIn(str(self.size + 1), detail)

    def test_lookalike_name_is_rejected(self):
        """名字那一半的钉子（本方案最要紧的一条）。

        RC 形态的目录里输入影像是 `PAN.tif`（`<目录名>.tif` 不存在），
        `input_scene_path` 返回的就是它。若用户拖进来的是别处一张**恰好同字节
        数**的 `<场景名>.tif`，只比字节数就会关联成功 —— 于是掩码按拖进来那张
        画，SR 却在盘阵上按 `PAN.tif` 跑，坐标整片错位。名字对不上一律不认。
        """
        self.tif.unlink()                       # 只留 PAN.tif
        d = self.scene_dir
        make_scene(d, SCENE_NAME, tif=False)
        arr = (np.arange(320 * 640).reshape(640, 320) % 65535).astype(np.uint16)
        pan = d / "PAN.tif"
        tifffile.imwrite(pan, arr, photometric="minisblack")

        r = self.resolve(name=SCENE_NAME, size_bytes=pan.stat().st_size)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("不是同一个文件", r.json()["detail"])
        # 不带上限的裸调用（老行为）仍然打得开 —— 这条守卫只针对带指纹的入口
        self.assertEqual(self.resolve(name=SCENE_NAME).status_code, 200)

    def test_jpg_name_skips_size_check(self):
        """拖盘阵上那份 `<场景名>.jpg` 也要能关联（查看器的 jpg 拖拽入口）。

        盘阵目录里的 jpg 是**另一份产物**（8bit 显示就绪的预览，见
        `scene_search._IMAGE_EXTS`），与输入的 TIF 不可能同字节 —— 字节数那一半
        对 JPEG 无意义，比下去只会把这条入口恒堵死。名字那一半仍然成立，只是比的
        是**场景目录名**（见下一条），所以仍算同一个场景，lq_path 照给。
        """
        jpg = self.d / f"{SCENE_NAME}.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xd9")        # 内容不参与判定，只走名字+尺寸
        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["lq_path"], self.d.as_posix())

    def test_jpg_matches_dir_name_even_when_input_is_pan(self):
        """钉子：纯 RC 场景（目录里只有 `PAN.tif`）拖 `<场景名>.jpg` 必须命中。

        这一条 2026-09-18 复现出来的 bug：拖 .jpg 这条入口**恰好在 SR 真要跑的
        场景上恒 404**。原因是 jpg 名被拿去和**栅格输入**的 stem 比，而纯 RC 目录里
        `input_scene_path` 返回的是 `PAN.tif`（`inp.stem == "PAN"`），显示件却是
        生产全名，永远比不过 —— 于是「极少出现盘阵小标」：只有目录里恰好躺着
        `<目录名>.tif`（SC 场景，或 RC 目录留着上游 SC 产物）时才关联得上。

        正确判据是 jpg 名 == **场景目录名**（生产全名 = `<目录名>_meta.xml` 的前缀）。
        """
        self.tif.unlink()                           # 只留 PAN.tif：纯 RC
        arr = (np.arange(320 * 640).reshape(640, 320) % 65535).astype(np.uint16)
        tifffile.imwrite(self.d / "PAN.tif", arr, photometric="minisblack")
        jpg = self.d / f"{SCENE_NAME}.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xd9")
        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["row"]["lq_path"], self.d.as_posix())
        # 同一目录下，栅格那条名字门照旧拦着 PAN.tif 之外的同名字节相同件
        self.assertEqual(self.resolve(name=SCENE_NAME,
                                     size_bytes=self.size).status_code, 404)

    def test_derived_and_renamed_jpg_404_never_link(self):
        """派生件 / 副本 / 别处导出的 jpg：一个都不许关联上。

        `_cloud.jpg`（云量图）、`.preview.jpg`（平台自己烤的缓存）、` - 副本.jpg`
        这些名字**带着时间戳**，所以能走到候选判定这一层；但它们手里的「生产名」
        是残的（后缀粘在最后一段上，或整个目录名对不上），反推出来的目录在盘阵上
        不存在 → 404，一条候选都命中不了。

        注意它们**不是**被「名字不一致」那一关挡下的：默认模板下场景目录名就是由
        这个名字（去后缀）得来的，dir 存在时 `want == d.name` 必然成立 —— 名字那
        一关是给「换了 SR_SCENE_PATH_TEMPLATE、目录改了命名」的部署留的守卫，在
        默认部署里不发力。这里真正挡住它们的是「目录不存在」。
        """
        for bad in (f"{SCENE_NAME}_cloud.jpg", f"{SCENE_NAME}.preview.jpg",
                    f"{SCENE_NAME} - 副本.jpg"):
            with self.subTest(bad=bad):
                r = self.resolve(name=bad, size_bytes=12345)
                self.assertEqual(r.status_code, 404, r.text)
                self.assertIn("目录不存在", r.json()["detail"])

    def test_jpg_without_timestamp_400_says_what_to_drag(self):
        """名字里没有生产全名的 jpg：400，且要说清「平台不猜目录、该拖哪一份」。

        `PAN.jpg` 这类名字里没有 14 位成像时刻 —— 反推路径的唯一依据就是文件名，
        拿不到日期就不知道该去 `<年>/<月>/<日>` 哪一天找。**绝不猜**（猜错就是拿
        另一景的 lq_path 去提交），所以只能如实说认不出来。
        """
        r = self.resolve(name="PAN.jpg", size_bytes=12345)
        self.assertEqual(r.status_code, 400, r.text)
        detail = r.json()["detail"]
        self.assertIn("不猜目录", detail)
        self.assertIn("<目录名>.jpg", detail)
        # 栅格那条 400 的措辞不受影响（它是「没有时间戳」，不是「不像生产全名」）
        r2 = self.resolve(name="local_nodate.tif", size_bytes=12345)
        self.assertEqual(r2.status_code, 400)
        self.assertIn("14/8 位成像时间戳", r2.json()["detail"])

    def test_other_raster_suffix_still_checks_size(self):
        """放行的是「拖进来的是 JPEG」这一件事，不是「后缀跟盘阵不一样」。

        泛化成后者的话，本机另存过一份 `<场景名>.tiff`（与盘阵的 `.tif` 同 stem
        不同字节）也会被认成同一个场景 —— 而那正是字节数这一半要挡的。
        """
        r = self.resolve(name=SCENE_NAME + ".tiff", size_bytes=self.size + 1)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("字节数", r.json()["detail"])

    def test_nonpositive_size_400(self):
        for bad in (0, -1, True):
            r = self.resolve(name=SCENE_NAME, size_bytes=bad)
            self.assertEqual(r.status_code, 400, f"{bad!r} → {r.text}")
            self.assertIn("size_bytes", r.json()["detail"])

    def test_non_integer_size_400(self):
        r = self.resolve(name=SCENE_NAME, size_bytes="2560256")
        self.assertEqual(r.status_code, 400)

    def test_path_branch_ignores_size_bytes(self):
        """`{path}` 是精确路径，天然没有冒名问题 —— 带不带指纹都开得了。"""
        r = self.resolve(path=self.win_path(self.d), size_bytes=1)
        self.assertEqual(r.status_code, 200, r.text)


class TestResolveStages(ResolveBase):
    """拖进来的 jpg 属于哪个环节：本体 / 本次产物 / 上一次产物（2026-09-21）。

    用户口径：一景里只有**本体**是可修复对象（SR 与掩码都建在它的网格上），中间
    产物只要关联到盘阵、标出环节、明说「仅对比不作修复」。所以这一节钉两件事：

    1. 产物 / 上一次产物的名字都要**认得出来**（suffix 从文件名本身切，不查任务库）；
    2. 认出来之后这一行描述的是**产物自己**（W/H 是它那张栅格的），而
       `lq_path` / `sr_capable` / `mask_path` 一律**摘掉** —— 否则前端会拿产物的
       W/H 配本体的 lq_path，把一张产物尺寸的掩码写到本体的掩码文件上。
    """

    #: 产物那张栅格用的尺寸，刻意与本体（320×640）不同：判「这一行描述谁」全靠它。
    PROD_W, PROD_H = 640, 1280

    def _write_jpg(self, path: Path, w: int = 64, h: int = 32) -> Path:
        arr = (np.arange(w * h).reshape(h, w) % 256).astype(np.uint8)
        Image.fromarray(arr).save(path, quality=90)
        return path

    def _write_raster(self, path: Path, w: int, h: int) -> Path:
        arr = (np.arange(w * h).reshape(h, w) % 65535).astype(np.uint16)
        tifffile.imwrite(path, arr, photometric="minisblack")
        return path

    def resolve(self, **body):
        return self.client().post("/api/scenes/resolve", json=body)

    def _make_product(self, suffix: str, nosr: bool = False):
        """本体场景 + 一份 `<目录名>_<suffix>[_NOSR]` 的栅格与显示件 jpg。"""
        d = self.make_scene()
        name = f"{SCENE_NAME}_{suffix}" + ("_NOSR" if nosr else "")
        raster = self._write_raster(d / f"{name}.tif", self.PROD_W, self.PROD_H)
        jpg = self._write_jpg(d / f"{name}.jpg")
        return d, name, raster, jpg

    def test_product_jpg_links_but_cannot_repair(self):
        """本次产物：认得出、描述的是它自己、但**没有**可提交的落点。"""
        d, name, raster, jpg = self._make_product("sr")

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["resolved"]["kind"], "product")
        self.assertEqual(body["resolved"]["suffix"], "sr")
        # 这一行描述**产物自己**：id/名字/尺寸都指向那份 `_sr.tif`
        row = body["row"]
        self.assertEqual(row["name"], name)
        self.assertEqual((row["W"], row["H"]), (self.PROD_W, self.PROD_H))
        self.assertEqual(row["size_bytes"], raster.stat().st_size)
        # 但「能不能提交 / 掩码写哪」三件全摘 —— 见类注释第 2 条
        self.assertIsNone(row["lq_path"], "产物没有可提交的 lq_path")
        self.assertFalse(body["resolved"]["sr_capable"])
        self.assertIsNone(body["resolved"]["mask_path"])
        self.assertFalse(body["resolved"]["mask_exists"])
        # 本体那一路仍然如实回报（SR 真要跑的是它）
        self.assertEqual(body["resolved"]["input_name"], f"{SCENE_NAME}.tif")
        self.assertEqual(body["resolved"]["dir"], d.as_posix())

    def test_nosr_jpg_is_the_previous_product(self):
        """上一次产物：`<目录名>_<suffix>_NOSR` —— 环节是 nosr，suffix 仍是那一段。"""
        _, name, _, jpg = self._make_product("sr", nosr=True)

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["resolved"]["kind"], "nosr")
        self.assertEqual(r.json()["resolved"]["suffix"], "sr")
        self.assertIsNone(r.json()["row"]["lq_path"])

    def test_suffix_with_underscore_is_cut_whole(self):
        """带下划线的 suffix（`sr_2`）**整段**切，不按段数猜。

        按段数猜（去一段）会切出 `_2`、反推出一个叫 `…_sr` 的目录；真机上的 suffix
        是用户自定的短串（`SR_code` 侧只限 `[A-Za-z0-9_-]{1,16}`），带下划线完全合法。
        """
        d, name, _, jpg = self._make_product("sr_2")

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["resolved"]["suffix"], "sr_2")
        self.assertEqual(r.json()["row"]["name"], name)
        self.assertEqual(r.json()["row"]["lq_path"] is None, True)

    def test_product_jpg_without_its_raster_404(self):
        """**真门**：同名栅格不在 → 不认这一份。

        这条同时挡住「切得干净就算数」那种松判据：`<目录名>_sr.jpg` 的名字确实能
        切出本景目录名（本体目录是存在的），但盘阵上并没有 `<目录名>_sr.tif` ——
        那份 jpg 是别处拿来的图，不是本景的产物。此时不得退回按本体关联：用户看的
        是产物，却拿到本体的尺寸与可提交入口，掩码坐标整片错位。
        """
        d = self.make_scene()
        jpg = self._write_jpg(d / f"{SCENE_NAME}_sr.jpg")

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("不是本景的输入件或中间产物", r.json()["detail"])

    def test_cloud_jpg_with_a_raster_still_404(self):
        """云量图这类派生件：**即便同名栅格真的在**，也不认它是产物。

        `_cloud` 与 `_thumb` 在真机目录里是真有栅格的（`scene_search` 的文档里点名
        过），只靠「同级栅格在」挡不住它们 —— 那一小张显式名单就是这么来的。
        """
        d = self.make_scene()
        self._write_raster(d / f"{SCENE_NAME}_cloud.tif", 160, 320)
        jpg = self._write_jpg(d / f"{SCENE_NAME}_cloud.jpg")

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 404, r.text)

    def test_own_preview_jpg_links_as_the_input(self):
        """平台自己烤的 `<目录名>_preview.jpg` 拖回来 ≡ 拖本体的显示件。

        `paths.drop_preview_path` 把这份预览落在**场景目录里**，于是它长得跟一份
        「场景里的 jpg」一样。剥掉 `_preview` 得到的正好是场景目录名，所以这一行
        与拖 `<目录名>.jpg` 同解：可提交 SR、掩码写本体。此前它恒 404，报错还列了
        两条自己拼出来的假路径（`…_preview/…_preview`）—— 平台烤的文件平台自己不认。
        """
        d = self.make_scene()                                  # 含 `<目录名>.tif` + meta
        preview = self._write_jpg(d / f"{SCENE_NAME}_preview.jpg")

        r = self.resolve(name=preview.name, size_bytes=preview.stat().st_size)

        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["resolved"]["kind"], "input")
        self.assertEqual(body["resolved"]["suffix"], "")
        self.assertTrue(body["resolved"]["sr_capable"])
        self.assertEqual(body["resolved"]["dir"], d.as_posix())
        self.assertEqual(body["row"]["lq_path"], d.as_posix())
        self.assertEqual(body["row"]["name"], SCENE_NAME)

    def test_product_preview_jpg_links_as_that_product(self):
        """产物的预览 `<目录名>_sr_preview.jpg` → 仍是**产物**那一行，不是本体。

        剥 `_preview` 只剥一层，剥完是 `<目录名>_sr`（产物的栅格 stem）—— 环节由
        `stage_of_jpg` 照旧判成 product，于是 `lq_path` 仍是空的、提交按钮仍该是灰的。
        """
        d, name, raster, _ = self._make_product("sr")
        preview = self._write_jpg(d / f"{name}_preview.jpg")

        r = self.resolve(name=preview.name, size_bytes=preview.stat().st_size)

        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["resolved"]["kind"], "product")
        self.assertEqual(body["resolved"]["suffix"], "sr")
        self.assertEqual(body["row"]["name"], name)
        self.assertEqual((body["row"]["W"], body["row"]["H"]),
                         (self.PROD_W, self.PROD_H))
        self.assertIsNone(body["row"]["lq_path"])
        self.assertFalse(body["resolved"]["sr_capable"])

    def test_cloud_with_a_preview_tail_still_404(self):
        """界线：剥掉 `preview` 之后还是黑名单尾巴 → 照旧不认。

        `<目录名>_cloud_preview.jpg` 剥一层得到 `<目录名>_cloud`，`cloud` 仍在
        `_NON_STAGE_TAILS` 里。**只剥 `preview` 那一层**，不因为名单里放行了一个就
        整份名单失效 —— 否则云量图会顺着这条路被认成本体的显示件。
        """
        d = self.make_scene()
        self._write_raster(d / f"{SCENE_NAME}_cloud.tif", 160, 320)
        jpg = self._write_jpg(d / f"{SCENE_NAME}_cloud_preview.jpg")

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("不是本景的输入件或中间产物", r.json()["detail"])

    def test_preview_of_a_missing_scene_still_404(self):
        """`_preview` 只是个尾巴，不是通行证：那景真不在盘阵上仍是 404。"""
        d = self.make_scene()
        missing = f"{SCENE_NAME[:-3]}009_preview.jpg"
        jpg = self._write_jpg(d / missing)

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("目录不存在", r.json()["detail"])

    def test_input_jpg_keeps_every_right(self):
        """本体（显示件）一切照旧：可提交、有掩码路径 —— 这条改动不许动它。"""
        d = self.make_scene()
        jpg = self._write_jpg(d / f"{SCENE_NAME}.jpg")

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["resolved"]["kind"], "input")
        self.assertEqual(body["resolved"]["suffix"], "")
        self.assertTrue(body["resolved"]["sr_capable"])
        self.assertEqual(body["row"]["lq_path"], d.as_posix())
        self.assertEqual(body["row"]["name"], SCENE_NAME)
        self.assertTrue(body["resolved"]["mask_path"].endswith(f"{SCENE_NAME}_mask.tif"))

    def test_pan_display_jpg_is_input_not_product(self):
        """RC 场景的 `PAN.jpg` 是**本体**的显示件，不是 `PAN` 这个 suffix 的产物。"""
        d = self.make_scene(tif=False)
        self._write_raster(d / "PAN.tif", 320, 640)
        jpg = self._write_jpg(d / "PAN.jpg")
        # PAN.jpg 这个名字里没有生产全名 → 前端本来就送不到这里；
        # 但真按名字给到（例如换过模板的部署），也不该被认成产物。
        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)
        self.assertEqual(r.status_code, 400, r.text)   # 无时间戳，猜不出是哪一天
        self.assertIn("不猜目录", r.json()["detail"])

    def test_failed_product_name_probes_are_bounded(self):
        """第二阶段（去掉尾段再反推）也要**定数探测**，不随目录内容增长。

        与 `TestNeverListsDirectories` 同一口径，补的是新增的那条路：产物名第一
        阶段必然落空（`…/<目录名>_sr` 这个目录不存在），于是会去试去掉尾段的候选。
        那条路照样只拼固定名字 + `is_file`，50 个噪声文件一个都不该被 stat。
        """
        d = self.make_scene()
        for i in range(50):
            (d / f"noise_{i}.dat").write_bytes(b"")
        jpg = self._write_jpg(d / f"{SCENE_NAME}_sr.jpg")      # 没有同名栅格 → 404
        c = self.client()
        real_stat = Path.stat
        calls: list[str] = []

        def counting_stat(self, *a, **kw):
            calls.append(self.name)
            return real_stat(self, *a, **kw)

        with ExitStack() as st:
            for name in ("rglob", "glob", "iterdir"):
                st.enter_context(mock.patch.object(
                    Path, name,
                    side_effect=AssertionError(f"禁止 Path.{name}（扫盘）")))
            for name in ("listdir", "scandir", "walk"):
                st.enter_context(mock.patch.object(
                    os, name,
                    side_effect=AssertionError(f"禁止 os.{name}（扫盘）")))
            st.enter_context(mock.patch.object(Path, "stat", counting_stat))
            r = c.post("/api/scenes/resolve",
                       json={"name": jpg.name, "size_bytes": jpg.stat().st_size})

        self.assertEqual(r.status_code, 404, r.text)
        self.assertNotIn("noise_0.dat", calls)
        self.assertLessEqual(len(calls), 30, calls)

    def test_preview_name_probes_are_bounded(self):
        """`_preview` 那条路（剥离 + 去尾）同样定数探测，不随目录内容增长。

        与上面那条同一口径：这一份名字以 `_preview` 结尾，剥掉后还要切一段才轮到
        真目录名，是候选最多的一条路（相位一 2 条 + 去尾后 4 条，共 6 个候选目录）。
        本条实测 23 次，上限放到 30 留余量。真正要挡的是「随目录内容增长」—— 50 个
        噪声文件一个都不该被 stat，探测只由候选名字决定。
        """
        d = self.make_scene()
        for i in range(50):
            (d / f"noise_{i}.dat").write_bytes(b"")
        jpg = self._write_jpg(d / f"{SCENE_NAME[:-3]}009_preview.jpg")   # 那景不存在
        c = self.client()
        real_stat = Path.stat
        calls: list[str] = []

        def counting_stat(self, *a, **kw):
            calls.append(self.name)
            return real_stat(self, *a, **kw)

        with ExitStack() as st:
            st.enter_context(mock.patch.object(Path, "stat", counting_stat))
            r = c.post("/api/scenes/resolve",
                       json={"name": jpg.name, "size_bytes": jpg.stat().st_size})

        self.assertEqual(r.status_code, 404, r.text)
        self.assertNotIn("noise_0.dat", calls)
        self.assertLessEqual(len(calls), 30, calls)

    def test_short_name_candidates_do_not_blow_up_the_request(self):
        """名字短到「去尾之后拆不出生产层」时，那两条候选各走各的（跳过），整个
        请求不能被它们拖垮。

        实测（2026-09-21 浏览器回归）：七段的名字去一次尾就剩六段，
        `scene_name_layers` 在 `seps[_SCENE_IDX]` 上越界 —— 本该 200 的拖入回到
        前端只有一句 `Failed to fetch`。这里两个方向都钉：命中的那条（本体 jpg）
        照旧 200，落空的那条（同名产物名）照旧 404，都不许变成 500。
        """
        name = "A_B_20260917124710_200536960_101_0005_001"
        self.assertEqual(len(name.split("_")), 7)
        d = make_scene(
            self.sat_dir / "A_B_20260917124710_200536960_101_001" / name, name)
        jpg = self._write_jpg(d / f"{name}.jpg")

        r = self.resolve(name=jpg.name, size_bytes=jpg.stat().st_size)

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["resolved"]["kind"], "input")
        self.assertEqual(r.json()["resolved"]["dir"], d.as_posix())

        # 同一个名字换掉最后一段（盘阵上没有那景）→ 仍要是一个干净的 404
        missing = name[:-3] + "002.jpg"
        r2 = self.resolve(name=missing, size_bytes=jpg.stat().st_size)
        self.assertEqual(r2.status_code, 404, r2.text)
        self.assertIn("目录不存在", r2.json()["detail"])


class TestDropPreview(ResolveBase):
    """`/preview-drop`：拖入链的预览 —— **写进生产场景目录**，落不下才退回临时缓存。"""

    def setUp(self):
        super().setUp()
        self.tmp_root = self.root / "tmp-previews"
        os.environ["SR_TEMP_PREVIEWS_ROOT"] = str(self.tmp_root)
        self.d = self.make_scene()
        self.tif = self.d / f"{SCENE_NAME}.tif"
        self.drop_jpg = self.d / f"{SCENE_NAME}_preview.jpg"

    def scene_id_of_manual(self, c) -> str:
        return c.post("/api/scenes/resolve",
                      json={"path": self.win_path(self.d)}).json()["row"]["id"]

    def tmp_bucket(self) -> Path:
        return self.tmp_root / date.today().isoformat()

    def test_bakes_into_scene_dir(self):
        """核心：拖入链的产物落在**源同目录**、叫 `<stem>_preview.jpg`。"""
        c = self.client()
        r = c.get(f"/api/scenes/{self.scene_id_of_manual(c)}/preview-drop")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertTrue(self.drop_jpg.is_file())
        # 没有走兜底
        self.assertNotIn("x-sr-preview-fallback", r.headers)
        # 不写平台自己那份缓存，也不碰临时桶
        self.assertFalse((self.d / f"{SCENE_NAME}.preview.jpg").exists())
        self.assertFalse(self.tmp_bucket().exists())

    def test_second_request_hits_cache(self):
        """第二次不再重烤：mtime 不变（烘焙一次大图很贵，别每次都来）。"""
        c = self.client()
        sid = self.scene_id_of_manual(c)
        c.get(f"/api/scenes/{sid}/preview-drop")
        first = self.drop_jpg.stat().st_mtime_ns
        self.assertEqual(c.get(f"/api/scenes/{sid}/preview-drop").status_code, 200)
        self.assertEqual(self.drop_jpg.stat().st_mtime_ns, first)

    def test_div_change_rebakes_in_place(self):
        """换档位 → **同一个落点原地重烤**（尺寸变了，文件数不变）。

        这是「div 进戳」的直接验收：只按落点判缓存的话，这里第二次会命中、
        盘上那张图一个字节都不变。
        """
        c = self.client()
        sid = self.scene_id_of_manual(c)
        c.get(f"/api/scenes/{sid}/preview-drop?div=2")
        with Image.open(self.drop_jpg) as im:
            self.assertEqual(im.size, (160, 320))
        c.get(f"/api/scenes/{sid}/preview-drop?div=8")
        with Image.open(self.drop_jpg) as im:
            self.assertEqual(im.size, (40, 80))
        self.assertEqual(len(list(self.d.glob("*_preview.jpg"))), 1)

    def test_each_div_bakes_its_own_size(self):
        """档位维度的尺寸表：源 320×640（竖幅），长边 = 640/div。"""
        c = self.client()
        sid = self.scene_id_of_manual(c)
        for div, (w, h) in ((2, (160, 320)), (4, (80, 160)), (8, (40, 80)),
                            (16, (20, 40)), (32, (10, 20))):
            with self.subTest(div=div):
                self.assertEqual(
                    c.get(f"/api/scenes/{sid}/preview-drop?div={div}").status_code,
                    200, f"div={div}")
                with Image.open(self.drop_jpg) as im:
                    self.assertEqual(im.size, (w, h))

    def test_invalid_div_400(self):
        """非法档位一律 400（三条入口同一套校验），且不落任何文件。"""
        c = self.client()
        sid = self.scene_id_of_manual(c)
        for bad in (0, 1, 3, 64, -4):
            with self.subTest(div=bad):
                r = c.get(f"/api/scenes/{sid}/preview-drop?div={bad}")
                self.assertEqual(r.status_code, 400, r.text)
        self.assertFalse(self.drop_jpg.exists())

    def test_fallback_to_tmp_when_scene_dir_unwritable(self):
        """场景目录不可写 → 退回临时缓存，回响应头如实说明。"""
        c = self.client()
        sid = self.scene_id_of_manual(c)
        real_access = os.access
        with mock.patch.object(
                os, "access",
                side_effect=lambda p, m, **kw: (False if Path(p) == self.d
                                                else real_access(p, m, **kw))):
            r = c.get(f"/api/scenes/{sid}/preview-drop")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["x-sr-preview-fallback"], "tmp")
        self.assertFalse(self.drop_jpg.exists())
        self.assertTrue(list(self.tmp_bucket().glob("*.jpg")))

    def test_both_paths_fail_422(self):
        """两条落点都写不进去 → 422，而不是 500 逃出去。"""
        from backend.api import app as app_mod
        c = self.client()
        sid = self.scene_id_of_manual(c)
        real_access = os.access
        with mock.patch.object(
                os, "access",
                side_effect=lambda p, m, **kw: (False if Path(p) == self.d
                                                else real_access(p, m, **kw))), \
                mock.patch.object(app_mod, "ensure_preview_jpg",
                                  side_effect=OSError("磁盘满")):
            r = c.get(f"/api/scenes/{sid}/preview-drop")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("预览生成失败", r.json()["detail"])

    def test_jpeg_source_short_circuits(self):
        """源本身就是 JPG（显示就绪图）：回源文件，不尝试烘焙 ——
        `build_preview_pixels` 只认 TIFF，不给这行短路就会 422。"""
        jpg_scene = self.d / "display.jpg"
        Image.new("L", (32, 32), 7).save(jpg_scene)
        r = self.client().get(
            f"/api/scenes/{scene_id_abs(jpg_scene)}/preview-drop")
        self.assertEqual(r.status_code, 200, r.text)

    def test_outside_whitelist_404(self):
        outside = Path("/etc/passwd") if os.name != "nt" else Path("C:/Windows/win.ini")
        r = self.client().get(f"/api/scenes/{scene_id_abs(outside)}/preview-drop")
        self.assertEqual(r.status_code, 404)

    def test_drop_preview_is_not_listed_as_a_scene(self):
        """`<stem>_preview.jpg` 不会在场景库里多出一行。

        `scene_search.is_scene_file` 是「文件名 == 目录名 或 PAN」的白名单，
        下划线名本来就拒 —— 这条把它钉住，免得日后有人把白名单放宽。
        """
        c = self.client()
        self.assertEqual(c.get(f"/api/scenes/{self.scene_id_of_manual(c)}"
                               "/preview-drop").status_code, 200)
        self.assertTrue(self.drop_jpg.is_file())
        names = [r["name"] for r in
                 self._listed_rows(self.root)]
        self.assertNotIn(f"{SCENE_NAME}_preview", names)

    def _listed_rows(self, root: Path) -> list[dict]:
        os.environ["SR_SCENES_ROOT"] = str(root)
        try:
            return self.client().get("/api/scenes").json()["results"]
        finally:
            os.environ.pop("SR_SCENES_ROOT", None)

    def test_never_lists_directories(self):
        """禁止扫盘的钉子同样管着这条链：落点只 mkdir + stat + os.access。"""
        c = self.client()
        sid = self.scene_id_of_manual(c)
        with ExitStack() as st:
            for name in ("rglob", "glob", "iterdir"):
                st.enter_context(mock.patch.object(
                    Path, name,
                    side_effect=AssertionError(f"禁止 Path.{name}（扫盘）")))
            for name in ("listdir", "scandir", "walk"):
                st.enter_context(mock.patch.object(
                    os, name,
                    side_effect=AssertionError(f"禁止 os.{name}（扫盘）")))
            r = c.get(f"/api/scenes/{sid}/preview-drop")
            self.assertEqual(r.status_code, 200, r.text)


class TestResolveRasterPreview(ResolveBase):
    """显示件 jpg 的「同名栅格」比较依据（api-contract 的「显示源比较规则」）。

    两条入口各自算一份，因为它们的 `row` 来源不同：
      * **库行**（`/api/scenes` 列表，`<目录名>.jpg` 是白名单内的场景文件）：
        `_scene_row` 的 jpg 短路分支里按**这份 jpg 自己**算；
      * **拖拽**（`POST /api/scenes/resolve {name}`）：`row` 描述的是**栅格**输入
        影像，所以那句按 jpg 源算的逻辑必然为 None，得单独按**盘阵那张 jpg** 补。

    比较所需的两个尺寸都从后端读（前端拿不到盘阵文件，也不该为此多发一次请求）。

    这里同时钉住一条**红线**：`hasPreview / jpgUrl / previewDiv` 的语义一个字都不
    变 —— jpg 行的显示源仍是那张 jpg，`rasterPreview` 是**另加**的一路参考
    （gui-experience §9.2，也是 e2e `test-scenes.js` 的「JPG 源」判据）。
    """

    def _write_jpg(self, path: Path, w: int, h: int) -> Path:
        arr = (np.arange(w * h).reshape(h, w) % 256).astype(np.uint8)
        Image.fromarray(arr).save(path, quality=90)
        return path

    def _library_row(self, name: str, ext: str = ".tif") -> dict:
        """把临时根当场景库列出，取 `<name><ext>` 那一行。

        **必须带后缀筛**：同一目录里 `<目录名>.tif` 与 `<目录名>.jpg` 都在白名单内
        （`is_scene_file` 判的是 stem == 目录名），所以库里本来就是**两行同名**的
        场景 —— 这不本轮去重（计划 §六.4 留作下轮议题），但取行时得说清要哪一份。
        `rel` 含文件名，是唯一能分辨两者的字段。
        """
        os.environ["SR_SCENES_ROOT"] = str(self.root)
        try:
            rows = self.client().get("/api/scenes").json()["results"]
        finally:
            os.environ.pop("SR_SCENES_ROOT", None)
        hit = [r for r in rows
               if r["name"] == name and (r["rel"] or "").endswith(ext)]
        self.assertEqual(len(hit), 1,
                         f"{name}{ext} 该恰好一行：{[(r['name'], r['rel']) for r in rows]}")
        return hit[0]

    # ---- 库行入口 ---------------------------------------------------------

    def test_library_jpg_row_carries_the_comparison(self):
        """盘阵显示件（320×640 的栅格配 64×32 的 `<目录名>.jpg`）→ 都交给前端比。"""
        d = self.make_scene()
        self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)

        row = self._library_row(SCENE_NAME, ".jpg")

        rp = row["rasterPreview"]
        self.assertIsNotNone(rp, "同目录有同名栅格 → 必须给出比较依据")
        self.assertEqual(rp["name"], f"{SCENE_NAME}.tif")
        self.assertEqual((rp["rasterW"], rp["rasterH"]), (320, 640))
        self.assertEqual((rp["jpgW"], rp["jpgH"]), (64, 32))
        self.assertFalse(rp["hasPreview"], "还没人烤过，前端据此提示首次较慢")

    def test_library_jpg_row_keeps_its_own_display_source(self):
        """**红线**：加了 rasterPreview 之后，这一行自己的三个字段语义不变。

        jpg 行的显示源就是那张 jpg（`hasPreview=True` / `jpgUrl` 直指源文件 /
        `previewDiv` 留 None —— 它不是烤出来的预览）。`rasterPreview` 是**另加**的
        一路参考，不是把这三个字段改指向栅格。
        """
        d = self.make_scene()
        self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)

        row = self._library_row(SCENE_NAME, ".jpg")

        self.assertTrue(row["hasPreview"], "还是那句「jpg 就是显示源」")
        self.assertTrue(row["jpgUrl"].endswith(f"{SCENE_NAME}.jpg"))
        self.assertFalse(row["jpgUrl"].endswith(".preview.jpg"))
        self.assertIsNone(row["previewDiv"])
        # 栅格那一路是**另**一个 id（指向 .tif），不是这一行的 id
        self.assertNotEqual(row["rasterPreview"]["id"], row["id"])

    def test_library_jpg_row_without_a_sibling_raster_is_null(self):
        """**没有配套 .tif 就回退显示 jpg 本身**：rasterPreview 为 null，前端照旧。"""
        d = self.make_scene(tif=False)
        self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)

        row = self._library_row(SCENE_NAME, ".jpg")

        self.assertIsNone(row["rasterPreview"])
        self.assertTrue(row["hasPreview"], "退化了也还是能直接看那张 jpg")

    def test_library_raster_row_has_no_raster_preview(self):
        """栅格行不需要它 —— 它自己就是那条「从栅格烤」的路。"""
        self.make_scene()
        row = self._library_row(SCENE_NAME, ".tif")
        self.assertIsNone(row["rasterPreview"])
        # 栅格行的 jpgUrl 指的是烤出来的预览（与上面 jpg 行正好相反）
        self.assertTrue(row["jpgUrl"].endswith(f"{SCENE_NAME}.preview.jpg"))

    # ---- 拖拽入口 ---------------------------------------------------------

    def test_dragged_jpg_gets_the_comparison_against_the_array_jpg(self):
        """拖 `<目录名>.jpg` 进来时 `row` 描述的是**栅格**输入影像（id/W/H/hasPreview
        全指向那份 tif），所以比较依据得单独按**盘阵那张 jpg** 补。

        这一条钉的正是「拖 jpg 这条路走不通」的实现错法：把 `_manual_row(inp, ...)`
        里那句按 jpg 源算的逻辑当成答案，它必然是 None（`inp` 是 .tif），于是前端
        永远拿不到比较依据、永远显示那张不够清晰的 jpg。
        """
        d = self.make_scene()
        jpg = self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)
        r = self.client().post("/api/scenes/resolve",
                               json={"name": jpg.name, "size_bytes": jpg.stat().st_size})
        self.assertEqual(r.status_code, 200, r.text)
        row = r.json()["row"]

        # 这一行仍然是栅格行（红线：row 的来源不变）
        self.assertEqual(row["name"], SCENE_NAME)
        self.assertNotEqual(row["rasterPreview"], None)
        self.assertEqual((row["W"], row["H"]), (320, 640))

        rp = row["rasterPreview"]
        self.assertEqual((rp["rasterW"], rp["rasterH"]), (320, 640), "栅格那一侧")
        self.assertEqual((rp["jpgW"], rp["jpgH"]), (64, 32),
                         "盘阵那张 jpg 的尺寸，不是用户本地那份")

    def test_dragged_jpg_uses_the_array_copy_not_the_local_one(self):
        """**必须量盘阵那张 jpg**：指纹对 jpg 只比名字（不比字节数），所以用户本地
        那份可能是另存过、缩过的 —— 拿它来比会把「服务端更清晰」判反。

        这里让本地那份**报一个更大的尺寸**（名字相同），盘阵那份仍是 64×32：
        比较结果必须是按盘阵那份算出来的。
        """
        d = self.make_scene()
        local = self._write_jpg(Path(self._tmp.name) / "local_copy.jpg", 300, 600)
        self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)     # 盘阵那份
        r = self.client().post(
            "/api/scenes/resolve",
            json={"name": f"{SCENE_NAME}.jpg", "size_bytes": local.stat().st_size})
        self.assertEqual(r.status_code, 200, r.text)

        rp = r.json()["row"]["rasterPreview"]
        self.assertEqual((rp["jpgW"], rp["jpgH"]), (64, 32),
                         "量的是盘阵那份（本地那份是 300×600）")

    def test_dragged_jpg_missing_on_the_array_falls_back_to_local(self):
        """盘阵上那份 jpg 被删/改名（用户手上只有本地副本）→ 判不出就不换图。

        以前这里会 500：`_cached_dims` 上来就 stat，文件不在就 FileNotFoundError。
        该退化成「继续显示本地那份」——保守方向是对的。
        """
        d = self.make_scene()
        local = self._write_jpg(Path(self._tmp.name) / "only_local.jpg", 64, 32)
        r = self.client().post(
            "/api/scenes/resolve",
            json={"name": f"{SCENE_NAME}.jpg", "size_bytes": local.stat().st_size})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["row"]["rasterPreview"])

    def test_dragged_jpg_needs_a_raster_to_resolve_at_all(self):
        """拖 jpg 这条入口**只在有栅格输入的目录上成立** —— 反推命中要求
        `input_scene_path(d)` 非空（SR 的输入影像），所以「拖 jpg 但目录里没有配套
        栅格」这个组合压根到不了栅格比较那一步：resolve 自己 404。

        换句话说，拖拽路径上的 `rasterPreview` 只要 resolve 成功就必然非空
        （除非盘阵那份 jpg 不在，见上一条）——「没有配套 .tif 就回退显示 jpg」那条
        规则在**库行**入口上才见得到。
        """
        d = self.make_scene(tif=False)
        jpg = self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)
        r = self.client().post("/api/scenes/resolve",
                               json={"name": jpg.name, "size_bytes": jpg.stat().st_size})
        self.assertEqual(r.status_code, 404, r.text)

    def test_dragged_tif_is_unaffected(self):
        """拖 .tif 那条入口一个字不改（rasterPreview 恒 null，走的是老路）。"""
        d = self.make_scene()
        self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)
        tif = d / f"{SCENE_NAME}.tif"
        r = self.client().post("/api/scenes/resolve",
                               json={"name": tif.name, "size_bytes": tif.stat().st_size})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["row"]["rasterPreview"])

    def test_dragged_jpg_resolve_probes_without_listing(self):
        """新增的那几次探测（找同名栅格）同样只拼名字 + `is_file`，绝不扫盘。

        `sibling_raster_path` 只试 `.tif/.tiff/.img` 三个固定后缀 —— 与
        `TestNeverListsDirectories` 同一条约束，这里补的是**拖 jpg 这条入口**。
        """
        d = self.make_scene()
        jpg = self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)
        c = self.client()
        for i in range(50):
            (d / f"noise_{i}.dat").write_bytes(b"")
        with ExitStack() as st:
            for name in ("rglob", "glob", "iterdir"):
                st.enter_context(mock.patch.object(
                    Path, name,
                    side_effect=AssertionError(f"禁止 Path.{name}（扫盘）")))
            for name in ("listdir", "scandir", "walk"):
                st.enter_context(mock.patch.object(
                    os, name,
                    side_effect=AssertionError(f"禁止 os.{name}（扫盘）")))
            r = c.post("/api/scenes/resolve",
                       json={"name": jpg.name, "size_bytes": jpg.stat().st_size})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(r.json()["row"]["rasterPreview"])

    def test_dragged_jpg_resolve_probes_a_bounded_number_of_files(self):
        """探测次数不随目录内容增长（与 `TestNeverListsDirectories` 同一口径）。

        拖 jpg 比拖 tif 多几次探测（同名栅格的 3 个后缀 + 盘阵 jpg 的 W/H 头读），
        那些都是**固定次数**的：本条实测 25 次，上限放到 35 留余量。真正要挡的是
        「换成列举目录再筛」—— 那种改法下这个数会随那 50 个噪声文件涨上去。
        """
        d = self.make_scene()
        for i in range(50):
            (d / f"noise_{i}.dat").write_bytes(b"")
        jpg = self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)
        c = self.client()
        real_stat = Path.stat
        calls: list[str] = []

        def counting_stat(self, *a, **kw):
            calls.append(self.name)
            return real_stat(self, *a, **kw)

        with mock.patch.object(Path, "stat", counting_stat):
            r = c.post("/api/scenes/resolve",
                       json={"name": jpg.name, "size_bytes": jpg.stat().st_size})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("noise_0.dat", calls)
        self.assertLessEqual(len(calls), 35, calls)

    def test_preview_div_is_read_from_the_existing_bake(self):
        """盘上那份预览按哪一档烤的也要报出来 —— 前端据此判断要不要重烤。

        「从栅格烤」的落点与栅格行**同一份** `<stem>.preview.jpg`（`with_suffix`
        对 jpg 与 tif 是同一个文件名），所以这里烤过一次之后，jpg 行的
        `rasterPreview.hasPreview/previewDiv` 就该跟着变 —— 两行共用一个缓存，
        不会各烤一份。
        """
        d = self.make_scene()
        self._write_jpg(d / f"{SCENE_NAME}.jpg", 64, 32)
        os.environ["SR_SCENES_ROOT"] = str(self.root)
        c = self.client()
        sid = scene_id(f"GSHC2IMPS/PRODUCT/2026/09/17/{SAT_NAME}/{PROD_MID}/"
                       f"{SCENE_NAME}/{SCENE_NAME}.tif")
        for div in (2, 8):
            pv = c.get(f"/api/scenes/{sid}/preview", params={"div": div})
            self.assertEqual(pv.status_code, 200, pv.text)
            row = self._library_row(SCENE_NAME, ".jpg")
            self.assertTrue(row["rasterPreview"]["hasPreview"])
            self.assertEqual(row["rasterPreview"]["previewDiv"], div,
                             "档位从落点那份 jpg 的注释里读回来")
            # 落点与栅格行同一份（`with_suffix` 对 jpg 与 tif 是同一个文件名）
            self.assertTrue((d / f"{SCENE_NAME}.preview.jpg").is_file())


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
        rel = (f"GSHC2IMPS/PRODUCT/2026/09/17/{SAT_NAME}/{PROD_MID}/"
               f"{SCENE_NAME}/{SCENE_NAME}.tif")
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
