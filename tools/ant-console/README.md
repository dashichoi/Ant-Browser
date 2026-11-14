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

   **实例名模板**默认 `{idx}-{u}`，即 `1-uvyjegfc`、`2-ftvxfwtsux`…：

   | 变量 | 含义 |
   |---|---|
   | `{idx}` | 从 1 开始的序号，不补零 |
   | `{seq}` | 补零序号 `001`、`002`（按名称排序也正确） |
   | `{u}` | 邮箱 `@` 前那段 |
   | `{user}` | 完整邮箱 |
   | `{date}` | 当天日期 `20261114` |

   Ant 的实例列表是「最新在前」，加了序号前缀就能一眼看出原始顺序。

   **没有账号时**：账号框留空，在下面的「数量」里填 N，直接建 N 个实例，
   实例名就是序号 `1`、`2`、`3`…（不会编造账号密码）。账号框有内容时「数量」被忽略。
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
7. **回收站**：实例区右上角会显示「回收站 N 个 · 大小」，点「清空回收站」直接彻底删除
   （连浏览器用户数据、快照、快捷码一起清，最后 VACUUM）。**Ant 开着也能清**——
   走 WAL 并发写，不需要停 Ant。正在被存活实例占用的目录会自动跳过。

   命令行版本（Ant 没开、或者要按天数过滤时用）：

   ```bash
   python purge-trash.py --app "C:/Cdoe/AntBrowser"                    # 预览
   python purge-trash.py --app "C:/Cdoe/AntBrowser" --force            # 真删
   python purge-trash.py --app "C:/Cdoe/AntBrowser" --force --keep-days 3
   ```

## 它调了哪些 Ant 接口

| 本工具 | Ant Browser |
|---|---|
| `/api/health` | `GET /api/health` + `GET /api/profiles` |
| `/api/list` | `GET /api/profiles` |
| `/api/create` | `POST /api/profiles` |
| `/api/action open` | `POST /api/launch` `{profileId}` |
| `/api/action stop` | `POST /api/profiles/{id}/stop` |
| `/api/action delete` | `DELETE /api/profiles/{id}` |

## 打开即登录（可选）

实例一打开就自动跑登录脚本，不用手动点。开关在实例区右上角「打开即登录」，默认开。

账号写在 `accounts.json`（同目录，**已被 gitignore，不会进仓库**）：

```json
{
  "jdamfyqw": { "email": "jdamfyqw@uigmail.jp", "password": "8vtNKz20n8ci" }
}
```

也可以写成 `"jdamfyqw": "邮箱----密码"`。key 是实例名，也支持用邮箱 `@` 前那段匹配。

机制：后台线程每 2 秒查一次实例状态，发现「已停止 → 运行中」就找对应账号，调
`bybit_login.py --keep-open` 登录，结果写在开关旁边的提示和 `/api/auto-login` 的 log 里。

- 关掉开关 → 只打开浏览器，不登录
- 启动时想默认关：`AUTO_LOGIN=0`
- 账号文件换位置：`ANT_ACCOUNTS=路径`
- 登录脚本换位置：`BYBIT_LOGIN_SCRIPT=路径`

## Ant 自动化脚本（另一种跑法）

`scripts/bybit-login.cjs` 是同一套逻辑的 Playwright 版，走 Ant 自带的脚本运行时：

1. Ant → 自动化脚本 → 导入本地文件 → 选 `scripts/bybit-login.cjs`
2. 选中一个实例，运行参数填 `{"email":"...","password":"..."}`
3. 运行，看日志和截图

入口是 `module.exports.run(api)`，`api` 里有 `useBrowser()`（拿到 Playwright 的 page）、
`params`、`log`、`selector`。相比命令行版，好处是有运行记录和截图；坏处是要装 Ant 的 Node/Playwright
运行时，且**必须手动点运行**（Ant 没有「启动时自动触发」的钩子）。

### 让脚本自己拿到账号

Ant 的实例**不存账号密码**，脚本的 `params` 也不会从实例继承（payload 里只有启动码）。
所以加了一条桥：建实例时勾选「账号写进标签」，会往实例标签写一条

```
acct:邮箱----密码
```

脚本运行时如果 `params` 里没填，就按 `api.selector.code`（启动码）去 `/api/profiles`
反查这条标签，自动取账号。

注意：**标签在 Ant 界面里是明文可见的**。不想暴露就取消勾选，改用手动填参数。


## 时区

建实例时填的「时区」会写成 `--timezone=<IANA>` 追加到 `fingerprintArgs`，一次启动就固定，不需要常驻进程。

**前提是内核版本够新。** 实测：

| 内核 | `--timezone` | 说明 |
|---|---|---|
| fingerprint-chromium **150** | ✅ 两条路径都对 | `Intl...timeZone` = `Australia/Sydney`，`Date.getTimezoneOffset()` = `-660` |
| fingerprint-chromium **135** | ❌ 完全无效 | 只能退回 CDP `Emulation.setTimezoneOverride`（断开即回滚） |

135 这类老内核的兜底方案仍在 `bybit-auto-register/ant_batch.py` 的 `tz --watch` 里。

## 部署内核

`chrome/<任意名字>/` 下只要存在 `chrome.exe`，Ant 启动时会自动扫描并注册（`FindCoreExecutable` 递归查找）。

```bash
# 例：fingerprint-chromium 150
unzip ungoogled-chromium_150.0.7871.186-1.1_windows_x64.zip -d chrome/chrom-150
# 确认 chrome/chrom-150/chrome.exe 存在，重启 Ant 即可
```

安装版在 `C:\Program Files\` 下时整棵树不可写（连 `data/` 都锁），内核放不进去。两个办法：
用管理员身份启动 Ant，或把整个安装目录复制到可写位置再跑（105MB，不带内核）。

## 已知限制

- 代理检测走的是代理自己的出口，超时 12 秒；死代理会拖慢整批（并发 8，最坏 12 秒/8 条）。
- 没做登录流程：只建实例和开关窗口，登录仍在人/脚本那一侧。
- 批量创建是串行的，几百个实例会慢，属预期。

## 许可

本目录的代码是本仓库内的新增部分，跟随仓库许可（PolyForm Noncommercial 1.0.0，非商业用途）。
