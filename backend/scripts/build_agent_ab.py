"""M10 A/B 对比：agent 评测报告 vs 直连 e2e 基线报告。

判定「不劣化」：Δ = agent - baseline，Δ ≥ -0.03 则该指标不劣化（M10 §7 RAG 对比回归门禁）。
输出 markdown 对比表。
用法:
  python scripts/build_agent_ab.py \
      --agent tests/reports/agent/run_agent_device_50_20261023.json \
      --baseline tests/final_results/run_e2e_device_50_20261005.json \
      --out tests/reports/agent/agent_vs_direct_ab.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

NON_DEGRADE_DELTA = -0.03  # agent 相对基线可接受的下降阈值
METRICS = [
    ("correctness", "Correctness（正确性）"),
    ("faithfulness", "Faithfulness（忠实度）"),
    ("answer_relevance", "Answer Relevance（答案相关性）"),
    ("citation_accuracy", "Citation Accuracy（引用准确率）"),
    ("multi_hop_recall", "Multi-hop Recall（agent 上下文召回）"),
    ("context_recall", "Context Recall（检索召回）"),
    ("tool_accuracy", "Tool Accuracy（工具链合理性）"),
]

# agent 模式下 context_recall 无直连可比（agent 用 multi_hop_recall 替代覆盖等价语义）
_AGENT_SUBSTITUTE = {"context_recall"}


def _overall(p: Any) -> dict:
    return (p.get("summary") or {}).get("overall") or {}


def _is_agent(p: Any) -> bool:
    return (p.get("config") or {}).get("mode") == "agent"


def build(agent_path: Path, baseline_path: Path) -> str:
    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    a, b = _overall(agent), _overall(baseline)

    lines = []
    lines.append("# M10 A/B 对比：Agent 首轮直答 vs RAG 直连基线")
    lines.append("")
    lines.append(f"- **题集**：`{agent.get('testset_id', '')}`（col_b7b876b1，50 题）")
    lines.append(f"- **Agent 报告**：`{agent_path}`（{agent.get('timestamp', '')}）")
    lines.append(f"- **直连基线**：`{baseline_path}`（{baseline.get('timestamp', '')}，当前口径）")
    lines.append(f"- **判定**：Δ = agent − 基线，Δ ≥ {NON_DEGRADE_DELTA} 记「不劣化」（M10 §7 回归门禁）")
    lines.append("")
    lines.append("## 指标对比")
    lines.append("")
    lines.append("| 指标 | Agent | 直连基线 | Δ | 判定 |")
    lines.append("|---|---|---|---|---|")
    for key, label in METRICS:
        av, bv = a.get(key), b.get(key)
        if av is None and bv is None:
            continue
        # agent 模式下 context_recall 无直连可比：agent 侧语义由 multi_hop_recall 覆盖，
        # 此处 0 是「未计算」而非「零分」，单列不判劣化
        if _is_agent(agent) and key in _AGENT_SUBSTITUTE:
            lines.append(f"| {label} | —（由 Multi-hop Recall 替代） | {bv:.4f} | — | — |")
            continue
        a_str = f"{av:.4f}" if isinstance(av, (int, float)) else "—"
        b_str = f"{bv:.4f}" if isinstance(bv, (int, float)) else "—"
        delta = (av - bv) if isinstance(av, (int, float)) and isinstance(bv, (int, float)) else None
        if delta is None:
            lines.append(f"| {label} | {a_str} | {b_str} | — | — |")
            continue
        verdict = "不劣化" if delta >= NON_DEGRADE_DELTA else "**劣化**"
        lines.append(f"| {label} | {a_str} | {b_str} | **{delta:+.4f}** | {verdict} |")

    # 结论行
    core = ["correctness", "faithfulness", "citation_accuracy"]
    deltas = {
        k: (a.get(k) - b.get(k))
        for k in core
        if isinstance(a.get(k), (int, float)) and isinstance(b.get(k), (int, float))
    }
    lines.append("")
    if deltas:
        all_ok = all(d >= NON_DEGRADE_DELTA for d in deltas.values())
        summary = "核心三项（correctness/faithfulness/citation_accuracy）**均不劣化**，agent 引入未降级底座" \
            if all_ok else \
            f"存在劣化项：{ {k: round(d, 4) for k, d in deltas.items() if d < NON_DEGRADE_DELTA} }，需排查"
        lines.append(f"> **结论**：{summary}")
    else:
        lines.append("> **结论**：核心指标数据缺失，无法判定。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="M10 agent vs 直连基线 A/B 对比")
    parser.add_argument("--agent", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    md = build(args.agent, args.baseline)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n已保存: {args.out}")


if __name__ == "__main__":
    main()