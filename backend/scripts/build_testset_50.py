"""生成 testset_cservice_50.json：35 题原地保留 + 追加 15 题。

用法：python scripts/build_testset_50.py
输出：backend/tests/testsets/testset_cservice_50.json
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "tests/testsets/testset_cservice_35.json"
DST = BASE / "tests/testsets/testset_cservice_50.json"

NEW_QUESTIONS = [
    {
        "id": "CS-FS-011",
        "category": "fact_single",
        "question": "各等级投诉的「方案提出」时效分别是多少？",
        "ground_truth": "各等级投诉的方案提出时效为：一级（紧急）2 小时、二级（重要）8 小时、三级（一般）24 小时。",
        "key_facts": [
            "一级方案提出=2小时",
            "二级方案提出=8小时",
            "三级方案提出=24小时",
        ],
        "must_have_docs": ["D1_客户服务投诉处理SOP.md"],
        "must_not_have_docs": [],
        "difficulty": "easy",
        "source_docs": ["D1"],
        "tags": ["投诉时效", "方案提出", "分级"],
    },
    {
        "id": "CS-FS-012",
        "category": "fact_single",
        "question": "智能问答引擎的意图识别采用什么技术方案？支持多少种意图分类？",
        "ground_truth": "意图识别基于 BERT-base-chinese 模型微调，支持 50+ 种意图分类，准确率目标 ≥ 92%。",
        "key_facts": [
            "基于BERT-base-chinese微调",
            "支持50+意图分类",
            "准确率目标≥92%",
        ],
        "must_have_docs": ["D2_智能客服系统产品需求文档.docx"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D2"],
        "tags": ["意图识别", "BERT", "技术选型"],
    },
    {
        "id": "CS-FS-013",
        "category": "fact_single",
        "question": "客服质检月度评分中，评为「服务之星」的条件是什么？连续多久低于多少分会启动绩效改进计划（PIP）？",
        "ground_truth": "月度评分前 10% 评为「服务之星」并给予奖金；月度评分低于 60 分需参加话术复训；连续两个月低于 60 分启动绩效改进计划（PIP）。",
        "key_facts": [
            "前10%评为服务之星",
            "低于60分需参加复训",
            "连续两个月低于60分启动PIP",
        ],
        "must_have_docs": ["D4_客服话术规范与FAQ.md"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D4"],
        "tags": ["质检", "服务之星", "PIP", "考核"],
    },
    {
        "id": "CS-FS-014",
        "category": "fact_single",
        "question": "智能客服系统项目的里程碑中，试点上线和全量上线分别计划在什么时间？",
        "ground_truth": "试点上线计划在 2026 年 12 月 25 日（由客服中心王小燕负责并输出试点运行报告），全量上线计划在 2027 年 1 月 15 日。",
        "key_facts": [
            "试点上线=2026年12月25日",
            "全量上线=2027年1月15日",
            "试点负责人=王小燕",
        ],
        "must_have_docs": ["D2_智能客服系统产品需求文档.docx"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D2"],
        "tags": ["里程碑", "上线计划", "项目计划"],
    },
    {
        "id": "CS-FC-008",
        "category": "fact_cross_doc",
        "question": "话术 FAQ 中「投诉多久能有结果」的标准回答（一般问题 5 个工作日处理完毕），与投诉处理 SOP 中的时效要求是否一致？",
        "ground_truth": "一致。D4 FAQ Q10 称一般问题（三级投诉）5 个工作日内处理完毕；D1 SOP §4.1 中三级投诉的问题解决时效同为 5 个工作日，两者口径吻合。",
        "key_facts": [
            "FAQ三级投诉5个工作日处理完毕",
            "SOP三级投诉问题解决=5个工作日",
            "两者口径一致",
        ],
        "must_have_docs": ["D4_客服话术规范与FAQ.md", "D1_客户服务投诉处理SOP.md"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D4", "D1"],
        "tags": ["FAQ", "时效", "跨文档一致性"],
    },
    {
        "id": "CS-FC-009",
        "category": "fact_cross_doc",
        "question": "D3 报表指出西南大区响应偏慢与其未接入智能客服有关，结合 D2 的规划，西南大区预计何时完成接入？智能问答引擎在 D2 中的优先级是什么？",
        "ground_truth": "西南大区预计 2026 年 10 月底完成智能问答引擎接入，接入后其拦截率与大区效率预计明显提升；智能问答引擎是 D2 PRD 中的 P0（最高）优先级模块，配套 ART 目标 ≤ 30 秒。",
        "key_facts": [
            "西南大区10月底完成智能客服接入",
            "智能问答引擎为P0最高优先级",
            "接入后拦截率与效率预计提升",
        ],
        "must_have_docs": ["D3_2026Q3客服运营数据报表.pdf", "D2_智能客服系统产品需求文档.docx"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D3", "D2"],
        "tags": ["西南大区", "智能问答引擎", "接入计划"],
    },
    {
        "id": "CS-PN-007",
        "category": "proper_noun",
        "question": "什么是 Deflection Rate（智能客服拦截率）？公司 Q3 实际值是多少，是否达成目标？",
        "ground_truth": "Deflection Rate（智能客服拦截率）指由智能问答引擎独立解决、无需转人工的对话占比，目标值 ≥ 60%。Q3 实际拦截率为 61.7%，首次突破目标线，达成目标。",
        "key_facts": [
            "拦截率=智能问答独立解决无需转人工的对话占比",
            "目标值≥60%",
            "Q3实际61.7%达成目标",
        ],
        "must_have_docs": ["D2_智能客服系统产品需求文档.docx", "D3_2026Q3客服运营数据报表.pdf"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D2", "D3"],
        "tags": ["DeflectionRate", "拦截率", "指标"],
    },
    {
        "id": "CS-PN-008",
        "category": "proper_noun",
        "question": "什么是「槽位填充」（Slot Filling）？它在智能客服系统的哪个模块中使用？",
        "ground_truth": "槽位填充是多轮对话管理的基础技术，配合状态机使用，用于在对话中提取并填充用户的关键信息槽位，支持上下文关联的多轮问答，最大对话轮次 8 轮。",
        "key_facts": [
            "槽位填充用于多轮对话管理",
            "配合状态机使用",
            "最大对话轮次8轮",
        ],
        "must_have_docs": ["D2_智能客服系统产品需求文档.docx"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D2"],
        "tags": ["槽位填充", "多轮对话", "技术名词"],
    },
    {
        "id": "CS-CP-005",
        "category": "comparison",
        "question": "行业报告中，金融、电商、通信三大行业的客服智能化渗透率分别是多少？我司所处的消费电子行业渗透率处于什么水平？",
        "ground_truth": "渗透率最高的是金融（68%）、电商（62%）、通信（57%）三大行业；我司所处的消费电子行业渗透率约 53%，处于行业中上游水平，仍有较大提升空间。",
        "key_facts": [
            "金融68%渗透率最高",
            "电商62%、通信57%",
            "消费电子约53%处于行业中上游",
        ],
        "must_have_docs": ["D5_客服智能化行业趋势报告.pdf"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D5"],
        "tags": ["渗透率", "行业对比", "消费电子"],
    },
    {
        "id": "CS-CP-006",
        "category": "comparison",
        "question": "对比 2025 年和 2026E 中国客服智能化市场规模增速，哪个更快？主要驱动因素是什么？",
        "ground_truth": "2026E 增速更快（+36.2%，市场规模 425 亿元），高于 2025 年的 +23.8%（312 亿元）。主要驱动因素：劳动力成本上升推动降本增效投入、LLM 技术突破提升语义理解与多轮对话能力、客户对 7×24 服务与秒级响应的体验要求升级。",
        "key_facts": [
            "2026E增速36.2%快于2025年23.8%",
            "驱动=劳动力成本上升+LLM突破+体验升级",
            "2026E市场规模425亿元",
        ],
        "must_have_docs": ["D5_客服智能化行业趋势报告.pdf"],
        "must_not_have_docs": [],
        "difficulty": "hard",
        "source_docs": ["D5"],
        "tags": ["市场规模", "增速对比", "增长驱动"],
    },
    {
        "id": "CS-TN-005",
        "category": "table_numeric",
        "question": "Q3 分月工单量中，8 月工单量是多少件？环比 7 月增长了多少？",
        "ground_truth": "8 月工单量为 15,205 件，环比 7 月（14,826 件）增长 2.6%。",
        "key_facts": [
            "8月工单量=15,205件",
            "环比7月增长2.6%",
        ],
        "must_have_docs": ["D3_2026Q3客服运营数据报表.pdf"],
        "must_not_have_docs": [],
        "difficulty": "easy",
        "source_docs": ["D3"],
        "tags": ["分月工单", "环比", "表格"],
    },
    {
        "id": "CS-TN-006",
        "category": "table_numeric",
        "question": "行业报告市场规模表中，2028 年预计市场规模是多少亿元？相比 2025 年增长了多少倍？",
        "ground_truth": "2028E 市场规模预计 810 亿元（企业级渗透率 75%），约为 2025 年（312 亿元）的 2.6 倍。",
        "key_facts": [
            "2028E市场规模=810亿元",
            "2025年=312亿元",
            "约为2.6倍",
        ],
        "must_have_docs": ["D5_客服智能化行业趋势报告.pdf"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D5"],
        "tags": ["市场规模", "表格", "倍率计算"],
    },
    {
        "id": "CS-SM-003",
        "category": "summary",
        "question": "请简要概括智能客服行业当前面临的主要挑战。",
        "ground_truth": "三大挑战：1）数据安全与合规——《个人信息保护法》实施后，对数据本地化、脱敏处理的要求显著提高；2）企业知识库落地成本高——各企业知识体系不同，知识库建设与调优投入大，制约中小企业普及；3）幻觉与准确性瓶颈——专业领域准确性仍不稳定，幻觉是制约深度应用的核心痛点。",
        "key_facts": [
            "挑战一=数据安全与合规",
            "挑战二=知识库落地成本高",
            "挑战三=幻觉与准确性瓶颈",
        ],
        "must_have_docs": ["D5_客服智能化行业趋势报告.pdf"],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": ["D5"],
        "tags": ["行业挑战", "数据合规", "知识库", "幻觉"],
    },
    {
        "id": "CS-UA-003",
        "category": "unanswerable",
        "question": "2026 年 Q1 的客服工单总量是多少？",
        "ground_truth": "现有材料中未提供 2026 年 Q1 的客服工单数据。D3 报表统计周期为 2026 年 Q3，仅提及 Q2 工单总量（41,692 件）与 Q3（46,820 件），无 Q1 数据，无法回答。",
        "key_facts": [
            "D3仅覆盖Q3并含Q2对比数据",
            "未提供Q1工单总量",
            "无法回答",
        ],
        "must_have_docs": [],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": [],
        "tags": ["无答案", "工单量", "Q1", "不可答"],
    },
    {
        "id": "CS-UA-004",
        "category": "unanswerable",
        "question": "客服中心计划在 2027 年上半年新增多少名客服人员？",
        "ground_truth": "现有材料中未提及客服中心 2027 年的人员编制或招聘计划。D3 报表仅给出 2026 年 Q3 各大区现有客服人数，D2 PRD 仅给出智能客服系统的上线计划，均无新增人力规划，无法回答。",
        "key_facts": [
            "无2027年招聘计划数据",
            "D3仅含Q3各区现有人数",
            "D2仅含系统上线计划",
            "无法回答",
        ],
        "must_have_docs": [],
        "must_not_have_docs": [],
        "difficulty": "medium",
        "source_docs": [],
        "tags": ["无答案", "人员编制", "招聘", "不可答"],
    },
]


def main() -> None:
    with open(SRC, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["testset_id"] = "testset_cservice_50"
    data["version"] = "v2.0"
    data["created"] = "2026-09-24"
    data["description"] = (
        "客服业务库完整版 50 题测试集。35 题最小集原样保留，新增 15 题后各类别均衡扩充，"
        "覆盖 fact_single / fact_cross_doc / proper_noun / comparison / table_numeric / summary / unanswerable 七类题型。"
    )
    data["questions"].extend(NEW_QUESTIONS)

    with open(DST, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"written: {DST}  total={len(data['questions'])}")


if __name__ == "__main__":
    main()