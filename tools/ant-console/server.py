#!/usr/bin/env python3
"""Ant Browser 批量控制台（外挂层，不修改 Ant Browser 源码）。

复用 Ant Browser 自带的本地 HTTP API（launch_server，默认 19900）：

  GET    /api/health                 探活
  GET    /api/profiles               实例列表
  POST   /api/profiles               创建实例（proxyConfig / fingerprintArgs / launchArgs / tags）
  DELETE /api/profiles/{id}          删除实例
  POST   /api/profiles/{id}/stop     停止实例
  POST   /api/launch                 启动实例（profileId）

本服务做的事，是 ixBrowser 侧那套流程（账号解析 / 代理池 / 穿代理查出口国 / IP 分配 /
批量建窗 / 批量启停）在 Ant 上的等价实现。纯标准库，不需要装依赖。

启动：python3 server.py        默认 http://127.0.0.1:8800
      ANT_PORT=19900 python3 server.py
"""
import concurrent.futures
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import struct
import sys
import threading
import time
from datetime import datetime, timedelta
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent.parent                     # Ant-Browser/
CONFIG_PATH = APP_ROOT / "config.yaml"

LOCAL_PORT = int(os.environ.get("ANT_CONSOLE_PORT") or 8800)
DEFAULT_ANT_PORT = 19900
CHECK_CONCURRENCY = 8
CHECK_LIMIT = 60

# ------------------------------------------------------------------ 配置读取
# 只认 config.yaml 里的几个键，用不着完整 YAML 解析器。
def _read_config_text():
    for path in (CONFIG_PATH, ROOT / "config.yaml"):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _slice_block(text, key, indent=0):
    """取出 `key:` 下面的子块（同缩进或更深），返回 [(缩进, 行)]。"""
    lines = text.splitlines()
    out, capturing = [], False
    base = 0
    for line in lines:
        if not capturing:
            m = re.match(r"^(\s*)" + re.escape(key) + r"\s*:\s*$", line)
            if m:
                capturing = True
                base = len(m.group(1))
            continue
        if not line.strip():
            out.append((0, ""))
            continue
        cur = len(line) - len(line.lstrip())
        if cur <= base and line.strip():
            break
        out.append((cur, line.strip()))
    return out


def load_app_config():
    """从 Ant Browser 的 config.yaml 取 launch_server 端口/鉴权与默认指纹参数。"""
    text = _read_config_text()
    cfg = {
        "ant_port": DEFAULT_ANT_PORT,
        "api_key": "",
        "auth_header": "X-Ant-Api-Key",
        "default_fingerprint_args": [],
        "default_launch_args": [],
        "user_data_root": "data",
        "core_id": "default",
        "source": str(CONFIG_PATH) if CONFIG_PATH.exists() else "",
    }
    server_block = _slice_block(text, "launch_server")
    in_auth = False
    for _, line in server_block:
        m = re.match(r"port\s*:\s*(\d+)", line)
        if m:
            cfg["ant_port"] = int(m.group(1))
        # auth 是 launch_server 的子块
        if re.match(r"auth\s*:\s*$", line):
            in_auth = True
            continue
        if in_auth:
            if not line:
                continue
            m = re.match(r"(enabled|api_key|header)\s*:\s*(.+)$", line)
            if m:
                k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
                if k == "api_key":
                    cfg["api_key"] = v
                elif k == "header" and v:
                    cfg["auth_header"] = v

    browser_block = _slice_block(text, "browser")
    section = None
    for _, line in browser_block:
        if not line:
            continue
        if re.match(r"user_data_root\s*:\s*(\S+)", line):
            cfg["user_data_root"] = re.match(r"user_data_root\s*:\s*(\S+)", line).group(1)
        elif re.match(r"default_fingerprint_args\s*:\s*$", line):
            section = "fp"
            continue
        elif re.match(r"default_launch_args\s*:\s*$", line):
            section = "launch"
            continue
        elif re.match(r"[a-zA-Z_]+:\s*", line) and not line.startswith("-"):
            if not line.startswith("-"):
                section = None
            continue
        if section and line.startswith("- "):
            cfg["default_fingerprint_args" if section == "fp" else "default_launch_args"].append(line[2:].strip())

    for _, line in _slice_block(text, "cores"):
        m = re.match(r"core_id\s*:\s*(\S+)", line)
        if m:
            cfg["core_id"] = m.group(1)
            break
    return cfg


CFG = load_app_config()
CFG_LOCK = threading.Lock()


def guess_app_dir():
    """Ant 的安装目录（要能找到 data/app.db）。安装版不在仓库里，所以按常见位置猜一遍。"""
    env = os.environ.get("ANT_APP_DIR")
    if env and (Path(env) / "data" / "app.db").exists():
        return Path(env)
    candidates = [
        APP_ROOT,
        Path("C:/Cdoe/AntBrowser"),
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Ant Browser",
    ]
    for cand in candidates:
        try:
            if (cand / "data" / "app.db").exists():
                return cand
        except OSError:
            continue
    return APP_ROOT


