#!/usr/bin/env python3
"""清理 Ant Browser 回收站里的实例（彻底删除，释放磁盘）。

Ant 的「删除实例」是软删除：记录还在 `browser_profiles` 表里，只是 `deleted_at` 有值；
界面里的「回收站」只能一个一个点「彻底删除」，而且超过 72 小时才自动清。这个脚本批量做。

它复刻的是 Ant 自己的 `PermanentlyDelete` 逻辑：
  launch_codes → 插件配置 / 插件运行态 → 用户数据目录 → 快照目录 → 指纹检测缓存 → profile 记录

用法：
  python3 purge-trash.py --app "C:/Cdoe/AntBrowser"            # 只列出，不动
  python3 purge-trash.py --app "C:/Cdoe/AntBrowser" --force    # 真删
  python3 purge-trash.py --app "C:/Cdoe/AntBrowser" --force --keep-days 3   # 只删 3 天前的

跑之前必须先关掉 Ant（它会持有 sqlite 和实例目录）。
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def dir_size(path: Path) -> int:
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


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f%s" % (n, unit)
        n /= 1024
    return "%.1fTB" % n


def main():
    ap = argparse.ArgumentParser(description="批量彻底删除 Ant Browser 回收站实例")
    ap.add_argument("--app", default="C:/Cdoe/AntBrowser", help="Ant Browser 安装目录（含 data/app.db）")
    ap.add_argument("--force", action="store_true", help="真的删除；不加只列出")
    ap.add_argument("--keep-days", type=float, default=0, help="只删删除时间超过 N 天的（默认全部）")
    args = ap.parse_args()

    app = Path(args.app)
    db = app / "data" / "app.db"
    data_root = (app / "data").resolve()
    if not db.exists():
        print("找不到数据库：%s" % db)
        print("用 --app 指定 Ant Browser 的安装目录（那个目录下应该有 data/app.db）。")
        return 1

    import socket
    if args.force:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", 19876)) == 0:
                print("Ant Browser 还在运行（19876 有监听）。")
                print("请先关掉它（用窗口的关闭按钮，别强杀），再跑这个脚本。")
                return 2

    conn = sqlite3.connect(str(db), timeout=10)
    conn.row_factory = sqlite3.Row
    cutoff = ""
    if args.keep_days > 0:
        cutoff = (datetime.now() - timedelta(days=args.keep_days)).strftime("%Y-%m-%dT%H:%M:%S")

    rows = conn.execute(
        "select profile_id, profile_name, user_data_dir, deleted_at from browser_profiles "
        "where deleted_at is not null and deleted_at <> '' order by deleted_at"
    ).fetchall()
    if cutoff:
        rows = [r for r in rows if r["deleted_at"][:19] >= cutoff]

    if not rows:
        print("回收站是空的（或没有超过 %g 天的）。" % args.keep_days)
        return 0

    total = 0
    plan = []

    # 存活实例占用的目录一律不许碰（有老版本用固定的 data/default，容易撞）
    alive = set()
    for r in conn.execute(
        "select profile_id, user_data_dir from browser_profiles "
        "where deleted_at is null or deleted_at = ''"
    ):
        udd = (r["user_data_dir"] or "").strip() or r["profile_id"]
        p = Path(udd)
        p = p if p.is_absolute() else (data_root / udd)
        try:
            alive.add(p.resolve())
        except OSError:
            alive.add(p)

    for r in rows:
        pid = r["profile_id"]
        udd = (r["user_data_dir"] or "").strip() or pid
        # user_data_dir 可能是绝对路径，也可能是相对 data 的名字；两种都兜住，且不许跳出 data
        p = Path(udd)
        target = p if p.is_absolute() else (data_root / udd)
        try:
            target = target.resolve()
        except OSError:
            target = data_root / udd
        inside = data_root in target.parents or target == data_root
        busy = target in alive
        snap = data_root / "snapshots" / pid
        size = dir_size(target) + dir_size(snap)
        total += size
        plan.append((pid, r["profile_name"] or "(未命名)", r["deleted_at"][:19],
                     target, inside and not busy, size, busy))

    print("回收站 %d 个实例，共占用 %s\n" % (len(plan), human(total)))
    print("%-14s %-20s %-10s %s" % ("删除时间", "实例名", "占用", "数据目录"))
    for pid, name, when, target, ok, size, busy in plan:
        note = ""
        if busy:
            note = "  ⚠ 仍被存活实例占用，跳过"
        elif not ok:
            note = "  ⚠ 路径在 data 外，跳过"
        print("%-14s %-20s %-10s %s%s" % (when[5:], name[:20], human(size), target.name, note))

    if not args.force:
        print("\n这是预览。确认无误后加 --force 再跑一次。")
        return 0

    freed = 0
    for pid, name, when, target, ok, size, busy in plan:
        try:
            if ok and target.exists() and target != data_root:
                shutil.rmtree(target, ignore_errors=False)
                freed += size
            snap = data_root / "snapshots" / pid
            if snap.exists():
                shutil.rmtree(snap, ignore_errors=True)
            conn.execute("delete from launch_codes where profile_id = ?", (pid,))
            conn.execute("delete from browser_profile_extension_settings where profile_id = ?", (pid,))
            conn.execute("delete from browser_profile_extension_runtime where profile_id = ?", (pid,))
            conn.execute("delete from browser_profiles where profile_id = ?", (pid,))
            print("已删除 %s（%s）" % (name, human(size)))
        except Exception as exc:                       # noqa: BLE001
            print("失败 %s：%s" % (name, exc))
            conn.rollback()
            continue
        conn.commit()

    conn.execute("vacuum")
    conn.close()
    print("\n完成：释放约 %s，数据库已 VACUUM。" % human(freed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
