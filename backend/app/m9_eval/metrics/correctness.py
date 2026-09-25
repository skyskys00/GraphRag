"""Correctness（正确性）：答案与标准答案的事实一致性。

让 LLM 裁判对比答案和标准答案，判断答案中的关键事实点是否正确。
不同于 faithfulness（只看是否有上下文依据），correctness 看答案本身是否正确。
"""
from __future__ import annotations

from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是对比答案与标准答案，判断答案在事实层面的正确性。
正确性 = 答案正确覆盖的标准答案事实点 ÷ 标准答案中的关键事实点总数。
判分规则：
- **语义对齐优先**：答案只要用自己的话说出了同一信息，即使措辞、顺序、结构、概括粒度不同，也视为覆盖该事实点。同一概念的不同中文译名、别名、缩写展开（如「首问解决率」vs「首次联系解决率」＝覆盖）都视为同一事实。示例「按自然时间计算」vs「24 小时连续计时」＝覆盖；标准答案把「多轮对话管理」单列，答案把它并入「核心能力升级（语义理解＋知识图谱＋多轮对话）」＝覆盖；标准答案逐条列事实，答案先用上位概括再用细节展开＝覆盖。
- **额外正确信息不扣分**：答案额外给出标准答案没有、但不与标准答案矛盾的内容，不计入评价，不稀释分数。只有与标准答案**矛盾**的内容才扣分。
- **部分覆盖按比例**：覆盖大部分事实点、缺少数点，按缺失比例扣分；不要因缺一个点就大幅压低分数，覆盖过半通常应 ≥ 0.6。
- **虚构/错答从严**：答案把文档中不存在的指标、数值或能力当作事实陈述，视严重程度显著扣分。
- 答案明确说「材料不足/无法回答」而标准答案有明确答案，视为不正确。
只根据标准答案判断，不要使用外部知识。"""

PROMPT_TEMPLATE = """请评估以下答案的正确性：对比答案与标准答案，判断答案正确覆盖了标准答案中的哪些关键事实点。

【问题】
{question}

【标准答案】
{ground_truth}

【答案】
{answer}

步骤：
1. 从标准答案中拆出关键事实点（一个数字、一个定义、一个动作、一个关系统计各算一点）。
2. 逐一将答案与每个事实点做**语义对齐**：答案用自己的话表达了同一信息就视为覆盖，不要求逐字相同。
3. 答案中与标准答案无对应关系、也不矛盾的额外内容，忽略不计入。
4. 统计覆盖数并打分。

输出 JSON 格式：
{{
  "score": 0.0 到 1.0 的正确性分数（覆盖的事实点数 / 标准答案事实点总数）,
  "total_facts": 标准答案中的关键事实点总数,
  "correct_facts": 答案覆盖的事实点数,
  "incorrect": [
    {{"fact": "未覆盖或与标准答案矛盾的事实点", "reason": "答案遗漏还是答错"}}
  ],
  "reason": "一句话总结判断结果"
}}
"""


async def compute_correctness(
    query_func: Callable,
    question: str,
    ground_truth: str,
    answer: str,
) -> dict[str, Any]:
    """计算 correctness。

    返回: {score, total_facts, correct_facts, incorrect, reason}
    """
    if not answer or not answer.strip():
        return {
            "score": 0.0,
            "total_facts": 0,
            "correct_facts": 0,
            "incorrect": [],
            "reason": "空答案，不正确",
        }

    if not ground_truth or not ground_truth.strip():
        return {
            "score": 1.0,
            "total_facts": 0,
            "correct_facts": 0,
            "incorrect": [],
            "reason": "无标准答案，默认正确",
        }

    prompt = PROMPT_TEMPLATE.format(
        question=question,
        ground_truth=ground_truth,
        answer=answer,
    )

    result = await judge(
        query_func,
        "correctness",
        prompt,
        system_prompt=SYSTEM_PROMPT,
        cache_key_parts=[question, ground_truth, answer],
    )

    return {
        "score": result["score"],
        "total_facts": result.get("total_facts", 0),
        "correct_facts": result.get("correct_facts", 0),
        "incorrect": result.get("incorrect", []),
        "reason": result["reason"],
    }
