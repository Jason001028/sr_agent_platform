# 重要配置文件位置清单

> 日期：2026-10-27 · 状态：已定（仅收录本地源码可见位置、项目交接记录及用户回传位置；线上未核实项明确标注）

## 1. 路径与证据范围

| 名称 | 路径 | 确认范围 |
|---|---|---|
| Windows 开发工作树 | `D:\BaiduNetdiskDownload\sr_agent_platform` | 当前本地工作树 |
| node81-135 应用根 `<APP>` | `/run/media/root/SSD/workspace/wangrz/sr-agent-platform` | 服务日志及项目真机文档 |
| API Python | `/opt/sr-venv/bin/python` | 用户回传 ExecStart 指向同环境的 `uvicorn` |
| API 服务入口 | `/opt/sr-venv/bin/uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000` | 用户回传确认 |

下文仓库路径均相对 Windows 工作树。Linux 路径属于部署机，不能当作 Windows 本地文件访问。
“文件路径确认”不代表该文件的全部内容或其中每个参数都已核实。不记录 API Key、密码等敏感值。
用户提供的 Claude Code 汇总也作为位置线索来源：能在当前工作树确认的条目按本地文件记录，用户级文件与未确认的服务器位置单独标为待核。发生冲突时，以用户直接回传的当前服务内容及有效配置为准。

## 2. 部署机：sr-api 主配置及全部已知 drop-in

主文件：`/etc/systemd/system/sr-api.service`。

覆盖目录：`/etc/systemd/system/sr-api.service.d/`。下表文件均已由用户的 `systemctl cat sr-api` 输出确认存在，顺序也是该输出中的加载顺序。

| 文件（均位于上述覆盖目录） | 已知用途或内容 |
|---|---|
| `10-slurm.conf` | 项目文档中的 Slurm 相关配置；当前内容未完整回传，文件名不代表当前 Executor 必为 Slurm |
| `20-agentdb.conf` | 项目文档中的 SQLite 路径配置；当前全部内容待核 |
| `30-srscript.conf` | 项目文档中的 SR 脚本配置；当前全部内容待核 |
| `40-local-exec.conf` | 项目文档中的本机 Executor 配置；当前全部内容待核 |
| `99-llm-output-budget.conf` | 回传内容含 `SR_LLM_MAX_TOKENS=4096` |
| `99-locked-dir.conf` | 文件存在，当前内容待核；名称提示与锁定场景目录有关 |
| `99-nolock.conf` | 文件存在，当前内容待核；不能仅据名称断言锁定是否关闭 |
| `llama.conf` | 回传内容含 `SR_LLM_MAX_TOKENS=512`，加载晚于 `99-llm-output-budget.conf` |
| `override.conf` | 文件存在，当前内容待核 |
| `scenes.conf` | 项目交接记录中的 `SR_SCENES_ROOT` 配置；当前全部内容待核 |

**当前已确认的预算覆盖关系**：`llama.conf` 的 512 覆盖前面文件中的 4096。4096 是否已通过修改该文件生效，尚无新的回传确认。

同名环境变量由后加载的有效配置覆盖；`99-` 不保证最后加载，字母开头的文件也可能排在它后面。
每份 drop-in 必须有 `[Service]` 段头。若配置了 `EnvironmentFile=`，还需核对对应文件；当前没有确认的 EnvironmentFile 文件路径。
修改服务配置后使用 `systemctl daemon-reload`，再重启对应服务，不能只据文件文本断言进程已采用新值。

### 旧版 systemd 可用的核查命令

以下命令不使用 `--value`，只展示路径或指定的非敏感参数。

```bash
systemctl show sr-api -p FragmentPath -p DropInPaths -p EnvironmentFiles
systemctl cat sr-api | awk '/^# \// {print} match($0, /SR_LLM_MAX_TOKENS=[0-9]+/) {print "  " substr($0, RSTART, RLENGTH)}'
systemctl show sr-api -p Environment | sed 's/^Environment=//' | tr ' ' '\n' | tr -d '"' | grep -E '^SR_LLM_(MAX_TOKENS|MODEL|TIMEOUT|INTEGRITY_RETRIES)='
```