ANT_APP_DIR = guess_app_dir()


def ant_base():
    return "http://127.0.0.1:%d" % ANT_PORT


ANT_PORT = int(os.environ.get("ANT_PORT") or (CFG["ant_port"] if 0 < CFG["ant_port"] < 65536 else DEFAULT_ANT_PORT))

# ------------------------------------------------------------------ Ant API 客户端
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class AntError(RuntimeError):
    pass


def ant_request(method, path, body=None, timeout=20.0):
    url = ant_base() + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if CFG.get("api_key"):
        headers[CFG.get("auth_header") or "X-Ant-Api-Key"] = CFG["api_key"]
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
            msg = payload.get("error") or payload.get("message") or str(payload)
        except Exception:
            msg = raw.decode("utf-8", "replace")[:200] or str(exc)
        raise AntError("Ant API %s %s -> HTTP %d：%s" % (method, path, exc.code, msg))
    except (urllib.error.URLError, ConnectionError, socket.timeout) as exc:
        raise AntError("连不上 Ant Browser（%s）。请先启动 Ant Browser，"
                       "并在设置里确认「本地接口」端口是 %d。" % (ant_base(), ANT_PORT))
    try:
        return json.loads(raw.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        raise AntError("Ant API 返回的不是 JSON：%s" % raw[:200])


def ant_list_profiles():
    payload = ant_request("GET", "/api/profiles")
    return payload.get("items") or []


def ant_create_profile(profile, auto_launch=False):
    body = {"profile": profile, "autoLaunch": bool(auto_launch)}
    return ant_request("POST", "/api/profiles", body)


def ant_delete_profile(profile_id):
    return ant_request("DELETE", "/api/profiles/" + urllib.parse.quote(profile_id, safe=""))


def ant_stop_profile(profile_id):
    return ant_request("POST", "/api/profiles/" + urllib.parse.quote(profile_id, safe="") + "/stop", {})


def ant_launch_profile(profile_id):
    # matchMode 只认 unique / first / all；按 profileId 精确启动用 unique
    return ant_request("POST", "/api/launch", {"profileId": profile_id, "matchMode": "unique"})


# ------------------------------------------------------------------ 账号解析
SEP_RE = re.compile(r"\s*(?:----|\|\||\||\t|,)\s*")
BASE32_RE = re.compile(r"^[A-Z2-7]{16}$|^[A-Z2-7]{26}$|^[A-Z2-7]{32}$")


def parse_accounts(text, count=0):
    rows = []
    for idx, raw in enumerate(str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in SEP_RE.split(line) if p]
        account = password = totp = ""
        for part in parts:
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", part):
                continue                                  # 邮箱登录地址之类，不是账号也不是密码
            if "@" in part and not account:
                account = part
            elif BASE32_RE.match(part) and not totp:
                totp = part
            elif not password:
                password = part
        if not account:
            account = parts[0] if parts else line
        rows.append({"line": idx, "account": account, "password": password,
                     "totp": totp, "raw": line})

    # 没填账号就只按数量建：不编造账号，实例名直接用序号
    if not rows and count:
        try:
            count = max(0, min(int(count), GENERATE_LIMIT))
        except (TypeError, ValueError):
            count = 0
        for i in range(count):
            rows.append({"line": i + 1, "account": "", "password": "",
                         "totp": "", "raw": "", "placeholder": True})
    return rows


GENERATE_LIMIT = 500


# ------------------------------------------------------------------ 代理池
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def parse_proxy_line(line):
    """一行代理 → dict。认：host:port:user:pass / scheme://host:port:user:pass /
    scheme://user:pass@host:port / host:port user pass / host:port。"""
    original = str(line or "").strip()
    if not original or original.startswith("#"):
        return None
    scheme = ""
    m = _SCHEME_RE.match(original)
    if m:
        scheme = m.group(0)[:-3].lower()
    raw = _SCHEME_RE.sub("", original).split("#")[0].strip()
    if not raw:
        return None

    user = pwd = ""
    if "@" in raw:
        cred, _, hostpart = raw.rpartition("@")
        if ":" in cred:
            user, pwd = cred.split(":", 1)
        else:
            user = cred
        parts = hostpart.split(":")
        if len(parts) < 2:
            return None
        host, port = parts[0], parts[1]
    elif ":" in raw:
        parts = [p.strip() for p in raw.split(":")]
        if len(parts) < 2 or not re.fullmatch(r"\d{1,5}", parts[1] or ""):
            parts = [p.strip() for p in re.split(r"[\s,;|]+", raw) if p.strip()]
        parts = [p for p in parts if p != ""]
        if len(parts) < 2:
            return None
        if len(parts) > 4:
            parts = parts[:3] + [":".join(parts[3:])]
        host, port = parts[0], parts[1]
        if len(parts) > 2:
            user = parts[2]
        if len(parts) > 3:
            pwd = parts[3]
    else:
        parts = [p for p in re.split(r"[\s,;|]+", raw) if p.strip()]
        if len(parts) < 2:
            return None
        host, port = parts[0], parts[1]
        if len(parts) > 2:
            user = parts[2]
        if len(parts) > 3:
            pwd = parts[3]

    if not host or " " in host or not re.fullmatch(r"\d{1,5}", str(port) or ""):
        return None
    if not (0 < int(port) < 65536):
        return None
    ptype = scheme or "socks5"
    if ptype == "https":
        ptype = "http"
    if ptype not in ("socks5", "http"):
        ptype = "socks5"
    return {"type": ptype, "host": host, "port": int(port), "user": user, "pass": pwd,
            "raw": original}


def proxy_to_config(p):
    """外部代理 → Ant 的 proxyConfig 字符串。

    带鉴权时用 `scheme://user:pass@host:port`：Ant 会识别为需要 xray/mihomo 桥接的代理
    （Chromium 的 --proxy-server 带不了凭据）。
    """
    auth = ""
    if p.get("user"):
        auth = urllib.parse.quote(str(p["user"]), safe="")
        if p.get("pass"):
            auth += ":" + urllib.parse.quote(str(p["pass"]), safe="")
        auth += "@"
    return "%s://%s%s:%d" % (p["type"], auth, p["host"], p["port"])


def parse_proxy_pool(text):
    pool, errors = [], []
    for idx, raw in enumerate(str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        p = parse_proxy_line(line)
        if not p:
            errors.append("第 %d 行认不出：%s" % (idx, line[:80]))
        else:
            pool.append(p)
    return pool, errors


# ---------------------------------------------------------------- 出口 IP 检测
SOCKS5_ERRORS = {1: "代理本身故障", 2: "规则不允许", 3: "网络不可达", 4: "主机不可达",
                 5: "目标拒绝连接", 6: "TTL 过期", 7: "不支持的命令", 8: "不支持的地址类型"}


def _socks5_tunnel(p, host, port, timeout=12.0):
    sock = socket.create_connection((p["host"], p["port"]), timeout)
    sock.settimeout(timeout)
    try:
        user, pwd = p.get("user") or "", p.get("pass") or ""
        sock.sendall(b"\x05" + (b"\x02\x00\x02" if user else b"\x01\x00"))
        r = sock.recv(2)
        if len(r) < 2 or r[0] != 5:
            raise RuntimeError("对方不是 SOCKS5 代理")
        if r[1] == 2:
            u, w = user.encode(), pwd.encode()
            if len(u) > 255 or len(w) > 255:
                raise RuntimeError("代理账号或密码过长")
            sock.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(w)]) + w)
            a = sock.recv(2)
            if len(a) < 2 or a[1] != 0:
                raise RuntimeError("SOCKS5 账号或密码不对")
        elif r[1] != 0:
            raise RuntimeError("代理不接受无认证/密码认证（方式 %d）" % r[1])

        hb = host.encode()
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", port))
        rep = sock.recv(4)
        if len(rep) < 4:
            raise RuntimeError("代理没有响应")
        if rep[1] != 0:
            raise RuntimeError("代理拒绝连接：%s" % SOCKS5_ERRORS.get(rep[1], "错误码 %d" % rep[1]))
        atyp = rep[3]
        if atyp == 1:
            sock.recv(4)
        elif atyp == 4:
            sock.recv(16)
        else:
            n = sock.recv(1)
            if n:
                sock.recv(n[0])
        sock.recv(2)
        return sock
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _http_tunnel(p, host, port, timeout=12.0):
    sock = socket.create_connection((p["host"], p["port"]), timeout)
    sock.settimeout(timeout)
    try:
        auth = ""
        if p.get("user"):
            import base64
            token = base64.b64encode(("%s:%s" % (p["user"], p.get("pass") or "")).encode()).decode()
            auth = "Proxy-Authorization: Basic %s\r\n" % token
        sock.sendall(("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n%s\r\n"
                      % (host, port, host, port, auth)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("代理提前断开")
            head += chunk
            if len(head) > 8192:
                raise RuntimeError("代理返回头部异常")
        first = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 200" not in first:
            if " 407" in first:
                raise RuntimeError("HTTP 代理账号或密码不对")
            raise RuntimeError("代理拒绝 CONNECT：%s" % first[:60])
        return sock
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _tunnel(p, host, port, timeout=12.0):
    if p["type"] == "http":
        return _http_tunnel(p, host, port, timeout)
    return _socks5_tunnel(p, host, port, timeout)


class _TunnelHTTPConnection(http.client.HTTPConnection):
    def __init__(self, p, host, port=80, timeout=12.0):
        super().__init__(host, port, timeout=timeout)
        self._p = p

    def connect(self):
        self.sock = _tunnel(self._p, self.host, self.port, self.timeout)


class _TunnelHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, p, host, port=443, timeout=12.0):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._p = p

    def connect(self):
        raw = _tunnel(self._p, self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _http_json(url, proxy=None, timeout=12.0):
    u = urllib.parse.urlparse(url)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    if u.scheme == "https":
        conn = (_TunnelHTTPSConnection(proxy, u.hostname, u.port or 443, timeout) if proxy
                else http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout,
                                                 context=ssl.create_default_context()))
    else:
        conn = (_TunnelHTTPConnection(proxy, u.hostname, u.port or 80, timeout) if proxy
                else http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout))
    try:
        conn.request("GET", path, headers={"User-Agent": "ant-console/1.0", "Accept": "application/json"})
        resp = conn.getresponse()
        raw = resp.read(200000)
        if resp.status != 200:
            raise RuntimeError("查询接口返回 HTTP %d" % resp.status)
        return json.loads(raw.decode("utf-8", "replace"))
    finally:
        conn.close()


