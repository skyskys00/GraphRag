"""轻量级 answer 验证：3 道代表题，top5 vs top8 人工对比。
用法：python /tmp/answer_verify.py <output_json>
注意：运行前需把 RERANK_TOP 设为对应值。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1] if "__file__" in dir() else Path.cwd()
import os
os.chdir(str(PROJ))

sys.path.insert(0, str(PROJ))

QIDS = ["CS-TN-001", "CS-FC-001", "CS-FC-007"]


async def main():
    from app.m3_index.runner import _load_dotenv, build_rag
    from app.m3_index.providers import build_llm_func
    from app.m5_retrieve.sparse_index import SPARSE_FILE, load as load_sparse
    from app.m6_generate.orchestrator import answer

    _load_dotenv(PROJ / ".env")
    workspace = "eval_cservice_ws"
    working_dir = PROJ / "data" / workspace

    print(f"初始化 RAG...", flush=True)
    rag = await build_rag(working_dir, workspace=workspace)
    sparse = load_sparse(working_dir / SPARSE_FILE)
    print(f"  chunks: {len(sparse.get('chunks', {}))}", flush=True)

    testset = json.load(open(PROJ / "tests/testsets/testset_cservice_35.json"))
    questions = {q["id"]: q for q in testset["questions"]}

    results = []
    for qid in QIDS:
        q = questions[qid]
        print(f"\n=== {qid} ({q['category']}) ===", flush=True)
        print(f"Q: {q['question']}", flush=True)
        t0 = time.time()
        try:
            ans = await answer(rag, q["question"], sparse)
            dt = round(time.time() - t0, 2)
            text = ans["text"]
            retr = ans["retrieval"]["results"]
            cits = ans.get("citations", [])
            meta = ans.get("meta", {})
            print(f"  用时: {dt}s  used_chunks: {meta.get('used_chunks', '?')}  citations: {len(cits)}  mode: {meta.get('mode','?')}", flush=True)
            print(f"\n【回答】\n{text}\n", flush=True)
            print(f"【检索上下文 ({len(retr)} 块)】", flush=True)
            for c in retr:
                print(f"  r{c['rank']} [{c.get('full_doc_id','?')[:50]}] score={c.get('score',0):.4f}: {c['content'][:120]}...", flush=True)
            print(f"【引用】", flush=True)
            for cit in cits:
                print(f"  - {cit.get('full_doc_id','?')[:50]} chunk={cit.get('chunk_id','?')[:20]} quote={cit.get('quote','')[:80]}", flush=True)
            results.append({
                "id": qid,
                "category": q["category"],
                "question": q["question"],
                "answer": text,
                "retrieval": [
                    {"rank": c["rank"], "chunk_id": c["chunk_id"], "full_doc_id": c["full_doc_id"], "score": c.get("score", 0), "content": c["content"]}
                    for c in retr
                ],
                "citations": cits,
                "meta": meta,
                "time_s": dt,
                "ground_truth": q.get("ground_truth", ""),
                "key_facts": q.get("key_facts", []),
            })
        except Exception as e:
            print(f"  ❌ 失败: {e}", flush=True)
            import traceback
            traceback.print_exc()
            results.append({"id": qid, "error": str(e)})

    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/answer_verify_result.json"
    with open(out, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