`DropInPaths` 的显示可能被截断，逐文件位置以 `systemctl cat` 的文件标题为补充。

## 3. 部署机：llama.cpp

| 项目 | 已确认位置或配置 |
|---|---|
| 服务文件 | `/etc/systemd/system/llama-cpp.service` |
| 工作目录 | `/opt/llama.cpp` |
| 运行用户与用户组 | `llama:llama` |
| 服务程序 | `/opt/llama.cpp/bin/llama-server` |
| 模型文件 | `/opt/models/Qwen3.8-27B-Q4_0.gguf`（按用户回传的文件名记录，不推断其内部架构） |
| 多模态投影文件 | `/opt/models/mmproj-F16.gguf` |
| 监听地址 | `127.0.0.1:8080` |
| 上下文配置 | `-c 32768` |
| GPU 环境配置 | `CUDA_VISIBLE_DEVICES=0` |
| 动态库搜索目录 | `/opt/llama.cpp/lib`、`/usr/local/cuda/lib64`、`/usr/local/lib64` |

上述内容由用户回传的 unit 确认。`-c` 设置上下文容量，平台请求的 `max_tokens` 是另一项配置。
尚无已确认的 llama.cpp 其他 drop-in 或外部模型参数配置文件，不假定它们不存在。

核查入口：`systemctl cat llama-cpp`、`journalctl -u llama-cpp -n 40 --no-pager`。

## 4. 部署机：nginx、依赖和 SR 资源

| 项目 | 位置 | 确认范围 |
|---|---|---|
| nginx 平台站点配置 | `/etc/nginx/conf.d/sr-agent-platform.conf` | 项目真机部署记录；当前内容待核 |
| 离线部署前端目录 | `<APP>/dist/` | 当前打包布局；实际服务目录由 nginx `root` 决定 |
| 源码构建前端目录 | `<APP>/frontend/dist/` | 源码构建布局，不能与上行混用 |
| 更新包依赖清单 | `<APP>/backend/requirements-api.txt` | 本地当前打包脚本会携带，部署机是否已更新该包待核 |
| 源码依赖清单 | `<APP>/deploy/requirements-api.txt` | 源码部署布局 |
| 首次安装清单可能位置 | `<APP>/requirements-api.txt` | 项目离线首次安装布局记录；不可默认其内容与当前更新包一致 |
| 依赖预检脚本 | `<APP>/backend/check_runtime.py` | 本地代码存在，部署机是否已更新待核 |
| API 环境 | `/opt/sr-venv/` | 用户回传服务入口确认；不能替换为当前 shell 的 `(base)` |
| SR 生产解释器 | `/run/media/root/SSD/program/anaconda/installed/envs/torch1.9.1py36/bin/python` | 项目部署文档记录，由有效 `SR_PYTHON` 决定实际使用值 |
| SR 程序目录 | `/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes` | 仓库默认值与部署文档，由有效 `SR_BUNDLE_DIR` 决定 |
| SR 参数 YAML | `/DiskArray/tmp/wangrz/sr_utils/espan3_2026_gf04_tile500.yml` | `backend/config.py` 默认值；具体请求可传 `options_yml`，真实文件内容待核 |
| SR 工作目录 | `/DiskArray/tmp/wangrz/sr_agent_work` | 项目部署模板与文档记录，由有效 `SR_SLURM_WORK_DIR` 决定 |
| 每次作业的 XML 配置 | `<SR_SLURM_WORK_DIR>/run_sr_<suffix>_<指纹前缀>.xml` | `backend/services/run_sr.py` 生成；以任务表 `config_xml` 字段为准，不是手工维护的全局配置 |

不要用仓库 `deploy/nginx.conf` 或 `deploy/sr-api.service` 直接覆盖部署机配置；模板包含示例路径。
nginx 站点的盘阵 `alias` 应与有效 `SR_SCENES_ROOT` 一致。交接记录中对应目录为 `/DiskArray/tmp/wangrz/datahub`，当前线上值需核对 `scenes.conf` 和 nginx 配置。

