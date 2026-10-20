"""聊天截图附件（services/chat_images.py + §3.2.1 的落盘/水合契约）。

这些用例只碰服务层；HTTP 端到端（SSE / 投影 / GET 图片 / 删会话）在
`test_api_platform.py::TestChat` 里。
"""

import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from backend.services import chat_images


def png_data_url(size=(8, 6), color=(10, 200, 30)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class ChatImagesBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {k: os.environ.get(k)
                       for k in ("SR_CHAT_IMAGE_DIR", "SR_AGENT_DB")}
        os.environ["SR_AGENT_DB"] = os.path.join(self._tmp.name, "db.sqlite")
        os.environ.pop("SR_CHAT_IMAGE_DIR", None)
        self.sid = "a" * 32

    def tearDown(self):
        self._tmp.cleanup()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def root(self) -> Path:
        return Path(self._tmp.name) / "chat_images"


class TestSaveImages(ChatImagesBase):
    def test_reenecodes_to_jpeg_and_caps_long_side(self):
        saved = chat_images.save_images(
            self.sid, [{"name": "big.png", "data": png_data_url((2600, 1300))}])
        self.assertEqual(len(saved), 1)
        url = saved[0]["url"]
        self.assertTrue(url.startswith(
            f"/api/chat/sessions/{self.sid}/images/"))
        self.assertEqual(saved[0]["name"], "big.png")

        path = chat_images.image_path(
            self.sid, url.rsplit("/", 1)[-1])
        self.assertIsNotNone(path)
        with Image.open(path) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertEqual(max(im.size), chat_images.MAX_SIDE)

    def test_alpha_is_composited_on_white_not_black(self):
        buf = io.BytesIO()
        Image.new("RGBA", (4, 4), (255, 0, 0, 0)).save(buf, format="PNG")
        data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        saved = chat_images.save_images(self.sid, [{"data": data}])
        path = chat_images.image_path(self.sid, saved[0]["url"].rsplit("/", 1)[-1])
        with Image.open(path) as im:
            rgb = im.convert("RGB").getpixel((0, 0))
        self.assertTrue(all(channel > 240 for channel in rgb), rgb)

    def test_rejects_third_image(self):
        with self.assertRaises(chat_images.ChatImageError) as cm:
            chat_images.save_images(
                self.sid, [{"data": png_data_url()}] * 3)
        self.assertIn("2", str(cm.exception))

    def test_rejects_bad_payloads(self):
        cases = [
            {"name": "x", "data": "data:image/png;base64,!!!not-base64!!!"},
            {"data": "   "},
            {"data": base64.b64encode(b"just some text").decode()},
            "not-an-object",
        ]
        for case in cases:
            with self.subTest(case=str(case)[:30]):
                with self.assertRaises(chat_images.ChatImageError):
                    chat_images.save_images(self.sid, [case])

    def test_images_must_be_a_list(self):
        with self.assertRaises(chat_images.ChatImageError):
            chat_images.save_images(self.sid, {"data": png_data_url()})

    def test_name_is_basename_only(self):
        saved = chat_images.save_images(
            self.sid, [{"name": "../../evil.jpg", "data": png_data_url()}])
        self.assertEqual(saved[0]["name"], "evil.jpg")
        # 名字不参与路径：落盘文件名是 uuid，目录也只有会话这一层
        files = list((self.root() / self.sid).iterdir())
        self.assertEqual(len(files), 1)
        self.assertNotIn("evil", files[0].name)

    def test_failed_batch_leaves_no_files_behind(self):
        with self.assertRaises(chat_images.ChatImageError):
            chat_images.save_images(self.sid, [
                {"data": png_data_url()},
                {"data": "not base64 at all"},
            ])
        target = self.root() / self.sid
        self.assertEqual(list(target.iterdir()), [])

    def test_pixel_bomb_rejected(self):
        with mock.patch.object(chat_images, "MAX_PIXELS", 10):
            with self.assertRaises(chat_images.ChatImageError):
                chat_images.save_images(
                    self.sid, [{"data": png_data_url((20, 20))}])

    def test_empty_input_is_noop(self):
        self.assertEqual(chat_images.save_images(self.sid, None), [])
        self.assertEqual(chat_images.save_images(self.sid, []), [])
        self.assertFalse((self.root() / self.sid).exists())


class TestImagePath(ChatImagesBase):
    def test_rejects_traversal(self):
        saved = chat_images.save_images(
            self.sid, [{"data": png_data_url()}])
        good = saved[0]["url"].rsplit("/", 1)[-1]
        self.assertIsNotNone(chat_images.image_path(self.sid, good))

        for sid, name in [
            (self.sid, "../" + good),
            (self.sid, ".."),
            (self.sid, good + ".exe"),
            (self.sid, good[:-4]),     # 没有 .jpg 后缀
            ("../../etc", good),
            ("", good),
            (self.sid, ""),
            (self.sid, "/" + good),
        ]:
            with self.subTest(sid=sid[:8] + name[:8]):
                self.assertIsNone(chat_images.image_path(sid, name))

    def test_foreign_session_filename_does_not_resolve_to_a_file(self):
        """别的会话的文件名形态合法，但落在**那个会话**的目录里 —— 取不到图。"""
        saved = chat_images.save_images(
            self.sid, [{"data": png_data_url()}])
        good = saved[0]["url"].rsplit("/", 1)[-1]
        path = chat_images.image_path("b" * 32, good)
        self.assertTrue(path is None or not path.is_file())

    def test_delete_session_images(self):
        chat_images.save_images(self.sid, [{"data": png_data_url()}])
        self.assertTrue((self.root() / self.sid).is_dir())
        chat_images.delete_session_images(self.sid)
        self.assertFalse((self.root() / self.sid).exists())
        # 幂等：再删一次不抛
        chat_images.delete_session_images(self.sid)


class TestHydrate(ChatImagesBase):
    def _wire(self, url: str, name: str = "a.png") -> list[dict]:
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": url}, "name": name},
            ]},
        ]

    def test_url_becomes_data_url_and_name_is_stripped(self):
        saved = chat_images.save_images(
            self.sid, [{"name": "a.png", "data": png_data_url()}])
        wire = self._wire(saved[0]["url"])
        out = chat_images.hydrate_images(wire)

        block = out[1]["content"][1]
        self.assertEqual(set(block), {"type", "image_url"})
        self.assertEqual(set(block["image_url"]), {"url"})
        self.assertTrue(block["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"))
        self.assertEqual(out[1]["content"][0], {"type": "text", "text": "看图"})
        # 浅拷贝：入参那条 wire 没有被改写
        self.assertEqual(wire[1]["content"][1]["name"], "a.png")

    def test_missing_file_degrades_to_text(self):
        wire = self._wire(
            f"/api/chat/sessions/{self.sid}/images/{'f' * 32}.jpg")
        out = chat_images.hydrate_images(wire)
        self.assertEqual(out[1]["content"][1]["type"], "text")
        self.assertIn("不可用", out[1]["content"][1]["text"])

    def test_text_only_messages_pass_through_untouched(self):
        wire = [{"role": "user", "content": "纯文字"},
                {"role": "assistant", "content": None}]
        out = chat_images.hydrate_images(wire)
        self.assertEqual(out, wire)
        self.assertIs(out[0], wire[0])       # 非 list content 原对象返回

    def test_cache_avoids_second_disk_read(self):
        saved = chat_images.save_images(
            self.sid, [{"data": png_data_url()}])
        cache: dict[str, str] = {}
        chat_images.hydrate_images(self._wire(saved[0]["url"]), cache=cache)
        self.assertEqual(len(cache), 1)
        with mock.patch.object(Path, "read_bytes",
                               side_effect=AssertionError("不该再读盘")):
            out = chat_images.hydrate_images(
                self._wire(saved[0]["url"]), cache=cache)
        self.assertTrue(out[1]["content"][1]["image_url"]["url"].startswith(
            "data:"))


