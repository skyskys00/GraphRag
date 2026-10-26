# M10 A/B 对比：Agent 首轮直答 vs RAG 直连基线

- **题集**：`testset_device_v2`（col_b7b876b1，50 题）
- **Agent 报告**：`tests/reports/agent/run_agent_device_50_v650_regress.json`（2026-10-26 21:10:27）
- **直连基线**：`tests/final_results/run_e2e_device_50_20261005_strict.json`（2026-10-24 00:56:55，当前口径）
- **判定**：Δ = agent − 基线，Δ ≥ -0.03 记「不劣化」（M10 §7 回归门禁）

## 指标对比

| 指标 | Agent | 直连基线 | Δ | 判定 |
|---|---|---|---|---|
| Correctness（正确性） | 0.9190 | 0.8733 | **+0.0457** | 不劣化 |
| Faithfulness（忠实度） | 0.9266 | 0.9655 | **-0.0389** | **劣化** |
| Answer Relevance（答案相关性） | 0.9790 | 0.9314 | **+0.0476** | 不劣化 |
| Citation Accuracy（引用准确率） | 0.8335 | 0.8960 | **-0.0625** | **劣化** |
| Multi-hop Recall（agent 上下文召回） | 0.9433 | 0.0000 | **+0.9433** | 不劣化 |
| Context Recall（检索召回） | —（由 Multi-hop Recall 替代） | 0.8833 | — | — |
| Tool Accuracy（工具链合理性） | 0.9470 | 0.0000 | **+0.9470** | 不劣化 |

> **结论**：存在劣化项：{'faithfulness': -0.0389, 'citation_accuracy': -0.0625}，需排查
