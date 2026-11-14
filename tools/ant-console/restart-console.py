"""Restart the Windows console: kill whatever owns port 8800, then let start-all.bat relaunch it.

Default behaviour is force-restart: any listener on the port is stopped (with its window),
so double-clicking start-all.bat always ends up with a fresh console instead of the
"port already in use" dead end.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8800
URL = "http://127.0.0.1:%d/" % PORT
STARTUP_TIMEOUT = 10
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def listeners():
    output = subprocess.check_output(["netstat.exe", "-ano", "-p", "TCP"])
    return {
        int(fields[-1])
        for fields in (line.split() for line in output.splitlines())
        if len(fields) == 5 and fields[1] in (b"127.0.0.1:8800", b"0.0.0.0:8800", b"[::]:8800")
        and fields[2] in (b"0.0.0.0:0", b"[::]:0") and fields[-1].isdigit()
    }


def serves_project():
    """只确认端口上有 HTTP 页面在响应，不比对内容：页面随时可能改版，比内容会误判。"""
    try:
        with HTTP.open(URL, timeout=1) as response:
            body = response.read(2048).lower()
            return b"<html" in body or b"<!doctype" in body
    except OSError:
        return False


def process_info(pid):
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "$p = Get-CimInstance Win32_Process -Filter 'ProcessId=%d' -ErrorAction Stop; "
        "if ($p) { $p | Select-Object Name,CommandLine,ParentProcessId,CreationDate | ConvertTo-Json -Compress }"
        % pid
    )
    result = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], encoding="utf-8"
    ).strip()
    return json.loads(result) if result else None


def stop_process(pid, reason):
    info = process_info(pid) or {}
    print("  - 结束 %s (pid=%d)：%s" % (info.get("Name") or "?", pid, reason))
    # /T：连同它的子进程一起收掉，避免残留的登录脚本继续占着实例
    subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_until(check):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.25)
    return False


def restart():
    owners = listeners()
    if not owners:
        print("  - 端口 %d 空闲，直接启动" % PORT)
        return
    for pid in owners:
        proc = process_info(pid)
        if not proc:
            continue
        name = (proc.get("Name") or "").lower()
        command = proc.get("CommandLine") or ""
        is_console = name.startswith("python") and "server.py" in command
        if is_console:
            stop_process(pid, "旧的 Ant Console")
            parent_pid = proc.get("ParentProcessId") or 0
            parent = process_info(parent_pid) if parent_pid else None
            if parent and (parent.get("Name") or "").lower() == "cmd.exe" \
                    and "server.py" in (parent.get("CommandLine") or ""):
                stop_process(parent_pid, "旧的控制台窗口")
        else:
            # 端口被别的程序占了：默认也收掉，否则 start-all 永远起不来
            stop_process(pid, "占用了 %d 端口" % PORT)
    if not wait_until(lambda: not listeners()):
        raise RuntimeError(
            "端口 %d 仍未释放，手动排查：netstat -ano | findstr :%d" % (PORT, PORT)
        )


def main():
    try:
        if "--wait" in sys.argv[1:]:
            if not wait_until(serves_project):
                raise RuntimeError("Ant Console 没有在 %d 端口起来，看控制台窗口的报错" % PORT)
        else:
            restart()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("[x] %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
