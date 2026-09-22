"""GET /api/scenes/{id}/siblings —— 一个场景的三类图（输入 / 本轮超分产物 / NOSR）。

这是**只读诊断端点**：把「三份各叫什么、在不在、各自的场景 id 是什么」一次交代清楚，
供查看器的对比视图直接消费；也是 `_NOSR` 拼法尚无真机实证时的核对窗口（`productCandidates`
回报试过哪些名字）。

因此本文件有两条与别处不同的重点：
  1. **永不烘焙、永不写盘** —— 断言跑完目录里没多出任何文件；
  2. **永不扫盘** —— 复用 `TestNeverListsDirectories` 那套打桩：新端点不能成为扫盘
     约束的空白区。

夹具直接复用 `test_scene_resolve` 的六层生产树（`ResolveBase`）—— 场景 id 的编解码、
白名单、`W:\\` 映射都在那条链上，另起一套只会测出另一套。
"""

import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from backend.services.run_sr import task_fingerprint
from backend.tests.test_preview_jpg import make_strip_tif
from backend.tests.test_scene_resolve import SCENE_NAME, ResolveBase

SUFFIX = "260318"
NOSR_SUFFIX = "260317"


class SiblingsBase(ResolveBase):
    """在 ResolveBase 的六层生产树上加「产物」与直调本端点的几个小工具。"""

    def resolve(self, c, d: Path) -> dict:
        r = c.post("/api/scenes/resolve", json={"path": self.win_path(d)})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["row"]

    def siblings(self, c, scene_id: str, **params):
        return c.get(f"/api/scenes/{scene_id}/siblings", params=params)

    def q(self, c, scene_id: str, **params) -> dict:
        r = self.siblings(c, scene_id, **params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def by_kind(self, body: dict, kind: str) -> dict:
        got = [i for i in body["items"] if i["kind"] == kind]
        self.assertEqual(len(got), 1, f"{kind} 恰好一项（0 = 拼不出名字，2 = 拼重了）")
        return got[0]

    def _product(self, d: Path, suffix: str):
        return make_strip_tif(d, f"{SCENE_NAME}_{suffix}.tif", 80, 40)[0]


class TestSiblingsOk(SiblingsBase):
    def test_three_kinds_all_present(self):
        d = self.make_scene()
        product = self._product(d, SUFFIX)
        nosr = self._product(d, f"{SUFFIX}_NOSR")
        c = self.client()
        row = self.resolve(c, d)

        body = self.q(c, row["id"], suffix=SUFFIX)

        self.assertEqual(body["suffix"], SUFFIX)
        self.assertEqual(body["suffixFrom"], "query")
        self.assertEqual(body["lqPath"], d.as_posix())
        self.assertEqual([i["kind"] for i in body["items"]],
                         ["input", "product", "nosr"])

        inp = self.by_kind(body, "input")
        self.assertEqual(inp["name"], f"{SCENE_NAME}.tif")
        self.assertTrue(inp["exists"])
        self.assertEqual((inp["W"], inp["H"]), (320, 640))

        got_product = self.by_kind(body, "product")
        self.assertEqual(got_product["name"], product.name)
        self.assertTrue(got_product["exists"])
        self.assertEqual((got_product["W"], got_product["H"]), (80, 40))
        self.assertGreater(got_product["sizeBytes"], 0)
        self.assertIsNotNone(got_product["mtime"])

        got_nosr = self.by_kind(body, "nosr")
        self.assertEqual(got_nosr["name"], nosr.name)
        self.assertTrue(got_nosr["exists"])

        # 每一类都有自己的 id → 各拿自己的 id 调 /preview 就能看图，
        # 三类各有自己的 <stem>_preview.jpg 落点，天然不撞名。
        ids = [i["id"] for i in body["items"]]
        self.assertEqual(len(set(ids)), 3, "三个 id 必须互不相同")
        for i in body["items"]:
            self.assertTrue(i["id"], "库内/库外的行都该给出可用的 id")

    def test_preview_state_is_reported_per_item(self):
        """三份各自有没有预览、是哪一档 —— 对比视图据此判断要不要等一次烘焙。"""
        d = self.make_scene()
        self._product(d, SUFFIX)
        c = self.client()
        row = self.resolve(c, d)

        body = self.q(c, row["id"], suffix=SUFFIX)

        for i in body["items"]:
            self.assertFalse(i["hasPreview"], "还没人烤过")
            self.assertIsNone(i["previewDiv"])

    def test_missing_product_still_answered_with_the_names_tried(self):
        """**「找不到」本身就是回答**：exists=false + productCandidates 说明试过哪些
        名字，前端不必再问一次。这也是 `_NOSR` 拼法待真机核对时的诊断窗口。"""
        d = self.make_scene()
        c = self.client()
        row = self.resolve(c, d)

        body = self.q(c, row["id"], suffix=SUFFIX)

        got = self.by_kind(body, "product")
        self.assertFalse(got["exists"])
        self.assertIsNone(got["sizeBytes"])
        self.assertEqual(got["name"], f"{SCENE_NAME}_{SUFFIX}.tif")
        self.assertEqual(body["productCandidates"],
                         [f"{SCENE_NAME}_{SUFFIX}.tif",
                          f"{SCENE_NAME}_{SUFFIX}.tiff"])
        # 拼不出的那类照样有条目，只是同样 exists=false —— 结构恒定，前端不必判有没有
        self.assertFalse(self.by_kind(body, "nosr")["exists"])

    def test_suffix_comes_from_the_latest_completed_task(self):
        """真跑过就用真跑的那个名字：`?suffix=` 缺省时它比配置缺省权威得多。"""
        d = self.make_scene()
        self._product(d, SUFFIX)
        c = self.client()
        row = self.resolve(c, d)
        store = self._apps[-1].state.store
        params = {"lq_path": d.as_posix(), "mask_path": None, "sr_scale": 2,
                  "suffix": SUFFIX, "gpu": 0, "cloud_limit": 80,
                  "delete_ori": False, "grid_align": True, "options_yml": None}
        store.put_sr_task(task_fingerprint(params), params, status="new", job_id=None)
        tid = store.get_sr_task(task_fingerprint(params))["task_id"]
        store.set_sr_task_state(tid, "COMPLETED", mark_finished=True)

        body = self.q(c, row["id"])

        self.assertEqual(body["suffix"], SUFFIX)
        self.assertEqual(body["suffixFrom"], "task", "得说清这个名字是哪来的")
        self.assertTrue(self.by_kind(body, "product")["exists"])

    def test_failed_task_suffix_is_not_used(self):
        """FAILED 已经动过产物路径，名字拼得出来但内容是半截 —— 拿它当「本次结果」
        只会骗人。退回配置缺省，并把 suffixFrom 如实标成 default。"""
        d = self.make_scene()
        c = self.client()
        row = self.resolve(c, d)
        store = self._apps[-1].state.store
        params = {"lq_path": d.as_posix(), "mask_path": None, "sr_scale": 2,
                  "suffix": SUFFIX, "gpu": 0, "cloud_limit": 80,
                  "delete_ori": False, "grid_align": True, "options_yml": None}
        store.put_sr_task(task_fingerprint(params), params, status="new", job_id=None)
        tid = store.get_sr_task(task_fingerprint(params))["task_id"]
        store.set_sr_task_state(tid, "FAILED", mark_finished=True)

        body = self.q(c, row["id"])

        self.assertNotEqual(body["suffix"], SUFFIX)
        self.assertEqual(body["suffixFrom"], "default")

    def test_suffix_falls_back_to_the_bundle_default(self):
        d = self.make_scene()
        c = self.client()
        row = self.resolve(c, d)

        body = self.q(c, row["id"])

        self.assertTrue(body["suffix"], "配置里总有 <Suffix>，回退链不会断")
        self.assertEqual(body["suffixFrom"], "default")
        self.assertTrue(body["productCandidates"])

    def test_div_is_the_server_side_bake_tier(self):
        """`div` = 服务端急烤的档位，**仅供界面标注**，不参与任何前端决策
        （前端的档位是用户滑块那个，两个「4」互不联动）。"""
        d = self.make_scene()
        c = self.client()
        row = self.resolve(c, d)
        self.assertEqual(self.q(c, row["id"])["div"], 4)          # 缺省 ÷4

        os.environ["SR_PRODUCT_PREVIEW_DIV"] = "8"
        self.addCleanup(os.environ.pop, "SR_PRODUCT_PREVIEW_DIV", None)
        self.assertEqual(self.q(c, row["id"])["div"], 8)

    def test_query_suffix_wins_over_the_task(self):
        d = self.make_scene()
        self._product(d, NOSR_SUFFIX)
        c = self.client()
        row = self.resolve(c, d)
        body = self.q(c, row["id"], suffix=NOSR_SUFFIX)
        self.assertEqual(body["suffixFrom"], "query")
        self.assertTrue(self.by_kind(body, "product")["exists"])

    def test_unknown_scene_404(self):
        c = self.client()
        r = self.siblings(c, "ghost-scene-id")
        self.assertEqual(r.status_code, 404)

    def test_outside_the_whitelist_404(self):
        """场景 id 解出来的路径要过白名单 —— 否则这个端点成了任意路径探测窗口。"""
        c = self.client()
        r = self.siblings(c, "~L3RtcC9ub3QtaW4td2hpdGVsaXN0")   # ~/tmp/not-in-whitelist
        self.assertIn(r.status_code, (404, 403))


class TestSiblingsSuffixValidation(SiblingsBase):
    def test_illegal_suffix_400(self):
        d = self.make_scene()
        c = self.client()
        row = self.resolve(c, d)
        # 空串不在此列：`?suffix=` 为空等于没给，走的是回退链而不是报错。
        for bad in ("../x", "a/b", "a\\b", "x" * 17, "a b", "a.b"):
            r = self.siblings(c, row["id"], suffix=bad)
            self.assertEqual(r.status_code, 400, f"{bad!r} 该被拒：{r.text}")
            self.assertIn("suffix", r.json()["detail"])


class TestSiblingsIsReadOnly(SiblingsBase):
    def test_never_bakes_and_never_writes(self):
        """纯只读：不该因为问了一句就把大图烤一遍，也不该在场景目录里留下任何东西。

        这一条要真断言盘上没多文件 —— 只断 HTTP 状态的话，「顺手烤一份」的实现
        照样全绿。
        """
        d = self.make_scene()
        self._product(d, SUFFIX)
        before = sorted(p.name for p in d.iterdir())
        c = self.client()
        row = self.resolve(c, d)

        self.q(c, row["id"], suffix=SUFFIX)

        self.assertEqual(sorted(p.name for p in d.iterdir()), before)

    def test_preview_endpoint_still_bakes_but_siblings_does_not(self):
        """对照组：证明上面那条不是因为「这里根本烤不了」，而是 siblings 不烤。"""
        d = self.make_scene()
        c = self.client()
        row = self.resolve(c, d)
        self.q(c, row["id"], suffix=SUFFIX)
        self.assertFalse((d / f"{SCENE_NAME}_preview.jpg").exists())

        pv = c.get(f"/api/scenes/{row['id']}/preview")
        self.assertEqual(pv.status_code, 200, pv.text)
        self.assertTrue((d / f"{SCENE_NAME}_preview.jpg").is_file())


class TestSiblingsNeverListsDirectories(SiblingsBase):
    """不扫盘是可执行的保证，不是注释里的承诺（同 test_scene_resolve 的口径）。

    打桩必须收在请求范围里：`os.scandir` 也是 `shutil.rmtree` 清临时目录时要用的，
    用 `addCleanup` 会让打桩活到 tearDown 之后，临时目录就删不掉了。
    """

    BANNED = ("rglob", "glob", "iterdir")

    def test_siblings_without_listing(self):
        d = self.make_scene()
        self._product(d, SUFFIX)
        c = self.client()
        row = self.resolve(c, d)
        with ExitStack() as st:
            for name in self.BANNED:
                st.enter_context(mock.patch.object(
                    Path, name,
                    side_effect=AssertionError(f"禁止 Path.{name}（扫盘）")))
            for name in ("listdir", "scandir", "walk"):
                st.enter_context(mock.patch.object(
                    os, name,
                    side_effect=AssertionError(f"禁止 os.{name}（扫盘）")))
            r = self.siblings(c, row["id"], suffix=SUFFIX)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(len(r.json()["items"]), 3)

    def _stat_count(self, noise: int) -> int:
        """在这个端点上跑一次请求，数 `Path.stat` 被调用了几次。"""
        d = self.make_scene()
        self._product(d, SUFFIX)
        for i in range(noise):
            (d / f"noise_{i}.dat").write_bytes(b"")
        c = self.client()
        row = self.resolve(c, d)
        real_stat = Path.stat
        calls: list[str] = []

        def counting_stat(self, *a, **kw):
            calls.append(self.name)
            return real_stat(self, *a, **kw)

        with mock.patch.object(Path, "stat", counting_stat):
            r = self.siblings(c, row["id"], suffix=SUFFIX)
        self.assertEqual(r.status_code, 200, r.text)
        return len(calls)

    def test_probes_a_bounded_number_of_files(self):
        """探测次数**绝不随目录内容增长** —— 这是不扫盘的等价表述，也是这个用例
        真正要钉的东西。

        断言写成「0 个噪声文件与 200 个噪声文件的计数相等」而不是一个魔数常量：
        魔数会随实现微调来回改（这里 24 里有 3 次还是 `pathguard.is_within` 里
        `Path.resolve()` 自己 stat 的），而**不随目录内容变**才是那条约束。
        `SR_SCENES_ROOT` 关闭时这些盘阵文件根本不在库内、本就不该被列举筛选；
        换成「列举目录再筛」的改法，这个数会直接随噪声文件数涨。
        """
        small = self._stat_count(0)
        big = self._stat_count(200)
        self.assertEqual(small, big,
                         "探测次数随目录内容变了 —— 这就是扫盘")
        self.assertLessEqual(small, 40, "粗上限：钉住数量级，防止哪天悄悄翻几倍")


if __name__ == "__main__":
    unittest.main()
