# real_machine_acceptance — 真机验收单（CentOS7 盘阵机 node81-135）

> 日期：2026-09-23 · 状态：已定 · 来源：原 `current-question.md` §6「真机一次出差：验收一页纸」。
>
> 用途：一次出差把「真机项」收口。本文只放**验收判据与勾选**。
> 部署步骤不在此重复 —— 见 [real-machine-bringup.md](real-machine-bringup.md)（本机实测运行手册）、
> [real-machine-bringup-adhd.md](real-machine-bringup-adhd.md)（动作版）、
> [deploy/README.md](../../deploy/README.md)（部署手册）。
> 真机配置与仓库默认值的偏差表在 [current-question.md](current-question.md) §6。
> 阶段 5 调度验收的分步命令与四条链路结论，细节以 [slurm-acceptance.md](slurm-acceptance.md) §D 为准。
>
> 图例：`[x]` 已完成（附实测记录）· `[ ]` 待勾 · `⏱` 参考耗时 · `✓=` 成功标准 · `记录:` 实测值 · `~~作废~~` 判据已失效。

---

## A 出发前（开发机）

- [x] **打包拷包**（⏱30′）· `npm run build && npm run package:offline` → 仓库根 `release/` 出**两个**包
  （`dist-*.tar.gz` 顶层 `dist/`、`backend-*.tar.gz` 顶层 `backend/`）+ 后端依赖 wheel 拷 U 盘。
  `nginx.conf` / `sr-api.service` / `requirements-api.txt` **在仓库 `deploy/`，不进包**。
  ✓= 两包解到 `$APP` 后 `$APP/dist` 与 `$APP/backend` 就位 · 记录: 2026-09-22 出包 `dist-…-6c6a36f.tar.gz`、`backend-…-6c6a36f.tar.gz`
- [x] **确认目标图**（⏱5′）· 从盘阵挑两张真实大图（GF07A03 1.11GB / KF02B04 1.78GB），记下它们在第几行。
  ✓= 知道 scene 名即可 curl 到 · 记录: 结构已确认（无压缩、每行一条带，24739×24199 级）

## B 首次部署（CentOS7）

已完成（2026-09-07/08）。步骤与实测见 [real-machine-bringup.md](real-machine-bringup.md)；
无 yum 源、Python 需 ≥3.8 两个前置见 [deploy/README.md](../../deploy/README.md) §二/§三。
四项判据当时全部通过：nginx 与 `sr-api` 均开机自启、Windows 浏览器可开 `http://10.10.81.135`、
`curl /api/health` `/api/scenes` `/api/queue` 三接口 200。

## C 阶段 4 场景验收（真机不可替代项）

- [x] **场景行元数据**（⏱10′）· curl 目标图对应行。
  ✓= `W/H` 与 ENVI 头一致、`jpgUrl` 非空或为 null（未生成前）· 记录: 2026-09-16 `SR_SCENES_ROOT` 接
  `/DiskArray/tmp/wangrz/datahub` 后出 disk 行，`/disk-array/<rel>` 返 `image/tiff`
- [ ] **首访预览生成耗时与内存**（⏱依赖图大小）· curl `/api/scenes/<id>/preview` 并计时。
  ✓= 预期几十秒内返回；期间 `ps` 记 FastAPI RSS 峰值不失控（逐条带抽读只碰采样行）。
  **口径已变（09-17 起为规则 v3）**：尺度由前端档位决定，默认各边 ÷4 + 直方图均衡，
  不再是固定的 8192 长边 / 2% 线性 · 记录:
- [x] **二次幂等 + 静态直出**（⏱10′）· 再 curl 同 id `/preview`；curl `/disk-array/<rel>`。
  ✓= 二次秒回；静态带缓存头 200 · 记录: 2026-09-16 已见 200 `image/tiff`
- [x] **浏览器出图**（⏱30′）· Win11 开 `/scenes` → 打开真实场景。
  ✓= canvas 出图、文件列表「盘阵」chip、二次秒开 · 记录: 场景库已在真机反复操作（09-20～09-22 多轮缺陷均由真机使用暴露）
  - ~~拉伸下拉禁用（tooltip「已烘焙 2% 线性」）~~ **判据作废（09-17 放开）**：盘阵场景现可换拉伸模式，
    以直方图均衡起手，拉伸是每张图各自的属性
  - 新增待实测：**8192 长边场景每次打开 / 换模式需重算全图**，性能与内存需真机确认（开发机夹具仅 1600×800）