class TestContentShapes(ChatImagesBase):
    def test_user_content_keeps_plain_string_without_images(self):
        self.assertEqual(chat_images.user_content("你好", []), "你好")

    def test_user_content_and_split_roundtrip(self):
        self.assertEqual(chat_images.user_content("", []), "")
        saved = chat_images.save_images(
            self.sid, [{"name": "a.png", "data": png_data_url()}])
        content = chat_images.user_content("看图", saved)
        self.assertIsInstance(content, list)
        text, images = chat_images.split_content(content)
        self.assertEqual(text, "看图")
        self.assertEqual(images, [{"url": saved[0]["url"], "name": "a.png"}])

    def test_image_only_content_has_no_text_block(self):
        saved = chat_images.save_images(
            self.sid, [{"data": png_data_url()}])
        content = chat_images.user_content("", saved)
        self.assertEqual([b["type"] for b in content], ["image_url"])
        text, images = chat_images.split_content(content)
        self.assertEqual(text, "")
        self.assertEqual(len(images), 1)

    def test_split_ignores_data_urls_and_plain_strings(self):
        self.assertEqual(chat_images.split_content("老消息"),
                         ("老消息", []))
        self.assertEqual(chat_images.split_content(None), ("", []))
        blocks = [{"type": "image_url",
                   "image_url": {"url": "data:image/png;base64,xx"}}]
        self.assertEqual(chat_images.split_content(blocks), ("", []))
