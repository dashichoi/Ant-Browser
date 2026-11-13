# Ant 批量控制台（ant-console）

把原来压在 **ixBrowser** 上的那套流程，搬到 **Ant Browser** 上：账号解析 → 代理池 → 穿代理查出口国 →
IP 分配 → 批量建实例 → 批量启停。**不修改 Ant Browser 源码**，只调它自带的本地 HTTP 接口。

纯 Python 标准库，不需要装依赖。

## 为什么要这一层

Ant Browser 本身已经有完整的本地 API（`backend/internal/launchcode/`），但只暴露给命令行和脚本。
ixBrowser 那边我们已经有的东西，它没有现成的：

| 能力 | ixBrowser 版 | Ant Browser 原生 | 本工具 |
|---|---|---|---|
| 账号粘贴解析（密码/2FA/账号，顺序不限） | 有 | 无 | 有 |
| 代理池粘贴 + 规范化 | 有 | 导入时有 | 有 |
| **穿代理查出口 IP / 国家 / 城市 / 延迟** | 有 | 只有测速 | 有 |
| **按国家批量剔除（剔除后撤销）** | 有 | 无 | 有 |
| IP → 实例分配策略（每 IP N 个 / 连续 / 交替 / 不够报错） | 有 | 无 | 有 |
| 批量建实例 | 有 | 有接口无界面批量 | 有 |
| 批量打开 / 停止 / 删除 | 有 | 单实例 | 有 |

## 前置条件

1. Ant Browser 已启动（开发模式 `bat\dev.bat`，或安装版）。
2. Ant Browser 的本地接口在跑。端口读自 `config.yaml`：

```yaml
launch_server:
    port: 19900
```

   `auth.enabled: true` 时会自动带上 `X-Ant-Api-Key`，不用手工填。
3. 内核已就绪（设置 → 指纹浏览器 → 内核管理，目录下要有 `chrome.exe`）。

## 启动

```bash
cd tools/ant-console
python3 server.py            # http://127.0.0.1:8800
python3 server.py 8805       # 换端口
```

macOS 双击 `start.command`，Windows 双击 `start.bat`。

## 用法

1. **账号**：每行一个，`----` / Tab / 逗号 / `|` 分隔，顺序不限。
   ```
   Xxx12883----R32HZLJPLA37Q7PN----CaseyKumlander2486@outlook.com
   ```
   含 `@` 的当账号；16/26/32 位 Base32（`A–Z` + `2–7`）当 2FA；剩下的当密码。
   URL 字段（`https://...`）会被跳过。
2. **代理池**：`host:port:user:pass`、`socks5://user:pass@host:port`、`http://host:port:u:p`、
   无鉴权的 `host:port` 都认；`#` 开头是注释。认不出的行会指到行号。
3. **穿代理检测**：并发 8，一次最多 60 条。结果给出口 IP / 国家码 / 城市 / 延迟；
   填了目标国家码（默认 `AU`）后，点「剔除非 AU」会把这些行从代理池里删掉，点「撤销」原样还原。
4. **分配**：每个 IP 带几个实例、连续还是交替、不够时复用还是报错。先「预览分配」核对
   第 i 个账号 → 哪个 IP。
5. **批量创建**：串行发（Ant 侧本身有写锁）。
   - 指纹：默认参数取自 `config.yaml` 的 `browser.default_fingerprint_args`，
     再追加 `--fingerprint=<seed>`（**放最后**——Ant 实测多个 `--fingerprint` 时最后一个生效）。
   - 代理：带鉴权的写成 `scheme://user:pass@host:port`，Ant 会识别为需要 xray/mihomo
     桥接的代理（Chromium 的 `--proxy-server` 带不了凭据）。
   - 直连：勾「直连」则不写 proxyConfig。
6. **实例**：实时拉 Ant 的列表，勾选后批量打开 / 停止 / 删除。

## 它调了哪些 Ant 接口

| 本工具 | Ant Browser |
|---|---|
| `/api/health` | `GET /api/health` + `GET /api/profiles` |
| `/api/list` | `GET /api/profiles` |
| `/api/create` | `POST /api/profiles` |
| `/api/action open` | `POST /api/launch` `{profileId}` |
| `/api/action stop` | `POST /api/profiles/{id}/stop` |
| `/api/action delete` | `DELETE /api/profiles/{id}` |

## 已知限制

- **时区不在这里处理。** Ant 内核的 `--timezone` 在部分内核版本上实测无效，仍需
  CDP `Emulation.setTimezoneOverride` 注入（且断开即失效）。这部分留在
  `bybit-auto-register/ant_batch.py` 的 `tz --watch` 里。
- 代理检测走的是代理自己的出口，超时 12 秒；死代理会拖慢整批（并发 8，最坏 12 秒/8 条）。
- 没做登录流程：只建实例和开关窗口，登录仍在人/脚本那一侧。
- 批量创建是串行的，几百个实例会慢，属预期。

## 许可

本目录的代码是本仓库内的新增部分，跟随仓库许可（PolyForm Noncommercial 1.0.0，非商业用途）。
