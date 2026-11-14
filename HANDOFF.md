# HANDOFF — 跨机器接手说明

> 给另一台机器（含 macOS）clone 本仓库后接手用。记录当前进度、跑起来要什么、以及和 Windows 开发机的差异。
> 最后更新：2026-11-14 · 版本 `1.9.3`

## 0. 先看这条：clone 哪个仓库

本机最新代码在 **`dashichoi/Ant-Browser`**。`README.md` 和 badge 里写的 `black-ant/Ant-Browser` 是上游原仓库，最新提交停在 `chore(release): 发布 1.9.3`，比本机少 18 个提交（ant-console 全部工作 + 本次 macOS 修复都不在里面）。

```bash
git clone https://github.com/dashichoi/Ant-Browser.git
```

## 1. 仓库现状

- 分支：`master`，与 `origin` 同步。
- 应用本体：Wails + Go 后端 + React 前端，版本号见 `wails.json` / `README.md`（1.9.3）。
- 近期工作集中在 `tools/ant-console`（批量控制台，对账号/代理池/IP 分配/批量建实例启停做封装），不修改应用源码，只调本地 HTTP 接口。该目录的提交尚未合并进 `CHANGELOG.md` 的版本条目。

## 2. 代码里有什么、没什么

| 内容 | 是否随仓库分发 |
| --- | --- |
| Go 后端、前端源码、打包脚本 | 有 |
| `bin/xray.exe`、`bin/sing-box.exe`（Windows 运行时） | 有 |
| `bin/darwin-arm64|amd64/{xray,sing-box}` | 有（Mach-O 64-bit） |
| `bin/linux-*` 运行时 | 有 |
| 浏览器内核（`chrome/`） | **没有**，只有 `chrome/README.md`，需自备 |
| 数据库 `data/app.db`、实例数据 | **没有**，各机器独立 |
| `backup.local.yaml`、账号、订阅等敏感配置 | **没有**，被 gitignore |

浏览器内核来源见 `README.md`：fingerprint-chromium <https://github.com/adryfish/fingerprint-chromium/releases>。放到 `chrome/` 目录，在「设置 → 指纹浏览器 → 内核管理」里指向它。

## 3. macOS 首次上手

### 最快路径：云端构建 .app（不用本地装 Go/Wails）

在 GitHub 仓库页面 **Actions → Publish macOS Packages → Run workflow**，选 `arm64` 或 `amd64`。跑完后在该次运行的 Artifacts 里下载 zip，解压即用：

```bash
xattr -dr com.apple.quarantine "AntBrowser-<version>-macos-<arch>.app"
open "AntBrowser-<version>-macos-<arch>.app"
```

本机无源码改动时优先走这条；要改代码再走下面的源码方式。

### 依赖（源码方式）

```bash
xcode-select --install          # 编译 Wails 需要
brew install go node python3
go install github.com/wailsapp/wails/v2/cmd/wails@latest
export PATH="$PATH:$(go env GOPATH)/bin"
```

### 从源码跑（开发模式）

```bash
git clone https://github.com/dashichoi/Ant-Browser.git
cd Ant-Browser
chmod +x scripts/*.sh tools/ant-console/start.command bin/darwin-arm64/*   # 权限兜底
./scripts/dev.sh                # stable：先构建前端静态资源，再起 Wails
./scripts/dev.sh live           # live：起 Vite dev server 再接 Wails
```

开发模式数据落在仓库内 `data/app.db`，配置读根目录 `config.yaml`（`launch_server.port` 默认 19900）。

### 打包 .app（未签名）

```bash
bash publish/mac/publish-mac.sh --arch arm64     # Apple Silicon
bash publish/mac/publish-mac.sh --arch amd64     # Intel
```

产物在 `publish/output/`。`.app` 运行时配置与数据改落 `~/Library/Application Support/ant-browser/`，细节见 `publish/mac/README.md`。

### 批量控制台

```bash
cd tools/ant-console && python3 server.py       # http://127.0.0.1:8800
```

前置：Ant Browser 已启动，本地接口在跑（端口取 `config.yaml` 的 `launch_server.port`），内核目录里有可执行的 Chromium。

## 4. 本次为 macOS 做的修复

| 问题 | 现象 | 处理 |
| --- | --- | --- |
| 运行时二进制缺执行位 | `bin/darwin-*`、`bin/linux-arm64` 在索引里是 `100644`，clone 后 xray/sing-box 报 permission denied | 改为 `100755` |
| `start.command` 缺执行位 | macOS 双击无法启动控制台 | 改为 `100755` |
| `scripts/dev.sh` 依赖 `ss` | macOS 无 `ss`，`require_cmd ss` 直接退出 | 改为 `ss` → `lsof` → `/dev/tcp` 逐级回退 |
| `scripts/dev.sh` 硬要求 `pkg-config` | macOS 无 pkg-config，脚本退出（webkit2gtk 只在 Linux 需要） | 检测不到就跳过，不加 `webkit2_41` tag |
| `scripts/run-app.sh` 依赖 `ss`、`setsid`、`stat -c` | macOS 均不可用 | 加 lsof/`/dev/tcp` 回退、`setsid` 可选、`stat -f` 兜底 |
| `run-app.sh` 端口写死 19876 | 与 `config.yaml` 的 19900 不一致，误判端口占用 | 改为从 `config.yaml` 读取 |

## 5. macOS 已知限制

- 未签名、未公证：首次打开需 `xattr -dr com.apple.quarantine <app>`，或右键打开。
- 未在真机验证：Finder 首启、内核拉起、xray/sing-box 进程启动、真实代理连通性都还需要在本机跑一遍（`publish/mac/README.md` 的 Validation Boundary 有清单）。
- 无 DMG、无自动更新。
- 代理双栈规则见 `docs/proxy-connector-stacks.md`：`xray` 组合栈（Xray + sing-box）与 `mihomo` 独立栈二选一，不可混用。

## 6. 常用命令

```bash
./scripts/dev.sh                          # 开发模式启动
./scripts/run-app.sh --check              # 只看状态，不动进程
./scripts/run-app.sh --restart            # 重启项目版实例
bash publish/mac/publish-mac.sh --arch arm64 --keep-staging   # 打包并保留 staging
```

Windows 侧对应入口：`bat/dev.bat`、`tools/ant-console/start-all.bat`。
