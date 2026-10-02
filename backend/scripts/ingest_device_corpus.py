#!/usr/bin/env python3
"""器械说明书语料批量入库（走 M7 POST /docs，多模态自动生效）。

上传目录下所有 PDF 到指定知识库，轮询直到全部 ready/error。
入库完成后需 `./dev.sh restart api` 刷新 AppDeps 缓存，检索才能看到新文档。

用法：
    cd backend && python scripts/ingest_device_corpus.py \
        --dir ~/Downloads/device_pdfs --collection col_b7b876b1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8787"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path.home() / "Downloads/device_pdfs"))
    ap.add_argument("--collection", default="col_b7b876b1")
    ap.add_argument("--poll", type=int, default=20, help="轮询间隔（秒）")
    ap.add_argument("--timeout", type=int, default=7200, help="总超时（秒）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 份（0=全部，试点用）")
    args = ap.parse_args()

    # trust_env=False：避开 macOS 系统代理 —— 否则 localhost 被送进代理 → 502
    s = requests.Session()
    s.trust_env = False

    pdfs = sorted(Path(args.dir).expanduser().glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]

    # 幂等：跳过库内已 ready / 正在 processing 的同名文档，避免重复入库
    # （processing 也跳：后端入库是后台任务，轮询脚本中断不代表入库停止）
    existing = {
        d["filename"]
        for d in s.get(
            f"{API}/docs", params={"collection_id": args.collection}, timeout=300
        ).json()
        if d.get("status") in ("ready", "processing")
    }
    if existing:
        skipped = [p.name for p in pdfs if p.name in existing]
        pdfs = [p for p in pdfs if p.name not in existing]
        for name in skipped:
            print(f"[跳过] {name}（库内已存在）", flush=True)

    if not pdfs:
        sys.exit(f"无待入库 PDF（库内已全部 ready）：{args.dir}")

    print(f"[开始] 目标库 {args.collection}｜待入库 {len(pdfs)} 份", flush=True)
    tasks: dict[str, str] = {}
    for p in pdfs:
        with open(p, "rb") as fh:
            r = s.post(
                f"{API}/docs",
                files={"file": (p.name, fh, "application/pdf")},
                data={"collection_id": args.collection},
                timeout=300,
            )
        r.raise_for_status()
        d = r.json()
        tasks[d["task_id"]] = p.name
        print(f"[提交] {p.name} → task {d['task_id'][:8]}", flush=True)

    t0 = time.time()
    while True:
        if time.time() - t0 > args.timeout:
            print("[超时] 退出", flush=True)
            break
        time.sleep(args.poll)
        # 后端串行处理多份文档时 /docs 可能响应很慢，超时给足并容错重试
        try:
            docs = s.get(
                f"{API}/docs", params={"collection_id": args.collection}, timeout=300
            ).json()
        except Exception as e:
            print(f"[警告] 轮询失败，{args.poll}s 后重试：{type(e).__name__}", flush=True)
            continue
        by_task = {d["task_id"]: d for d in docs}
        counts: dict[str, int] = {}
        for tid in tasks:
            st = by_task.get(tid, {}).get("status", "?")
            counts[st] = counts.get(st, 0) + 1
        el = int(time.time() - t0)
        summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"[{el // 60:02d}:{el % 60:02d}] {summary}", flush=True)
        if counts.get("processing", 0) == 0:
            break

    print("[汇总]", flush=True)
    docs = s.get(
        f"{API}/docs", params={"collection_id": args.collection}, timeout=300
    ).json()
    by_task = {d["task_id"]: d for d in docs}
    ok = 0
    for tid, name in tasks.items():
        d = by_task.get(tid, {})
        st = d.get("status", "?")
        ok += st == "ready"
        extra = f" doc_id={d['doc_id']}" if d.get("doc_id") else ""
        err = f" error={d['error']}" if d.get("error") else ""
        print(f"  [{st}] {name}{extra}{err}", flush=True)
    print(f"[完成] ready {ok}/{len(tasks)}｜耗时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)
    print("[提醒] 入库后执行 ./dev.sh restart api 刷新缓存", flush=True)


if __name__ == "__main__":
    main()