## 5. 仓库：后端配置入口

| 相对路径 | 管理内容 |
|---|---|
| `backend/config.py` | LLM 地址、模型、输出预算、超时、流式与完整性重试；SR Executor、解释器、程序目录、工作目录等默认值及环境变量读取 |
| `backend/pathguard.py` | 盘阵路径白名单、Windows 盘符映射与场景路径模板 |
| `backend/services/goal_worker.py` | `SR_GOAL_*` 回合、工具调用、轮询和调度预算等读取入口 |
| `backend/services/goal_lease.py` | Worker Lease / Fencing 实现；Lease 时长配置由 `goal_worker.py` 读取 |
| `backend/services/tool_result_formatter.py` | `SR_TOOL_RESULT_*` 结果预算、存档大小及保留期配置 |
| `backend/services/skills.py` | `SR_SKILL_ROOTS` 及 Skill 根目录读取 |
| `backend/services/scene_search.py` | `SR_SCENES_ROOT` 的检索实现 |
| `backend/services/store.py` | `SR_AGENT_DB`、SQLite 表结构及持久化入口 |
| `backend/tools/contract.py` | 工具注册、参数 Schema 与单工具结果预算/Formatter 声明 |
| `deploy/requirements-api.txt` | 唯一维护的后端运行依赖清单，包括 `jsonschema` 和 `PyYAML` |
| `deploy/sr-api.service` | sr-api unit 模板；不是线上已定制的 unit |
| `deploy/nginx.conf` | nginx 站点模板；不是线上已定制的站点配置 |
| `deploy/README.md` | 安装、更新、依赖预检、配置与服务重载说明 |

平台配置通过环境变量读取；没有已确认的自动加载 `.env` 文件入口，不能仅创建 `.env` 就假定服务会读取它。
`backend/config.py` 不是全部环境变量的唯一读取位置；Goal、工具结果预算、Skill 等模块也直接读取各自变量。
平台默认 Skill 目录为 `<仓库根>/.agents/skills/`，每个 Skill 的入口为 `SKILL.md`；本地当前未发现 `.agents/` 目录，设置 `SR_SKILL_ROOTS` 时以其指定目录为准。

## 6. 仓库：前端、构建与测试

| 相对路径 | 管理内容 |
|---|---|
| `frontend/package.json` | 前端依赖和开发、构建、测试、离线打包命令 |
| `frontend/package-lock.json` | 前端依赖锁定 |
| `frontend/index.html` | 前端构建入口 |
| `frontend/vite.config.ts` | Vue/Vite、资源基址、构建输出、离线 vendor 接线和开发端口 |
| `frontend/vitest.config.ts` | 前端测试环境、范围和超时 |
| `frontend/tsconfig.json` | TypeScript 配置 |
| `frontend/scripts/package-offline.sh` | 前后端离线包内容、依赖清单映射和发布包命名 |
| `frontend/scripts/check-terms.mjs` | `npm run check:terms` 的术语检查规则 |
| `frontend/src/vendor/package.json` | 本地 vendor 目录的 CommonJS 声明 |
| `frontend/fixtures/manifest.json` | 前端测试夹具清单，不是生产数据集配置 |
| `frontend/src/lib/scene.ts` | 前后端运行时地址读取 `loadSrConfig()`、预览档位等 |
| `frontend/src/lib/brand.ts` | 产品名称等品牌常量 |
| `frontend/src/style.css` | 全局样式及主题/动效令牌 |
| `frontend/src/router/index.ts` | 页面路由 |
| `frontend/src/extensions/registry.ts` | 前端扩展路由注册入口 |
| `.e2e/package.json` | 浏览器回归测试命令和 Puppeteer 依赖 |
| `.e2e/package-lock.json` | 浏览器测试依赖锁定 |
| `.e2e/launchBrowser.js` | 测试浏览器查找、启动参数和 `SR_E2E_BROWSER` 覆盖 |
| `AGENTS.md` | 项目导航和约束入口 |
| `CLAUDE.md` | 仓库内另一份开发代理指令文件，当前清单仅确认文件位置 |
| `.codex/environments/environment.toml` | 仓库 Codex 环境配置，当前清单仅确认文件位置，不复制内容 |
| `.claude/settings.local.json` | 仓库级 Claude Code 本机设置文件，已确认存在；权限及 `additionalDirectories` 等内容来自用户汇总，本次不读取 |
| `.gitignore`、`frontend/.gitignore` | 版本控制忽略规则 |

