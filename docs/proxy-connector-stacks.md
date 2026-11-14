# 代理连接栈：Xray 组合栈 / Mihomo 独立栈

本仓库只有两套连接栈，由 `config.yaml` 中的 `browser.default_connector_type` 决定，二选一，不混用。

| 取值 | 连接栈 | 运行时二进制 |
| --- | --- | --- |
| `xray`（默认） | Xray + sing-box 组合栈 | `bin/<os>-<arch>/xray`、`bin/<os>-<arch>/sing-box` |
| `mihomo` | 独立 Mihomo 栈 | `mihomo`，不预置，运行时从 `MetaCubeX/mihomo` Releases 下载 |

## 协议归属

`xray` 组合栈内部按协议分流，两个进程各管一段：

- Xray：`vmess` / `vless` / `trojan` / `shadowsocks` / 链式代理（前置代理 + 落地代理）
- sing-box：`hysteria2` / `tuic` / `anytls`

`mihomo` 栈：上述全部协议由 mihomo 单进程承担。

## 硬性规则

- 实例启动、测速、真实连通性、IP 健康、预热、内核下载，都必须按当前连接栈执行，不允许在 `xray` 组合栈和 `mihomo` 栈之间自动回退或混用。
- 启动失败时不要跨栈兜底：`xray` 栈里 hysteria2 起不来是 sing-box 侧问题，不能判定为「xray 不支持该协议」，也不能改调 mihomo。
- 切换栈只能通过配置显式修改，并重启实例；不要在运行中热切换。

## 升级内核

- `xray` / `sing-box` 预置在 `bin/<os>-<arch>/`，随仓库分发，可直接替换后重启实例。
- 替换后保持可执行权限，否则实例启动会失败：

```bash
chmod +x bin/darwin-arm64/xray bin/darwin-arm64/sing-box
```

- `mihomo` 由应用内下载逻辑管理，版本固定在代码内的 spec 中（`backend/app_proxy_core_download.go` 的 `proxyCoreSpec`）。

## 相关代码

- 连接栈配置字段：`backend/internal/config/config.go`（`DefaultConnectorType`）
- 内核下载与规格：`backend/app_proxy_core_download.go`
- 实例启动时选择内核：`backend/app_instance_start_proxy.go`、`backend/app_bridge_refs.go`
- 虚拟分组健康探测：`backend/app_proxy_virtual_group_health.go`
