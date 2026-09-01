# sr_agent_platform 前端离线部署（阶段2）

> 产物自包含、全离线，无任何 CDN/外网依赖。本包 = `dist/`（构建产物）+ `nginx.conf` + 本说明。
> 开发机（Windows，外网）打包 → 拷到内网机（CentOS7）解压 → nginx 托管，即可用。

## 一、开发机打包

```bash
cd frontend
npm run build            # 产出 dist/（全离线，含 vue/router/pinia/vendor 三库）
npm run package:offline  # 产出 release/sr-agent-platform-<日期>-<版本>.tar.gz
```

产物结构：

```
sr-agent-platform/
├── dist/           # ← 前端产物（Vite base:'./'，相对路径引用，随便放哪都行）
├── nginx.conf      # → /etc/nginx/conf.d/
└── README.md       # 本说明
```

## 二、内网机部署（CentOS7）

1. **装 nginx**（EPEL 源，联网装一次即可，后续部署不再需要外网）：

   ```bash
   yum install -y epel-release && yum install -y nginx
   systemctl enable nginx
   ```

2. **拷包并解压**（U 盘 / scp 均可）：

   ```bash
   mkdir -p /data/www
   tar xzf sr-agent-platform-*.tar.gz -C /data/www
   # 得到 /data/www/sr-agent-platform/dist/...
   ```

3. **放配置并生效**：

   ```bash
   cp /data/www/sr-agent-platform/nginx.conf /etc/nginx/conf.d/sr-agent-platform.conf
   nginx -t                       # 语法检查通过再 reload
   systemctl reload nginx         # 或 nginx -s reload
   ```

4. **防火墙放行 80**（按内网实际策略，通常不拦内网）：

   ```bash
   firewall-cmd --permanent --add-service=http && firewall-cmd --reload
   ```

## 三、验证

```bash
curl -s http://127.0.0.1/ | head -5        # 返回 index.html
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/viewer   # 200（SPA fallback 生效）
```

浏览器打开 `http://<内网机IP>/viewer` → 选择 TIF → canvas 出图、右下角出现 256×128 预览尺寸即成功。
F12 Network 里应只有本站请求（`./assets/*`），**没有任何外网域名**。

## 四、升级 / 回滚

- **升级**：开发机重新 `npm run build && npm run package:offline` → 拷新包 → 解压覆盖 `dist/` → `systemctl reload nginx`。
  带 hash 的资源名每次变化，Nginx 的 immutable 缓存不会卡住旧版本。
- **回滚**：保留上一版目录，改 nginx.conf 的 `root` 指向旧版 dist，reload 即回滚。

## 五、硬性约束（移植期红线）

- **vendor 已打进 dist，全离线**：pako/utif(补丁版)/geotiff 均来自 `frontend/src/vendor/`，Vite 构建时打进产物。
- **utif.js 为补丁版（cmpr 8/32946 走 pako inflate），绝不能被 npm 重装覆盖**——只能从 `src/vendor/utif.js` 本地引入。
- 真实大图只在有盘阵的内网机（外网开发机读不到），因此解码回归用 `.e2e/` 本机资产 + `frontend/fixtures/` 入库小图。
- 浏览器单次分配约 2GB、Canvas 面积上限 16384²——超限路径在阶段3 UI 上按预览降采样处理。