当前仓库根目录与 `frontend/` 根目录未发现 `.env`、`.env.*`、`pyproject.toml`、`pytest.ini`、`.eslintrc*`、`eslint.config.*`。
此结论仅覆盖上述两层目录，不推断服务器、其他子目录或用户目录中也没有这些文件。

### 插件、SR 参数及开发记录补充

以下相对路径已在当前工作树确认存在。

| 相对路径 | 用途与边界 |
|---|---|
| `backend/plugins/image_quality/manifest.json` | 影像质量插件的声明、默认启用标记及前后端入口；具体机器的插件状态还受服务端持久化状态控制 |
| `deploy/slurm/.gitattributes` | Slurm 目录文件属性约束 |
| `deploy/slurm/probe_slurm.sh`、`deploy/slurm/probe_structure.sh` | 调度器与环境探针脚本，不是当前服务的配置入口；本次未运行 |
| `SR_code/espan3_2026_gf04_tile1000.yml` | SR 模型/推理参数文件，包含 `t_ht/t_wd=1000`；不能替代默认服务器 `tile500.yml`，也不能据文件名认定它正在生产中使用 |
| `SR_code/variants/code_0817_prod_slurm.provenance.json` | SR 变体来源与哈希记录，非运行参数入口 |
| `SR_code/variants/.gitattributes` | 变体文件属性约束 |
| `.learnings/LEARNINGS.md`、`.learnings/ERRORS.md`、`.learnings/FEATURE_REQUESTS.md` | 开发经验、错误与需求记录，不是服务运行配置，也不要求每次提交都修改 |

本地安装目录 `.e2e/node_modules/`、构建目录 `frontend/dist/`、发布包目录 `release/` 都不属于源配置文件。

### 浏览器内设置不是服务器配置文件

- `window.__SR_CFG__`：前端运行时 `apiBase/staticBase` 注入入口，未注入时默认同源。
- 浏览器 `localStorage`：预览档位、侧栏状态、开发者模式、本机场景隐藏记录及显示偏好等；不等于 systemd 环境变量。
- 场景库入库记录与标签：服务端 SQLite 持久化，不是浏览器标签配置文件。

## 7. 运行时数据位置（不能当成配置覆盖）

| 数据 | 已知位置或定位方式 |
|---|---|
| 平台 SQLite | 有效 `SR_AGENT_DB`；项目交接记录为 `/DiskArray/tmp/wangrz/sr_agent_db/sr_agent.db`，由 `20-agentdb.conf` 配置，当前值待核 |
| 本地默认 SQLite | 未设置 `SR_AGENT_DB` 时为进程工作目录中的 `sr_agent.db`；不能据此推断线上位置 |
| 工具完整结果引用、场景库标签、Goal/Intent | 同一平台 SQLite；无额外任意文件读取目录 |
| 每个 SR 作业的配置/脚本/日志 | 任务表中的 `config_xml`、`batch_script`、`log_dir` 字段 |
| SR 退出判定文件 | `<场景目录>/Debug/_SREXIT_<job_id>.txt` |
| API 与模型服务日志 | `journalctl -u sr-api`、`journalctl -u llama-cpp`；未确认独立日志文件路径 |

## 8. 查找顺序

