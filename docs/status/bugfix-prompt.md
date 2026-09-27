# 新窗口开工提示词：修一个 bug

> 日期：2026-09-17 · 状态：已定 · 用法：整篇复制粘给新开的 Claude 窗口，末尾补上 bug 描述。

---

## 你要做的事

修我在末尾描述的 bug。修完更新 `docs/status/current-question.md`。

## 协作方式

- 我在内网机 node81-135（CentOS7）上执行命令，你读不到那台机器。需要什么信息就给我一条命令，
  我贴输出回来。
- 仓库根 `D:\BaiduNetdiskDownload\sr_agent_platform`，你只在这台外网 Windows 开发机上改代码。
- 我偏好简要回答：先给结论和清单。
- 改代码前先说方案（改哪些文件、为什么）。这个项目跑了很久，看着可以简化的地方往往是有原因的。
- 现象描述可能与真实原因不符。先定位，再下结论；不要照着我的猜测直接改。

## 两条红线

- **不要扫盘**。真实遥感数据在盘阵上，数据量极大。后端代码禁止 `ls` / `glob` / `rglob` /
  `iterdir`，只允许 `stat` 用户明确给的那一个路径。有测试把 `os.listdir` / `scandir` / `walk`
  打成 `AssertionError` 钉这条。
- 涉及路径反推的场景：**命中才允许提交，猜错必须报错并说明原因，绝不静默降级**。

## 30 秒地图

| 位置 | 里面是什么 |
|---|---|
| `backend/api/app.py` | 场景检索 `/api/scenes`、预览 `/preview`、`POST /api/scenes/resolve` |
| `backend/api/platform.py` | `/api/chat/*`、`/api/queue*`（REST + SSE）、`/api/tools`、`/api/masks` |
| `backend/pathguard.py` | 盘阵路径的唯一真源：盘符映射、白名单、由文件名反推场景目录 |
| `backend/services/` | 流程编排：场景判据、JPG 预览、跑 SR、掩码、SQLite store |
| `frontend/src/pages/` | `ViewerPage` 查看器、`ScenesPage` 场景库、`QueuePage` 队列、`ChatPage` 对话 |
| `frontend/src/stores/` | `viewer` / `scenes` / `queue` / `chat` |

详细地图与当前路线见 [handoff-prompt.md](handoff-prompt.md)；项目背景见
[docs/status/current-question.md](current-question.md) §1（平台现状）与
[docs/status/timeline-archive.md](timeline-archive.md)（时间线，按日期倒着看）。

## 干活流程

1. **复现**：先让现象稳定重现。必要时让我在真机跑一条命令。
2. **定位**：找到真正的原因，写清楚。分清实测与推测。
3. **说方案**：改哪几个文件、为什么这么改、有没有更小的改法。等我点头。
4. **改 + 加测试**：新行为要有测试钉住；改掉的行为要同步改掉对应测试，不要留红。
5. **验证**：跑下面全套，基线不许退化。
6. **文档**：`docs/status/timeline-archive.md` 加一条当日条目（现象 / 根因 / 改法 / 验证 / 真机待确认），
   `current-question.md` 如果状态变了就同步。相关契约文档若受影响一并改。

## 验证命令

```bash
# 后端（基线 464 passed / 1 skipped）
python -m pytest backend/ -q

# 前端（基线 171 passed；先加 nvm 到 PATH，勿用系统 node）
export PATH="/c/Users/lenovo/AppData/Local/nvm/v20.19.5:$PATH"
cd frontend && npx vitest run && npx vue-tsc --noEmit && npm run build

# 浏览器回归（先 build；基线 39 / 61 / 18 断言）
cd .e2e && node test-manual-scene.js && node test-scenes.js && node test-platform.js
```

## 收尾

改完重新打交付物（用户要拷去内网机覆盖部署）：

```bash
cd frontend && npm run build && cd ..
# dist 与 backend 两个包，名字带时间戳与 HEAD 短 sha
```

## 写作要求

不用 emoji，不带情感色彩，术语统一，不用黑话。实测、推测、待核三种措辞分开写。

## Bug 描述

（在这里贴上现象、报错原文、复现步骤、以及我看到的预期行为）