def _err_text(exc):
    if isinstance(exc, socket.timeout) or "timed out" in str(exc):
        return "超时（代理可能已失效）"
    if isinstance(exc, ConnectionRefusedError):
        return "连不上代理（端口拒绝）"
    if isinstance(exc, socket.gaierror):
        return "代理地址解析失败"
    if isinstance(exc, ssl.SSLError):
        return "TLS 握手失败"
    if isinstance(exc, ConnectionResetError):
        return "连接被重置（代理断开）"
    return str(exc)[:160] or exc.__class__.__name__


# 国家码 → 中文名。ipwho.is 只给英文，这里本地翻一遍；ip-api 能直接返回中文。
COUNTRY_ZH = {
    "AU": "澳大利亚", "NZ": "新西兰", "US": "美国", "CA": "加拿大", "GB": "英国",
    "IE": "爱尔兰", "DE": "德国", "FR": "法国", "NL": "荷兰", "BE": "比利时",
    "LU": "卢森堡", "CH": "瑞士", "AT": "奥地利", "IT": "意大利", "ES": "西班牙",
    "PT": "葡萄牙", "SE": "瑞典", "NO": "挪威", "FI": "芬兰", "DK": "丹麦",
    "IS": "冰岛", "PL": "波兰", "CZ": "捷克", "SK": "斯洛伐克", "HU": "匈牙利",
    "RO": "罗马尼亚", "BG": "保加利亚", "GR": "希腊", "HR": "克罗地亚", "RS": "塞尔维亚",
    "SI": "斯洛文尼亚", "LT": "立陶宛", "LV": "拉脱维亚", "EE": "爱沙尼亚", "UA": "乌克兰",
    "BY": "白俄罗斯", "MD": "摩尔多瓦", "RU": "俄罗斯", "TR": "土耳其", "CY": "塞浦路斯",
    "MT": "马耳他", "JP": "日本", "KR": "韩国", "CN": "中国", "HK": "中国香港",
    "MO": "中国澳门", "TW": "中国台湾", "SG": "新加坡", "MY": "马来西亚", "TH": "泰国",
    "VN": "越南", "PH": "菲律宾", "ID": "印度尼西亚", "IN": "印度", "PK": "巴基斯坦",
    "BD": "孟加拉国", "LK": "斯里兰卡", "NP": "尼泊尔", "KH": "柬埔寨", "MM": "缅甸",
    "MN": "蒙古", "KZ": "哈萨克斯坦", "UZ": "乌兹别克斯坦", "GE": "格鲁吉亚",
    "AM": "亚美尼亚", "AZ": "阿塞拜疆", "AE": "阿联酋", "SA": "沙特阿拉伯",
    "QA": "卡塔尔", "KW": "科威特", "IL": "以色列", "JO": "约旦", "LB": "黎巴嫩",
    "IQ": "伊拉克", "IR": "伊朗", "EG": "埃及", "ZA": "南非", "NG": "尼日利亚",
    "KE": "肯尼亚", "MA": "摩洛哥", "BR": "巴西", "AR": "阿根廷", "CL": "智利",
    "CO": "哥伦比亚", "PE": "秘鲁", "VE": "委内瑞拉", "EC": "厄瓜多尔", "UY": "乌拉圭",
    "PY": "巴拉圭", "BO": "玻利维亚", "MX": "墨西哥", "PA": "巴拿马", "CR": "哥斯达黎加",
    "GT": "危地马拉", "DO": "多米尼加", "CU": "古巴", "JM": "牙买加", "TT": "特立尼达和多巴哥",
    "FJ": "斐济", "PG": "巴布亚新几内亚",
}