1. **模型输出被截断**：`sr-api.service.d/llama.conf` 及所有后加载配置 → 有效 `SR_LLM_MAX_TOKENS` → `llama-cpp.service` 的服务参数；不要只看 `-c`。
2. **API 启动缺包**：`sr-api` 的 ExecStart 对应 Python → 当前包的 `backend/requirements-api.txt` → `check_runtime.py` 与 API 导入检查。
3. **场景列表或静态图访问异常**：`scenes.conf` 的有效场景根 → nginx 站点的 `alias/root/proxy_pass`。
4. **SR 提交或结果异常**：有效 Executor、`SR_PYTHON/SR_BUNDLE_DIR` → 本次任务 XML 和参数 YAML → 判定文件。
5. **配置文字已改但数值不变**：逐文件加载顺序、段头和 EnvironmentFile → daemon-reload → 重启 → 查看有效值与服务日志。

## 9. Windows 用户级位置（用户汇总提供，未读取内容）

这些位置不在仓库内。本次仅收录用户提供的路径与用途说明，没有访问凭据正文，也没有验证文件当前存在或其内容仍有效。

| 位置 | 用途/来源说明 |
|---|---|
| `C:\Users\lenovo\.claude\settings.json` | 用户汇总指向 Claude Code 模型路由及鉴权设置；可能包含明文凭据，只记录位置 |
| `C:\Users\lenovo\.claude\skills\` | 用户汇总中的 Claude Code Skill 安装目录；具体 Skill 是否都有 `_meta.json` 未核实 |
| `C:\Users\lenovo\.claude\projects\d--BaiduNetdiskDownload-sr-agent-platform\memory\MEMORY.md` | 用户汇总中的项目记忆索引，同目录可能有其他记忆文档 |
| `C:\Users\lenovo\.codex\config.toml` | 用户汇总中的 Codex 全局配置 |
| `C:\Users\lenovo\.codex\auth.json` | 用户汇总中的 Codex 凭据文件，只记录位置，勿提交或外传内容 |
| `C:\Users\lenovo\.codex\.codex-global-state.json` | 用户汇总中的 Codex 全局状态文件，不建议作为项目配置手工维护 |
| `C:\Users\lenovo\AppData\Local\nvm\v20.19.5` | 用户汇总中的 Node 安装目录；不是配置文件，当前 PATH 是否使用该版本待核 |

Claude Code 的用户级 Skill 目录与平台 `SR_SKILL_ROOTS` 默认目录不是同一个入口；未经显式配置，不能假定平台会加载用户级 Skill。

## 10. 用户汇总中的其他服务器线索（待核）

| 位置或线索 | 状态与使用约束 |
|---|---|
| `/etc/nginx/nginx.conf` | 用户汇总中的 nginx 系统主配置位置，未回传文件或 include 关系；平台站点的已知位置仍为 `/etc/nginx/conf.d/sr-agent-platform.conf` |
| `/opt/sr-venv/pyvenv.cfg` | 用户汇总中的 API venv 解释器来源配置位置，内容待核 |
| `/opt/sr-venv/lib/.../site-packages/` | API 依赖安装位置的模式，`...` 不是实际目录名；应使用 API Python 的 `site` 模块查询具体路径 |
| `/DiskArray/tmp/wangrz/sr_agent_work/.local_job_seq` | 用户汇总中的本地作业编号状态文件；所在工作目录以有效 `SR_SLURM_WORK_DIR` 为准，勿作为配置覆盖或重置 |
| `wangrz/mmsr_bundle_240617` | 用户汇总中的另一套 SR bundle 线索，尚无完整绝对路径及当前生效证据；不能据此覆盖 `SR_BUNDLE_DIR` 或替换已知默认值 |
| `/etc/systemd/system/llama-server.service` | 用户汇总提供的待核 unit 路径，尚未确认存在或参与当前启动；当前已确认的是 `/etc/systemd/system/llama-cpp.service` |
| `start_llama.sh` | 用户汇总中的启动脚本线索，绝对路径及是否参与当前启动待核；其中参数不能替代第 3 节直接回传的服务配置 |

具体 Python 依赖目录可只读查询：`/opt/sr-venv/bin/python -m site`。无需为了定位而修改 venv、模型服务或 SR 环境。

补充说明见 `deploy/README.md`、`docs/status/current-question.md`、`docs/status/real-machine-bringup.md` 和 `docs/knowledge/tool_result_budget.md`。
