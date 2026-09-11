# SR 提交链路恢复 · 分阶段计划清单

> **版本**：v1.0 · **日期**：2026-09-10 · **状态**：待评审
> **目标**：把「10.10.81.135 查看器 → 画掩码 → 提交 SR → Slurm 分配 GPU → conda 环境跑超分 → 结果与状态回前端」这一条链**从当前断裂状态恢复到端到端可跑**。
> **执行分工**：代码改动（阶段 1）由我改 + 本地测试；真机命令由你在 10.10.81.135 上执行、输出贴回我判读。
> **上位文档**：[slurm-integration.md](../status/slurm-integration.md)（决策快照）、[slurm-acceptance.md](../status/slurm-acceptance.md)（真机验收 A–D，本清单的阶段 3–5 是它的执行版）、[sr-slurm-deploy-variant.md](../sr_code/sr-slurm-deploy-variant.md)（变体差异 E1–E9）。

---

## 0. 断点盘点：为什么现在点不亮

| # | 断点 | 证据 | 本计划对应 |
|---|---|---|---|
| B1 | 提交的作业跑的是**生产原版** `code_0817_prod.py`，不是部署变体（无 E1–E9：GPU 数守卫 `!= 4` 直接判失败、`CUDA_VISIBLE_DEVICES` 被覆写） | [run_sr.py:180](backend/services/run_sr.py#L180) 硬编码脚本名，无 env 开关；对比 [run_sr.py:131-132](backend/services/run_sr.py#L131-L132) 的 `SR_VERIFY_SCRIPT` **有**开关 | 阶段 1 E-A、阶段 2 |
| B2 | 空后缀直通到 config.xml，落到 SR 就变成"就地覆盖"语义（输入被改名 `<stem>_NOSR.tif`，输出占原名），且掩码烘焙预填的 draft 就是 `suffix: ""` | [platform.py:529](backend/api/platform.py#L529)（draft `""`）、[platform.py:356](backend/api/platform.py#L356)、[tools/run_sr.py:100](backend/tools/run_sr.py#L100)、[queue.ts:46](frontend/src/stores/queue.ts#L46) 一路不挡 | 阶段 1 E-B |
| B3 | 检索按 `.tif/.tiff/.img` 后缀收文件，在 `/DiskArray/GSHC2IMPS/<年>/<月>/<日>/<生产编号>/` 深树下会把 `_NOSR`/`_mask`/SR 输出**当成场景**，且每次请求全树 `rglob` + 逐文件 `stat`，不缓存 | [scene_search.py:26](backend/services/scene_search.py#L26)、[:54-58](backend/services/scene_search.py#L54-L58) | 阶段 1 E-D |
| B4 | 云量字段解析不具容错：`int(CloudPercent)` 一个非数字就抛，作业无 SRLOG 退出，平台侧看到的是"静默失败" | [code_0817_prod.py:157-160](SR_code/code_0817_prod.py#L157-L160) | 阶段 1 E-C |
| B5 | 服务环境未落盘（六项 env 只写在文档里）、变体未上机 | [deploy/README.md](../../deploy/README.md) §七只给了命令 | 阶段 2 |
| B6 | 平台写盘阵的落点未定：预览 JPG 默认落在**源图同目录**（即生产数据目录里） | [paths.py:118-135](backend/api/paths.py#L118-L135) | 决策点 ①、阶段 1 E-E |

> 注：B1/B2/B4 是"改了才能跑"；B3/B6 是"能用"与"好用"的分界。阶段 3–5 依赖 B1/B2/B4 先落地。

---

## 0.1 真机统一环境变量（每个 shell 会话先 source 一次）

```bash
APP=/run/media/root/SSD/workspace/wangrz/sr-agent-platform
BUNDLE=/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes
SR_PYTHON=/run/media/root/SSD/program/anaconda/installed/envs/torch1.9.1py36/bin/python
TEST=/DiskArray/tmp/wangrz/sr_test          # 沙箱场景副本根（手工验证用；平台侧走 SR_SANDBOX_ROOT）
SANDBOX=/DiskArray/tmp/wangrz/sr_sandbox    # 平台沙箱：每个作业的私有副本（2026-09-10 起平台自建）
WORK=/DiskArray/tmp/wangrz/sr_agent_work    # Slurm 作业工作目录（多节点必须共享 → 留盘阵）
PREV=/var/lib/sr-agent/previews             # 预览 JPG 缓存（本地盘，见决策点 ①）
OPT=/DiskArray/tmp/wangrz/sr_utils/espan3_2026_gf04_tile500.yml
PROD=/DiskArray/GSHC2IMPS                   # 生产盘阵根（仅阶段 6 使用）
PART=gpu                                   # 2026-09-11 sinfo 实测（无 gpup）
```

沙箱副本是本计划的地基：**阶段 0–5 全部在副本上跑，生产盘阵只读**。这样排查期生产目录里不会出现预览/掩码/Debug 等新文件。

> **2026-09-10 修订 · 副本由平台自己建**。原计划让阶段 0.2 手工 `cp -a` 到 `$TEST`、阶段 4-5 前端填 `$TEST` 里的路径。改成：平台加 `SR_SANDBOX_ROOT`，**每个作业在批脚本第一步**把 `lq_path` 复制到 `<SANDBOX>/<任务指纹前12位>/<目录名>`，config 的 `DatarootLQ` 直接指向副本（见 [deploy/README §7.5](deploy/README.md)）。
>
> 动机有两条：① 每换一个场景就要手工拷一次，是**每次**的税，而不是**一次**的税；② `util.writeTiff` 对输入是改名而非只读（`os.rename(..., "_NOSR")`），"前端填路径"的字面含义就是"那个目录会被改写"——把只读保证交给人的记性，迟早会忘。
>
> 影响：**阶段 0.2 的手工 `$TEST` 副本只用于阶段 C（不碰平台的手工验证）**；阶段 4-5 前端填的是**生产路径**，平台自己拷。`$TEST` 仍保留给手工命令用。

---

## 0.2 三个待你拍板的决策点

| # | 决策 | 我的建议 | 影响 |
|---|---|---|---|
| ① | **预览 JPG 缓存落哪**。浏览器**不能**写 Windows 的 D 盘（无任意写盘权限，唯一"落 D 盘"的路子是手动"另存为"，每张一次，体验更差）。可行的等价方案是写**服务器本地盘**（毫秒级、不碰盘阵），再由 nginx 发图 + 浏览器 HTTP 缓存复用 | `SR_PREVIEWS_ROOT=/var/lib/sr-agent/previews`（本地盘、开机即在）。**需改代码**：现在 [paths.py:129-132](backend/api/paths.py#L129-L132) 强制要求预览根**必须位于 scenes 根之下**（理由是 nginx 单根 alias），落在本地盘会被 `PreviewError` 拒绝 | 阶段 1 E-E；nginx 需加第二个 `location` |
| ② | **默认后缀取值** | `sr`，白名单 `[A-Za-z0-9_-]{1,32}`。副作用：作业日志目录会成 `run_sr_sr`（[run_sr.py:326](backend/services/run_sr.py#L326) 的兜底串）。若现场习惯 `t`/`t1`，改这个常量即可 | 阶段 1 E-B |
| ③ | **阶段 6 生产写入范围** | 灰度：先只放开**一个生产编号目录**给 nginx 写（`chgrp` + `g+w` 或 ACL），验证后再决定全树 | 阶段 6 |

---

## 阶段 0 · 地基（真机，只读 + 建目录）

**目标**：把"盘阵上一条真实场景长什么样"从推断变成实测，并备好沙箱。

### 0.1 结构核查（只读，5 项）

在生产盘阵上任挑一个生产编号目录，逐条贴回输出：

```bash
# ① 挑一个真实生产编号目录（层级：年/月/日/生产编号）
ls -d $PROD/*/*/*/*/ 2>/dev/null | head -3
D=$(ls -d $PROD/*/*/*/*/ 2>/dev/null | head -1); echo "D=$D"

# ② 目录里到底有什么
ls -la "$D"

# ③ 目录名 vs tif 名是否同名（SR 的输入判据就是这个）
basename "$D"; ls "$D" | grep -i '\.tif$'

# ④ meta 与 Debug
ls -la "$D"/*_meta.xml "$D"/Debug 2>&1 | head

# ⑤ 走 RC 步还是 SC 步（SolarAzimuth 空 → RC，非空 → SC）
$SR_PYTHON -c "
import sys,xml.dom.minidom as m
d=m.parse('$D/'+__import__('os').path.basename('$D')+'_meta.xml')
n=d.getElementsByTagName('SolarAzimuth')[0].childNodes
print('step =', 'RC' if n.length==0 else 'SC')
print('CloudPercent =', [c.data for c in d.getElementsByTagName('CloudPercent')[0].childNodes])
"
```

**判据**：② 里应能找到 `<目录名>.tif`（SC 步）或 `PAN.tif`（RC 步），且 `<目录名>_meta.xml` 存在。
**若不符**：说明实际层级或命名与 [util.py:989-991](SR_code/util.py#L989-L991) 的判据不一致 —— **停下贴给我**，阶段 1 的检索过滤规则要按实测改写。

### 0.2 制备沙箱副本

```bash
mkdir -p $TEST
cp -a "$D" $TEST/            # 保留目录名（SR 靠目录名找输入，改名即失效）
chown -R nginx:nginx $TEST   # 作业以 User=nginx 跑（见 deploy/README §7.2）
ls -la $TEST/$(basename "$D")
```

> 目录名必须保持不变：[util.py:991](SR_code/util.py#L991) 用 `osp.basename(lq_path)` 拼输入文件名。

### 0.3 服务器本地暂存区

```bash
mkdir -p $PREV $WORK
chown -R nginx:nginx $PREV $WORK
df -h $PREV $WORK
```

**判据**：`$PREV` 在**本地盘**（非盘阵挂载点）；`$WORK` 在**共享存储**（分区 `gpu` 多节点，作业在计算节点上要读得到，见 [config.py:44-49](backend/config.py#L44-L49)）。
**若不符**：`$PREV` 若落在开机不自动挂载的移动介质上，nginx 起服时目录不存在会 500 —— 换 `/var/lib/sr-agent/previews`。

### 0.4 环境清账

```bash
sinfo -p $PART -N -o "%N %G %t" | head
$SR_PYTHON -c "import torch, gdal; print(torch.__version__, torch.cuda.is_available())"
ls $BUNDLE/code_0817_prod.py $BUNDLE/tools/ImgHistMatch.so
id nginx
```

**判据**：分区有多节点（→ `$WORK` 必须共享）；conda 解释器能 import torch/gdal；bundle 两个关键文件在；`nginx` 用户存在。

### 0.5 备份

```bash
cp $BUNDLE/code_0817_prod.py $BUNDLE/code_0817_prod.py.orig.$(date +%Y%m%d)
cp $APP/deploy/sr-api.service $APP/deploy/sr-api.service.bak.$(date +%Y%m%d)
systemctl cat sr-api > /tmp/sr-api.service.snapshot
```

**产物**：0.1 的五段输出（贴回）、沙箱目录就位、三个决策点的答复。

---

## 阶段 1 · 代码改动（离机，开发机，每项配测试）

**目标**：把阶段 3–5 会撞到的五个坑在开发机上先改掉。全部改完跑一次 `python -m pytest backend/tests -q`（基线 **284 passed**）。

> **进度**：E-A 已完成（2026-09-10，测试 287 passed）。E-B/E-C/E-D/E-E 未动。

### E-A `SR_SR_SCRIPT` 开关（对应 B1）· ✅ 已完成

**为什么必须要**：变体装法有二 —— ① 改名覆盖 `code_0817_prod.py`；② 变体以 `code_0817_prod_slurm.py` 之名并置，用 env 指过去。选 ②，**生产原文件保持字节不动**，回滚只是改一行 env。

已落地：
- [run_sr.py:100](backend/services/run_sr.py#L100) 函数签名加 `sr_script=None`；
- [run_sr.py:131](backend/services/run_sr.py#L131) 旁加对称的一行（含"名字在生成期写进批脚本、不在作业里读 env"的说明）；
- [run_sr.py:180](backend/services/run_sr.py#L180) 字面量 → `{sr_script}`；
- audit 回显加 `echo "SR_SCRIPT=..."` —— 从 `%j.out` 就能看出跑的是原版还是变体。

测试（`test_run_sr.py`）：默认仍输出 `code_0817_prod.py`；`SR_SR_SCRIPT` 生效且**整份脚本不再出现原脚本名**；显式参数压过 env；audit 含 `SR_SCRIPT=`。

同步更新：[deploy/sr-api.service](deploy/sr-api.service)（新增两项 env + 注释）、[deploy/README.md](../../deploy/README.md) §七（§7.1 改为并置装法 + 八项 env 表）、[docs/status/slurm-acceptance.md](../status/slurm-acceptance.md) §0.2/§0.3、[docs/sr_code/sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md) §7.1、[docs/status/current-question.md](../status/current-question.md) §6.4。

### E-B 空后缀归一（对应 B2）

三层同时挡，任一层漏掉都不会造成就地覆盖：

1. **后端归一（权威）**：[platform.py:356](backend/api/platform.py#L356) 之后加 `_norm_suffix(s)` —— 去空白 → 空则取默认值 → 白名单 `[A-Za-z0-9_-]{1,32}` 过滤 → 非法 400。**必须白名单**：suffix 会拼进输出文件名（[run_sr.py:88](backend/services/run_sr.py#L88) 写进 `<Suffix>`），`../` 之类即路径穿越。
2. **工具入口同源**：[tools/run_sr.py:100](backend/tools/run_sr.py#L100) 复用同一 `_norm_suffix`（agent 直接调工具时走这条路，REST/工具两条路必须同结果，否则 `task_fingerprint` 会分叉）。
3. **预填与前端默认**：[platform.py:529](backend/api/platform.py#L529) draft 的 `"suffix": ""` → 默认值；[queue.ts:46](frontend/src/stores/queue.ts#L46) `defaultForm()` 的 `suffix: ''` → 默认值；[QueuePage.vue:128](frontend/src/pages/QueuePage.vue#L128) placeholder 与 [QueuePage.vue:176](frontend/src/pages/QueuePage.vue#L176) 的「无后缀」分支同步。

测试：`test_api_platform.py` 加 `suffix=""` / `suffix="  "` / `suffix="a/b"` / `suffix="x"*40` 四例；`test_run_sr.py` 确认 fingerprint 对 `""` 与默认值**不同**（不能悄悄合并两个语义不同的请求）。

### E-C 变体新增 E10：云量容错（对应 B4）

在 [gen_slurm_variant.py](SR_code/tools/gen_slurm_variant.py) 的 `EDITS` 追加第 10 条，锚点即 [code_0817_prod.py:157-158](SR_code/code_0817_prod.py#L157-L158)：

```python
# 锚点（原文）
    cloudpercent_tmp = util.get_cfg_value(meta_abs_path, "CloudPercent")
    cloudpercent = int(cloudpercent_tmp) if cloudpercent_tmp is not None else 0
# 替换：非数字 → 视为不跳过（fail-open，只影响策略跳过，不影响 SR 正确性）
```

**判据**：`python SR_code/tools/gen_slurm_variant.py && python SR_code/tools/gen_slurm_variant.py --check` 退出 0；`provenance.json` 出现 `E10`。
**注意**：`util.py` 内**还有一族 `exit()`**（[util.py:999](SR_code/util.py#L999)、[1002](SR_code/util.py#L1002)、[1014](SR_code/util.py#L1014) 等）也是退出码 0 的静默失败。**本轮不patch `util.py`** —— 理由见 E-C 备注，由 `verify_sr_run.py` 兜住（见下）。

> **E-C 备注（重要）**：`verify_sr_run.py` 的三条契约（退出码 0 + SRLOG 末行 `Run finished.` + 输出 tif 存在）已经把这些 `exit(0)` 全部判成 **90 = 契约不满足 → FAILED**。所以平台不会被骗；代价只是失败原因只剩一句 `reason`，没有原始 `print` 文案。先接受，若排查期觉得不够再单开一轮 patch `util.py`。

### E-D 检索：按目录收，不按后缀收（对应 B3）

**实测依据**：SR 的"场景"是**生产编号目录**，输入文件 = `<目录名><tif_type>`（[util.py:991](SR_code/util.py#L991)），meta = `<目录名>_meta.xml`（[code_0817_prod.py:152](SR_code/code_0817_prod.py#L152)）。

改 [scene_search.py](backend/services/scene_search.py)：

1. **收件规则**（替换 [:55](backend/services/scene_search.py#L55) 的后缀判断）：
   `p.stem == p.parent.name` 且 `p.suffix.lower() in _SCENE_EXTS` 且同目录存在 `<目录名>_meta.xml`。
   这条天然排除 `_NOSR` / `_mask` / `_<suffix>` 全部派生件（它们的 stem ≠ 目录名），也排除了没有 meta 的非场景 tif。
2. **索引化**（把每次请求的全树 `rglob` 变成一次扫描 + 复用）：
   - 新增 `backend/services/scene_index.py`：扫描结果落 **服务器本地** JSON（形如 `/var/lib/sr-agent/scene_index.json`，键 = 索引文件路径，值 = `{根, 生成时间, 行数, rows}`）；**不要落在 scenes 根下**（生产根只读）。
   - 失效判据：TTL（如 300s，env `SR_SCENE_INDEX_TTL`）+ 手动刷新端点。**不**做目录 mtime 增量 —— 深树下逐目录 `stat` 本身就是慢的那一半。
   - `search_scenes()` 改为读索引；索引不存在/过期 → 重建（重建期间返回上次结果 + `"stale": true`，不阻塞请求）。
   - 索引内的行**仍要过 `paths.ensure_within`**（[paths.py:61](backend/api/paths.py#L61)），索引不是白名单的替代。
3. **`/api/scenes` 响应**加 `"indexed_at"` 字段，前端可显示"数据截至 XX:XX"。

测试：`test_search_scenes.py` 加"派生件不被收"（造 `<D>/<D>.tif` + `<D>_mask.tif` + `<D>_NOSR.tif` + `<D>_t1.tif`，断言只返回 1 行）、"无 meta 不收"、"索引 TTL 内不重扫"。

### E-E 预览缓存迁出盘阵（决策点 ①，对应 B6）

若决策 ① 选"本地盘"：

- [paths.py:118-135](backend/api/paths.py#L118-L135) `preview_jpg_path`：删除 [129-132](backend/api/paths.py#L129-L132) 的"必须在 scenes 根之下"约束，改为"预览根必须存在且可写"；
- 新增 `previews_url(path)`：预览 URL 用独立前缀 `SR_PREVIEWS_URL_PREFIX`（默认 `/previews/`），不再复用 `rel_url`（后者会因跨根而抛 `PathDeniedError`）；
- [app.py:103](backend/api/app.py#L103) `row["jpgUrl"] = paths.rel_url(jpg, root)` → `paths.previews_url(jpg)`；
- nginx 加第二个 location（见阶段 2.5）；
- 若镜像原树结构（`<previews>/<rel 目录>/<stem>.preview.jpg`），`preview_jpg_path` 的 rel 计算逻辑保留，只是根换人。

若决策 ① 选"留在盘阵内"：**本项跳过**，阶段 2.3 的 `SR_PREVIEWS_ROOT` 设为 `$PROD/.previews`（扫描时需排除该目录名）。

测试：`test_api.py` 断言 `jpgUrl` 前缀；`test_api_platform.py` 补一条"预览根在 scenes 根之外不再报错"。

### 阶段 1 收口

```bash
python -m pytest backend/tests -q                       # 期望：全绿
python SR_code/tools/gen_slurm_variant.py --check       # 期望：OK
cd frontend && npm run test:unit -- --run && npm run build
```

**产物**：一份 diff、一份全绿测试输出、更新后的 `SR_code/variants/`。

---

## 阶段 2 · 变体上机 + 服务环境

### 2.1 上传（**不覆盖生产原文件**）

```bash
cp $APP/SR_code/variants/code_0817_prod_slurm.py $BUNDLE/
cp $APP/SR_code/variants/verify_sr_run.py        $BUNDLE/
chown nginx:nginx $BUNDLE/code_0817_prod_slurm.py $BUNDLE/verify_sr_run.py
$SR_PYTHON -c "import ast;ast.parse(open('$BUNDLE/code_0817_prod_slurm.py').read());print('syntax ok')"
```

### 2.2 六项 env 写进 systemd unit

编辑 [deploy/sr-api.service](deploy/sr-api.service)，`[Service]` 段加：

```ini
Environment=SR_BUNDLE_DIR=/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes
Environment=SR_PYTHON=/run/media/root/SSD/program/anaconda/installed/envs/torch1.9.1py36/bin/python
Environment=SR_SLURM_WORK_DIR=/DiskArray/tmp/wangrz/sr_agent_work
Environment=SR_SLURM_PARTITION=gpu
Environment=SR_SLURM_GRES=1
Environment=SR_SCENES_ROOT=/DiskArray/tmp/wangrz/sr_test
Environment=SR_SR_SCRIPT=code_0817_prod_slurm.py
Environment=SR_VERIFY_SCRIPT=verify_sr_run.py
Environment=SR_PREVIEWS_ROOT=/var/lib/sr-agent/previews
Environment=SR_SCENE_INDEX=/var/lib/sr-agent/scene_index.json
```

> **阶段 2–5 的 `SR_SCENES_ROOT` 指沙箱 `$TEST`**；阶段 6 才切 `$PROD`。

### 2.3 重启与自检

```bash
systemctl daemon-reload && systemctl restart sr-api
systemctl is-active sr-api
curl -s localhost:8000/api/health          # {"ok":true,"source":"disk"}
systemctl show sr-api -p Environment | tr ' ' '\n' | grep SR_   # env 真的进去了
```

**判据**：`source":"disk"`（=沙箱根被认到，不是 fake 回退）。

### 2.4 探针（**不带 `--deep`**，不提交作业）

按 [slurm-acceptance.md](../status/slurm-acceptance.md) §A 执行 `deploy/slurm/probe_slurm.sh`。

### 2.5 nginx（仅决策点 ① 选"本地盘"时需要）

```nginx
location /previews/ { alias /var/lib/sr-agent/previews/; }
```

改完 `nginx -t && systemctl reload nginx`。
**判据**：`curl -sI localhost/previews/<某已生成预览的相对路径>` 返回 200。

**产物**：`/api/health` 输出、探针输出、`systemctl show` 的 env 回显。

---

## 阶段 3 · 裸 Slurm 冒烟（绕开平台，确认地基）

按 [slurm-acceptance.md](../status/slurm-acceptance.md) §B 执行 B1–B3：

| 步骤 | 验什么 | 判据 |
|---|---|---|
| B1 `sudo -u nginx srun` 一行命令 | `--gres=gpu:1` 到底给没给卡 | 作业内 `torch.cuda.device_count() >= 1`；`CUDA_VISIBLE_DEVICES` 非空 |
| B2 `sudo -u nginx sbatch` 手工跑一次 `$SR_PYTHON $BUNDLE/code_0817_prod_slurm.py -f <沙箱 config.xml>` | 变体在真机上能不能跑完 | 输出 tif 出现、SRLOG 末行 `Run finished.` |
| B3 读退出码文件 | 终态文件机制通不通 | `$TEST/<编号>/Debug/_SREXIT_<job_id>.txt` 存在且 `verdict=0` |

> ⚠️ **不要看 `sacct`** —— 账务未启用，恒 rc=1。终态一律读 `_SREXIT_<job_id>.txt`（[slurm-acceptance.md](../status/slurm-acceptance.md) §0.3）。
> ⚠️ B2 若在 import torch/gdal 阶段就挂，是 `--export=NONE` 把 `LD_LIBRARY_PATH` 丢了 —— 按 [run_sr.py:105-113](backend/services/run_sr.py#L105-L113) 的一条注释把 `--export=NONE` 改成 `--export=ALL`，重跑一次即可确认。

---

## 阶段 4 · 走平台提交一次真 SR（沙箱由平台自建）

**目标**：用接口而非手工，把同一件事再跑一遍。

**前置**：`SR_SANDBOX_ROOT` 已配且 nginx 可写（[deploy/README §7.5](deploy/README.md)）。

```bash
# ① 从检索里拿到沙箱场景的 opaque id（验证 E-D 的过滤真的只收场景本身）
curl -s 'localhost:8000/api/scenes?limit=5' | python -m json.tool

# ② 掩码：登录前端画一个 ROI → /api/masks；或用 curl 直接造
#    预期 draft 的 suffix 已是默认值（E-B 生效）

# ③ 提交 —— lq_path 填**生产路径**（沙箱由平台自己建，不必手工拷贝）
curl -s -XPOST localhost:8000/api/queue -H 'Content-Type: application/json' -d '{
  "lq_path":"/DiskArray/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<卫星>/<外层 …_L1_PAN>",
  "sr_scale":2,"suffix":"sr","gpu":0,"cloud_limit":80,
  "delete_ori":false,"grid_align":true}' | python -m json.tool

# ④ 看状态推进；run_dataroot 就是产物所在（= 副本路径，不是 lq_path）
curl -s localhost:8000/api/queue | python -m json.tool
```

**判据**：`201` 返回 `job_id` 非空；`/api/queue` 状态由 `SUBMITTING → PENDING → RUNNING → COMPLETED`；**`lq_path` 目录一字节没变**（`ls` 前后一致，没有 `*_NOSR.tif`）；输出 tif 与 `Debug/_SREXIT_<job_id>.txt` 都在 `run_dataroot` 下。

---

## 阶段 5 · 平台链路四条结论（逐条留证）

按 [slurm-acceptance.md](../status/slurm-acceptance.md) §D，四条各一条真实输出：

| # | 结论 | 造法 | 期望 |
|---|---|---|---|
| D1 | **静默失败不被标成成功** | 在 `$TEST` 下备一个**故意弄坏**的副本（输入 tif 改名，触发 [util.py:1014](SR_code/util.py#L1014) 的 `exit()`），`lq_path` 指它，提交 | 平台 **FAILED**（不是 COMPLETED）；`_SREXIT_*.txt` 里 `verdict=90` |
| D2 | **幂等回归** | 对**同一组参数**再提交一次 | `RESUMED_COMPLETED`（或 `RESUMED_ACTIVE`），**job_id 不变**、不产生第二个作业 |
| D3 | **云量跳过** | 在 `$TEST` 下备一个 `CloudPercent > cloud_limit` 的副本，`lq_path` 指它，提交 | 平台 **COMPLETED**；SRLOG 有 `Run skipped:`；`_SREXIT_*.txt` 里 `skip=1`；重复提交不重复投递 |
| D4 | **SSE 实时** | `curl -N localhost:8000/api/queue/events` 保持连着，另一窗口提交/等待状态变化 | 收到 `job_update` 帧，`task_id`/`state` 与 REST 一致 |

> D1 是**反向验证**：它证明的是"退出码 0 但没干成活"能被识别。D2/D3 的判据里都写着"不产生第二个作业" —— 这正是幂等层要看的东西。
>
> ⚠️ D1/D3 用的是 `$TEST` 下**手工弄坏的副本**，不是生产目录 —— 弄坏生产数据来测故障路径是反的。平台沙箱保护的是"别把好的写坏"，它不阻止你拿一个本来就坏的目录去提交。
>
> D1/D3 与阶段 4 用**不同** `lq_path`，因此任务指纹不同、沙箱目录也不同，互不干扰；沙箱每次作业都重新复制，改了源再重跑一定生效。

**产物**：D1–D4 四段原始输出 + 对应的 `_SREXIT_*.txt` 内容。

---

## 阶段 6 · 切生产（确认阶段 5 全绿后再做）

```bash
# ① 建索引（首扫，允许慢，只此一次）
SR_SCENES_ROOT=$PROD python -c "from backend.services import scene_index as si; print(si.rebuild('$PROD'))"

# ② 灰度写权：只放开一个生产编号目录给 nginx
chgrp nginx "$PROD/<年>/<月>/<日>/<编号>"
chmod g+w      "$PROD/<年>/<月>/<日>/<编号>"
```

③ 改 unit：`SR_SCENES_ROOT=$PROD`，`systemctl daemon-reload && systemctl restart sr-api`。
④ 对**那一个**生产编号走一遍阶段 4，确认预览/掩码/Debug/SR 输出四个落点都正常。
⑤ 再决定是否全树放开写权（决策点 ③）。

**回滚**：`sed -i 's#SR_SCENES_ROOT=.*#SR_SCENES_ROOT=/DiskArray/tmp/wangrz/sr_test#'` + `daemon-reload` + `restart`；变体回滚只需把 `SR_SR_SCRIPT` 改回 `code_0817_prod.py`（生产原文件从未被覆盖）。

---

## 附：本轮明确不做

- 不 patch `SR_code/util.py`（那族 `exit(0)` 由契约校验器兜住，见 E-C 备注）。
- 不做"浏览器写 Windows D 盘"（不可实现；用服务器本地盘 + 浏览器缓存替代）。
- 不做目录 mtime 增量索引（深树下逐目录 `stat` 本身就是慢的一半）。
- 不在阶段 1 之前动生产盘阵（全程沙箱）。
- 不把探针 `--deep` 的提交动作改成默认开启。

## 关联

- 真机验收命令与判据：[slurm-acceptance.md](../status/slurm-acceptance.md)
- 决策与实测值：[slurm-integration.md](../status/slurm-integration.md)
- 变体差异与上机必验项：[sr-slurm-deploy-variant.md](../sr_code/sr-slurm-deploy-variant.md)
- 部署侧（变体安装 + 六项 env + 终态判定）：[deploy/README.md](../../deploy/README.md) §七
- SR 调用契约（退出码 0/90、`Run skipped:`）：[sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md) §2