def zh_country(code, fallback=""):
    return COUNTRY_ZH.get((code or "").upper(), fallback or "")


def _looks_zh(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def geo_lookup(proxy=None, ip="", timeout=12.0):
    """查归属地。带 proxy 时查的是代理出口 IP；不带时查指定 ip（或本机 IP）。

    先用 ip-api.com 取中文结果（lang=zh-CN），失败再退回 ipwho.is（英文，用本地表翻国家）。
    """
    started = time.time()
    fields = "status,message,country,countryCode,regionName,city,isp,org,query"

    # 1) ip-api：可以直接给中文
    try:
        data = _http_json("http://ip-api.com/json/%s?fields=%s&lang=zh-CN" % (ip, fields), proxy, timeout)
        if data.get("status") != "success":
            raise RuntimeError(data.get("message") or "ip-api 查询失败")
        code = str(data.get("countryCode") or "").upper()
        country = data.get("country") or ""
        return {
            "ip": data.get("query") or ip,
            "code": code,
            "country": country,
            "countryZh": country if _looks_zh(country) else zh_country(code, country),
            "region": data.get("regionName") or "",
            "city": data.get("city") or "",
            "isp": data.get("isp") or data.get("org") or "",
            "ms": int((time.time() - started) * 1000),
            "source": "ip-api",
        }
    except Exception as first:                                    # noqa: BLE001
        # 2) ipwho.is 兜底（HTTPS 更稳，但只有英文）
        data = _http_json("https://ipwho.is/%s" % ip if ip else "https://ipwho.is/", proxy, timeout)
        if data.get("success") is False:
            raise RuntimeError("归属地查询失败：%s" % (data.get("message") or first))
        conn = data.get("connection") or {}
        code = str(data.get("country_code") or data.get("countryCode") or "").upper()
        country = data.get("country") or ""
        return {
            "ip": data.get("ip") or data.get("query") or ip,
            "code": code,
            "country": country,
            "countryZh": zh_country(code, country),
            "region": data.get("region") or data.get("regionName") or "",
            "city": data.get("city") or "",
            "isp": conn.get("isp") or conn.get("org") or data.get("isp") or data.get("org") or "",
            "ms": int((time.time() - started) * 1000),
            "source": "ipwho.is",
        }


def check_one(raw_line):
    p = parse_proxy_line(raw_line)
    if not p:
        return {"raw": raw_line, "ok": False, "error": "认不出这行代理"}
    try:
        info = geo_lookup(p)
        out = {"raw": raw_line, "ok": True}
        out.update(info)
        return out
    except Exception as exc:                                     # noqa: BLE001
        return {"raw": raw_line, "ok": False, "error": _err_text(exc)}


# ------------------------------------------------------------------ 分配
def allocate(accounts, proxies, per_ip=1, mode="seq", on_short="reuse"):
    """返回 (assignment, errors, warnings)。assignment[i] = proxies 下标。"""
    n, m = len(accounts), len(proxies)
    errors, warnings = [], []
    if n == 0:
        errors.append("没有账号")
    if m == 0:
        errors.append("代理池是空的")
    if errors:
        return [], errors, warnings
    per_ip = max(1, int(per_ip or 1))
    capacity = m * per_ip
    if n > capacity:
        if on_short == "error":
            errors.append("IP 不够：%d 个账号需要 %d 个 IP（每 IP %d 实例），只有 %d 个"
                          % (n, -(-n // per_ip), per_ip, m))
            return [], errors, warnings
        warnings.append("IP 不够，后面的账号会循环复用同一批 IP（%d 个账号 / %d 个 IP）" % (n, m))

    idx = []
    if mode == "interleave":
        for i in range(n):
            idx.append(i % m)
    else:
        for i in range(n):
            idx.append(min(i // per_ip, m - 1))
    return idx, errors, warnings


def build_profile_name(template, acct, seq, width=3):
    user = acct.get("account") or ""
    local = user.split("@")[0] if "@" in user else user
    if not local:
        return str(seq)                              # 没账号：名字就用序号，不编造
    name = str(template or "{idx}-{u}")
    repl = {
        "{user}": user,
        "{u}": local,
        "{idx}": str(seq),                       # 从 1 开始，不补零
        "{seq}": str(seq).zfill(width),          # 001 这种，方便排序
        "{date}": time.strftime("%Y%m%d"),
    }
    for k, v in repl.items():
        name = name.replace(k, v)
    return name.strip() or local or str(seq)


def build_fingerprint_args(seed, extra):
    """默认指纹参数 + 本次 seed。seed 放最后：Ant 实测多个 --fingerprint 时最后一个生效。"""
    args = []
    for a in CFG.get("default_fingerprint_args") or []:
        if a.lower().startswith("--fingerprint="):
            continue
        args.append(a)
    if not args:
        args = ["--fingerprint-brand=Chrome", "--fingerprint-platform=windows",
                "--disable-non-proxied-udp", "--fingerprinting-canvas-image-data-noise",
                "--fingerprinting-client-rects-noise"]
    for a in (extra or []):
        a = str(a).strip()
        if a and a not in args:
            args.append(a)
    if seed:
        args.append("--fingerprint=%s" % seed)
    return args


# ------------------------------------------------------------------ 回收站
# Ant 的删除是软删除（deleted_at），界面只能逐个「彻底删除」，72 小时才自动清。
# 这里直接操作同一份 sqlite + 实例目录，复刻 PermanentlyDelete 的顺序。
def _db_path():
    return ANT_APP_DIR / "data" / "app.db"


def _dir_size(path):
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f%s" % (n, unit)
        n /= 1024
    return "%.1fTB" % n


def _resolve_dir(udd, pid, data_root):
    p = Path(udd or pid)
    target = p if p.is_absolute() else (data_root / (udd or pid))
    try:
        return target.resolve()
    except OSError:
        return data_root / (udd or pid)


def _alive_dirs(conn, data_root):
    alive = set()
    for r in conn.execute("select profile_id, user_data_dir from browser_profiles "
                          "where deleted_at is null or deleted_at = ''"):
        alive.add(_resolve_dir(r["user_data_dir"], r["profile_id"], data_root))
    return alive


def trash_snapshot():
    db = _db_path()
    if not db.exists():
        return {"ok": False, "error": "找不到数据库：%s（用 ANT_APP_DIR 指定 Ant 目录）" % db}
    data_root = (ANT_APP_DIR / "data").resolve()
    conn = sqlite3.connect(str(db), timeout=8)
    conn.row_factory = sqlite3.Row
    try:
        alive = _alive_dirs(conn, data_root)
        rows = conn.execute(
            "select profile_id, profile_name, user_data_dir, deleted_at from browser_profiles "
            "where deleted_at is not null and deleted_at <> '' order by deleted_at"
        ).fetchall()
        items, total = [], 0
        for r in rows:
            target = _resolve_dir(r["user_data_dir"], r["profile_id"], data_root)
            snap = data_root / "snapshots" / r["profile_id"]
            size = _dir_size(target) + _dir_size(snap)
            inside = (data_root in target.parents) or target == data_root
            busy = target in alive
            total += size
            items.append({"id": r["profile_id"], "name": r["profile_name"] or "(未命名)",
                          "deletedAt": r["deleted_at"][:19], "dir": target.name,
                          "bytes": size, "size": _human(size),
                          "deletable": bool(inside and not busy),
                          "blocked": "仍被存活实例占用" if busy else ("" if inside else "路径不在 data 下")})
        return {"ok": True, "count": len(items), "bytes": total, "size": _human(total),
                "items": items, "appDir": str(ANT_APP_DIR)}
    finally:
        conn.close()


def trash_purge(keep_days=0):
    db = _db_path()
    if not db.exists():
        return {"ok": False, "error": "找不到数据库：%s" % db}
    data_root = (ANT_APP_DIR / "data").resolve()
    cutoff = ""
    if keep_days and keep_days > 0:
        cutoff = (datetime.now() - timedelta(days=float(keep_days))).strftime("%Y-%m-%dT%H:%M:%S")
    conn = sqlite3.connect(str(db), timeout=8)
    conn.row_factory = sqlite3.Row
    deleted, failed, freed = [], [], 0
    try:
        alive = _alive_dirs(conn, data_root)
        rows = conn.execute(
            "select profile_id, profile_name, user_data_dir, deleted_at from browser_profiles "
            "where deleted_at is not null and deleted_at <> '' order by deleted_at"
        ).fetchall()
        for r in rows:
            if cutoff and r["deleted_at"][:19] < cutoff:
                continue
            pid = r["profile_id"]
            target = _resolve_dir(r["user_data_dir"], pid, data_root)
            inside = (data_root in target.parents) or target == data_root
            if target in alive or not inside or target == data_root:
                failed.append({"name": r["profile_name"], "error": "目录受保护，已跳过"})
                continue
            size = _dir_size(target)
            try:
                if target.exists():
                    shutil.rmtree(target)
                snap = data_root / "snapshots" / pid
                if snap.exists():
                    shutil.rmtree(snap, ignore_errors=True)
                conn.execute("delete from launch_codes where profile_id = ?", (pid,))
                conn.execute("delete from browser_profile_extension_settings where profile_id = ?", (pid,))
                conn.execute("delete from browser_profile_extension_runtime where profile_id = ?", (pid,))
                conn.execute("delete from browser_profiles where profile_id = ?", (pid,))
                conn.commit()
                freed += size
                deleted.append({"name": r["profile_name"], "size": _human(size)})
            except Exception as exc:                        # noqa: BLE001
                conn.rollback()
                failed.append({"name": r["profile_name"], "error": str(exc)})
        if deleted:
            try:
                conn.execute("vacuum")
            except sqlite3.Error:
                pass
        return {"ok": True, "deleted": deleted, "failed": failed,
                "freed": freed, "freedText": _human(freed)}
    finally:
        conn.close()


# ------------------------------------------------------------------ HTTP 服务
def json_response(handler, code, payload):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


class Handler(BaseHTTPRequestHandler):
    server_version = "AntConsole/1.0"

    def log_message(self, fmt, *args):                            # 安静点
        pass

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = (ROOT / "index.html").read_text(encoding="utf-8")
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/health":
            try:
                ant_request("GET", "/api/health", timeout=5)
                payload = ant_request("GET", "/api/profiles", timeout=10)
                items = payload.get("items") or []
                return json_response(self, 200, {"ok": True, "antPort": ANT_PORT,
                                                 "count": len(items),
                                                 "configSource": CFG.get("source") or ""})
            except AntError as exc:
                return json_response(self, 200, {"ok": False, "antPort": ANT_PORT, "error": str(exc)})
        if path == "/api/list":
            try:
                items = ant_list_profiles()
                slim = [{
                    "id": p.get("profileId"),
                    "name": p.get("profileName"),
                    "running": bool(p.get("running")),
                    "debugPort": p.get("debugPort") or 0,
                    "proxyConfig": p.get("proxyConfig") or "",
                    "launchCode": p.get("launchCode") or "",
                    "tags": p.get("tags") or [],
                    "createdAt": p.get("createdAt") or "",
                } for p in items]
                return json_response(self, 200, {"ok": True, "items": slim})
            except AntError as exc:
                return json_response(self, 200, {"ok": False, "error": str(exc)})
        if path == "/api/trash":
            try:
                return json_response(self, 200, trash_snapshot())
            except Exception as exc:                        # noqa: BLE001
                return json_response(self, 200, {"ok": False, "error": str(exc)})
        if path == "/api/config":
            return json_response(self, 200, {"ok": True, "antPort": ANT_PORT,
                                             "localPort": LOCAL_PORT,
                                             "appDir": str(ANT_APP_DIR),
                                             "defaultFingerprintArgs": CFG.get("default_fingerprint_args"),
                                             "defaultLaunchArgs": CFG.get("default_launch_args"),
                                             "coreId": CFG.get("core_id")})
        return json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._body()

        if path == "/api/parse":
            accounts = parse_accounts(body.get("accounts"), body.get("count"))
            pool, errors = parse_proxy_pool(body.get("proxies"))
            names = [build_profile_name(body.get("nameTemplate") or "{idx}-{u}", r, i + 1)
                     for i, r in enumerate(accounts)]
            existing = set()
            try:
                existing = {str(p.get("profileName") or "") for p in ant_list_profiles()}
            except AntError:
                pass
            dups = sorted({n for n in names if n in existing})
            return json_response(self, 200, {
                "ok": True, "accounts": accounts, "proxyCount": len(pool),
                "proxyErrors": errors, "names": names,
                "duplicates": dups,
                "withTotp": sum(1 for a in accounts if a["totp"]),
            })

        if path == "/api/allocate":
            accounts = parse_accounts(body.get("accounts"), body.get("count"))
            pool, errors = parse_proxy_pool(body.get("proxies"))
            direct = bool(body.get("direct"))
            if direct:
                idx, aerr, warn = ([0] * len(accounts), [], []) if accounts else ([], ["没有账号"], [])
            else:
                idx, aerr, warn = allocate(accounts, pool,
                                           body.get("perIp") or 1,
                                           body.get("mode") or "seq",
                                           body.get("onShort") or "reuse")
            preview = []
            if not aerr:
                for i, acct in enumerate(accounts):
                    p = pool[idx[i]] if not direct else None
                    preview.append({
                        "seq": i + 1,
                        "name": build_profile_name(body.get("nameTemplate") or "{idx}-{u}", acct, i + 1),
                        "account": acct["account"],
                        "totp": acct["totp"],
                        "proxy": "直连" if direct else proxy_to_config(p),
                    })
            return json_response(self, 200, {
                "ok": not aerr, "errors": errors + aerr, "warnings": warn,
                "preview": preview,
                "capacity": len(accounts) if direct else len(pool) * max(1, int(body.get("perIp") or 1)),
                "need": len(accounts),
            })

        if path == "/api/proxies/check":
            pool, errors = parse_proxy_pool(body.get("proxies"))
            lines = [p["raw"] for p in pool][:CHECK_LIMIT]
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=CHECK_CONCURRENCY) as pool_ex:
                for res in pool_ex.map(check_one, lines):
                    results.append(res)
            want = (body.get("expectCode") or "").strip().upper()
            if want:
                good = sum(1 for r in results if r.get("ok") and r.get("code") == want)
                summary = "%d / %d %s" % (good, len(results), want)
            else:
                good = sum(1 for r in results if r.get("ok"))
                summary = "%d / %d 可用" % (good, len(results))
            return json_response(self, 200, {"ok": True, "results": results,
                                             "errors": errors, "summary": summary,
                                             "notChecked": max(0, len(pool) - len(lines))})

        if path == "/api/create":
            accounts = parse_accounts(body.get("accounts"), body.get("count"))
            pool, perr = parse_proxy_pool(body.get("proxies"))
            direct = bool(body.get("direct"))
            per_ip = body.get("perIp") or 1
            mode = body.get("mode") or "seq"
            on_short = body.get("onShort") or "reuse"
            idx, aerr, warn = (([0] * len(accounts), [], []) if direct
                               else allocate(accounts, pool, per_ip, mode, on_short))
            if not accounts:
                aerr = ["没有账号"]
            if aerr or perr:
                return json_response(self, 400, {"ok": False, "errors": perr + aerr})
            seed_base = str(body.get("seedBase") or "").strip()
            extra_fp = list(body.get("extraFingerprintArgs") or [])
            tz = str(body.get("timezone") or "").strip()
            if tz:
                # 内核 ≥142 的 --fingerprint-chromium 补丁里 --timezone 直接生效，两条读取路径都对
                # （Intl.DateTimeFormat().resolvedOptions().timeZone 与 Date.getTimezoneOffset）。
                # 135 及更早实测无效，那种情况只能退回 CDP Emulation.setTimezoneOverride。
                extra_fp.append("--timezone=" + tz)
            launch_args = body.get("launchArgs") or []
            auto_open = bool(body.get("autoOpen"))
            created, failed = [], []
            for i, acct in enumerate(accounts):
                name = build_profile_name(body.get("nameTemplate") or "{idx}-{u}", acct, i + 1)
                if direct:
                    proxy_cfg, proxy_mode = "", "fixed"
                else:
                    proxy_cfg, proxy_mode = proxy_to_config(pool[idx[i]]), "local"
                seed = ""
                if seed_base:
                    digits = re.sub(r"\D", "", seed_base) or "7"
                    seed = (digits + str(100000 + i * 7919))[:12]
                profile = {
                    "profileName": name,
                    "coreId": body.get("coreId") or CFG.get("core_id") or "default",
                    "fingerprintArgs": build_fingerprint_args(seed, extra_fp),
                    "launchArgs": launch_args,
                    "tags": [t for t in (body.get("tags") or []) if t],
                    "proxyConfig": proxy_cfg,
                }
                # 只传新旧版本都认的字段：安装版（1.8/1.9.0）的 ProfileInput 没有
                # proxyMode / keywords / groupId，多了会被 DisallowUnknownFields 打成 400。
                # 不传 proxyMode 时后端按「proxyConfig 非空 → local」自行推断。
                try:
                    res = ant_create_profile(profile, auto_launch=False)
                    pid = res.get("profileId") or (res.get("profile") or {}).get("profileId")
                    created.append({"seq": i + 1, "name": name, "id": pid,
                                    "account": acct["account"],
                                    "proxy": proxy_cfg or "直连"})
                    if auto_open and pid:
                        try:
                            ant_launch_profile(pid)
                        except AntError as exc:
                            failed.append({"name": name, "error": "建好但打开失败：%s" % exc})
                except AntError as exc:
                    failed.append({"name": name, "error": str(exc)})
            return json_response(self, 200, {"ok": True, "created": created,
                                             "failed": failed, "warnings": warn})

        if path == "/api/trash/purge":
            if not body.get("confirm"):
                return json_response(self, 400, {"ok": False, "error": "需要 confirm=true"})
            try:
                return json_response(self, 200, trash_purge(body.get("keepDays") or 0))
            except BaseException as exc:                    # noqa: BLE001
                # 一次删几百个文件时，某些环境的安全策略会中断删除（异常不一定继承 Exception），
                # 这里兜住，至少让前端拿到一句话而不是断连接。
                msg = str(exc) or exc.__class__.__name__
                return json_response(self, 200, {
                    "ok": False,
                    "error": "清理被中断：%s。如果提示要删的文件太多，改用命令行 "
                             "`python purge-trash.py --app \"<Ant目录>\" --force`。" % msg[:200],
                })

        if path == "/api/action":
            action = (body.get("action") or "").strip()
            ids = [str(x) for x in (body.get("ids") or []) if x]
            if not ids:
                return json_response(self, 400, {"ok": False, "error": "没选实例"})
            done, failed = [], []
            for pid in ids:
                try:
                    if action == "open":
                        ant_launch_profile(pid)
                    elif action == "stop":
                        ant_stop_profile(pid)
                    elif action == "delete":
                        ant_delete_profile(pid)
                    else:
                        return json_response(self, 400, {"ok": False, "error": "未知操作：%s" % action})
                    done.append(pid)
                except AntError as exc:
                    failed.append({"id": pid, "error": str(exc)})
            return json_response(self, 200, {"ok": True, "done": done, "failed": failed})

        if path == "/api/ant-port":
            global ANT_PORT
            try:
                port = int(body.get("port") or 0)
            except (TypeError, ValueError):
                port = 0
            if not (0 < port < 65536):
                return json_response(self, 400, {"ok": False, "error": "端口不合法"})
            ANT_PORT = port
            try:
                ant_request("GET", "/api/health", timeout=5)
                return json_response(self, 200, {"ok": True, "antPort": ANT_PORT})
            except AntError as exc:
                return json_response(self, 200, {"ok": False, "antPort": ANT_PORT, "error": str(exc)})

        return json_response(self, 404, {"ok": False, "error": "not found"})


def main():
    port = LOCAL_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Ant 控制台: http://127.0.0.1:%d" % port)
    print("Ant API : %s（来自 %s）" % (ant_base(), CFG.get("source") or "默认值"))
    if not CFG.get("source"):
        print("提示：没找到 Ant-Browser/config.yaml，端口用默认 19900；界面里可以改。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