- [ ] **越权拦截**（⏱10′）· 拼 `../` 越权 id / URL 各打一发。
  ✓= 403/404；`journalctl -u sr-api` 无越权访问告警 · 记录:
- [ ] **本地文件路径回归**（⏱30′）· 真实大图走「选择 TIF…」本地路径。
  ✓= 稀疏预览不崩、画掩码可落盘 · 记录:
  - ~~2% 线性导出 JPG 8192×8013 无条纹~~ **判据作废（09-14）**：浏览器端 JPG 导出与
    File System Access「输出目录」授权整条链路已删除，现无此功能
- [ ] **场景库「清除缓存」的写权限**（⏱10′，09-22 新增功能）· 场景库勾一个场景 →「清除选定」→ 确认；
  再点「全部清除」并输入确认词 `清除全部`。
  ✓= 明细里是 `cleared` 而不是 `failed: PermissionError`（**服务账号 `User=nginx` 对盘阵目录有无 `unlink`
  权限从未验证** —— 见 [deploy/README.md](../../deploy/README.md) 中「替换件属主变 nginx」两条注意事项）；
  清掉的行重新检索应显示「未生成」，点开重烤一次；`ls -l <场景目录>` 确认场景源 `.tif` 与
  `<编号>_mask.tif` 一个没少 · 记录:

## D 阶段 5 调度验收（真调度 · `SR_SLURM_FAKE=0`）

> **状态：随 Slurm 冻结（2026-09-14 路线中止），未执行。** 本节保留为重启 Slurm 时的存量，
> 重启后先读本节的 ⚠️ 判据与 [slurm-acceptance.md](slurm-acceptance.md) §D 再动手。
>
> ⚠️ **判据已改（09-10，取代 squeue / sacct）**：真机 `AccountingStorageType=none`，`sacct` 永久不可用。
> 终态一律读作业写的**退出码文件** `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`
> （`verdict=0` → COMPLETED；其中 `skip=1` 的云量跳过也是 COMPLETED；`verdict!=0` → FAILED）。
> **进程退出码 0 不等于成功。** 该判据对本地执行器同样适用。
> 分步命令 + 四条链路结论（静默失败不被标成成功 / 幂等回归 / 云量跳过 / SSE）见
> [slurm-acceptance.md](slurm-acceptance.md) §D。

- [ ] **前置：装变体 + 九项 env**（⏱30′）· 按 [deploy/README.md](../../deploy/README.md) §7.1：变体与
  `verify_sr_run.py` **并置**在 `$SR_BUNDLE_DIR`（**不覆盖** `code_0817_prod.py`，靠 `SR_SR_SCRIPT` 指过去），
  核对九项 env。✓= `head -3 code_0817_prod_slurm.py` 见 `GENERATED FILE` 横幅、`code_0817_prod.py`
  未被改动；`probe_slurm.sh` 除 sacct 两行外无 FAIL · 记录:
- [ ] **真实提交 + 推进**（⏱20′）· `/queue` 手填 `lq_path` + 非空 `suffix` 提交。配了 `SR_SANDBOX_ROOT`
  可直接填生产路径（平台在作业第一步自建副本，生产目录只读）。✓= sbatch 起真实作业，徽标按
  提交中 → 排队/运行中 → 完成 推进；`/api/queue` 的 `run_dataroot` 指出产物位置，其状态与退出码文件一致；
  提交前后 `lq_path` 目录 `ls` 一致 · 记录 job_id / 耗时:
- [ ] **静默失败不再被标成成功**（⏱15′）· 构造一个必然缺 SRLOG 的任务（如 SC 步目录里没有 `<目录名>.tif`）。
  ✓= 退出码文件 `verdict=90`、平台显示 **FAILED**、同参数重投返回 `SUBMITTED`（**不是** `RESUMED_COMPLETED`）· 记录:
- [ ] **幂等回归**（⏱10′）· 同参数连投两次。✓= 活跃期 `RESUMED_ACTIVE`、终态 `RESUMED_COMPLETED`，
  两次都不产生第二个 job_id · 记录:
- [ ] **云量跳过**（⏱10′）· 提交一个 `cloud_limit` 必然触发的任务。✓= COMPLETED、SRLOG 末行 `Run skipped:`、
  再投为 `RESUMED_COMPLETED`（不被反复重投）· 记录:
- [ ] **取消**（⏱10′）· 提交一个会排队的作业 → 点取消。✓= scancel 生效、状态变失败/取消 · 记录:
- [ ] **重启校准**（⏱10′）· `systemctl restart sr-api` → 刷新 `/queue`。
  ✓= 内存缓存丢失后 `GET /api/queue` 当场校准（读退出码文件），已完成作业仍显示「完成」 · 记录:

## E 掩码 → SR 真链 + SSE 长连

- [ ] **掩码落盘 + ENVI 核对**（⏱30′）· 真实场景画矩形掩码 →「提交 SR」→ 落盘原图目录。
  ✓= ENVI 打开 `<stem>_mask.tif` 区域位置正确（在 JPG 上画 → 全分辨率落点）+ `<stem>_mask.txt` 质心可读；
  并用带 `MaskPath` 的真作业确认 0817 消费链一致 · 记录:
- [ ] **《待修复清单》写回**（⏱20′）· 盘阵上放一份**真实清单**（GBK，先备份）→ `/viewer` 导入 →
  页脚粘完整路径（`W:\…\待修复清单.txt`）→「同步」。
  ✓= 文件真被改写：上半部分逐字未动、下半部分只剩终态行、编码仍是 GBK（`file -i` 或记事本「另存为」看编码）；
  `ls -l` 确认替换后**属主变成 nginx**、权限位未变 · 记录:
- [ ] **SSE 长连**（⏱30′，可挂后台）· 临时开 `SR_LLM_MOCK=1` 或挂 `/api/queue/events`，经 nginx 连 10min+。
  ✓= 事件逐帧到达不攒批、断链后前端重连正常、无 502。
  ⚠️ 服务端**不发心跳帧**（2026-09-22 核实，`platform.py::_broadcast` 只推业务帧），
  不要拿「有没有心跳」判活 —— 判活看 `/queue` 是否仍能推 `job_update`；真断了前端会自行退避重连
  （2s → 30s 封顶）并重拉一次 `GET /api/queue` 校准 · 记录:

## F 决策点（标灰，不阻塞；当天记结论）

- [ ] **LLM 底座拍板** · 内网有无第二台 GPU → vLLM + Qwen / Ollama + Qwen / 第三方端点。
  ✓= 出结论即可 · 结论:
- [ ] **上线还差的两件事记账** · 配好端点后真 LLM 复测一轮 `/chat`；产物回到用户（队列「完成」之后的
  目录 / 对比 / 失败诊断）+ 多人权限是否需要（M2）。✓= 列进 [current-question.md](current-question.md) §4 · 结论:

---

## 附 部署期两条已知坑

均为 2026-09-16 核实时记录，改动前先看这一节：

- **systemd drop-in 必须有 `[Service]` 段头。** 只写 `Environment=…` 一行会被 systemd 静默忽略整个文件
  （`systemctl show` 里看不到该变量、接口照旧 `source:fake`）。正确写法：
  `printf '[Service]\nEnvironment=SR_SCENES_ROOT=<路径>\n' > $D/scenes.conf`，再
  `systemctl daemon-reload && systemctl restart sr-api`。
- **`/etc/nginx/nginx.conf` 里没有站点配置。** 该文件只有全局段 + `include conf.d/*.conf`，
  `location` / `alias` / `proxy_pass` 全在 `/etc/nginx/conf.d/sr-agent-platform.conf`。改动前先
  `nginx -T | grep '^# configuration file'` 找到真身；对着 `nginx.conf` 做 `sed` 是空操作。
  URL 前缀保持仓库默认 `/disk-array/`，**不要**改成 `/DiskArray/`（后者会被 `location /` 兜底成
  `index.html`，返回 200 `text/html`，看着像成功）。
