"""探针：结构化表索引（列名→语义 + 行值→实体/数值匹配）能否救回「进不了候选集」的表块。

目标问题（根因分析后提出的遗留方向 1）：
- bge-m3 sparse 对中文高频专名（城市/公司/人名）不激活 → 字面命中的表块三路全失
  （q004 3.1 城市分级表块含 query 原词「上海」但 graph/vector/keyword 全 false）。
- 若把表解析为「列名 + 行 cell」，query 实体做**值匹配**（专名精确/数值区间），
  可绕过 sparse 激活盲区——这正是结构化索引相对 sparse 注入的杀手锏。

本探针只验证**召回资格**（结构化匹配能否给缺口表一个进候选池的理由），
不跑完整 RRF+rerank（需 Xinference，历史上 reranker 对数字表失明的风险单独标注）。

用法：`python scripts/probe_struct_table.py`（纯本地，无 LLM / 无 Xinference）
"""
import json, re, glob, sys, os
import collections

CHUNK_DIR = "data/chunks/eval_admin"

# ---------- 1. 解析表块 ----------

def parse_table_blocks():
    """识别 eval_admin 中带【列：】前缀的表块，解析为结构化对象。"""
    tables = []
    for f in glob.glob(f"{CHUNK_DIR}/*.jsonl"):
        for line in open(f):
            u = json.loads(line)
            c = u.get("content", "")
            if "【列：" not in c:
                continue
            t = parse_one_table(c)
            if t is None:
                continue
            t["chunk_id"] = u.get("text_unit_id")
            t["full_doc_id"] = u.get("full_doc_id")
            t["heading"] = u.get("heading")
            t["title_path"] = u.get("title_path")
            tables.append(t)
    return tables


def parse_one_table(content):
    """解析 M2 表块 content：列名前缀行 + markdown pipe 表 → {col_headers, rows:[{col:val}], cellpool}"""
    lines = [ln.strip() for ln in content.splitlines()]
    col_headers = None
    sep_line_re = re.compile(r"^\|[\s:\-\|]+\|?$")
    data_rows = []
    for i, ln in enumerate(lines):
        if ln.startswith("【列：") and "】" in ln:
            inner = ln[4:-1] if ln.startswith("【列：") else ln[3:-1]
            col_headers = [x.strip() for x in inner.split("|") if x.strip()]
            continue
        if not ln.startswith("|"):
            continue
        if col_headers is None or sep_line_re.match(ln):  # 头行/分隔行
            continue
        cells = [x.strip() for x in ln.strip("|").split("|")]
        data_rows.append(cells)
    if col_headers is None:
        return None
    rows = []
    cellpool = set()
    for cells in data_rows:
        row = {}
        for j, ch in enumerate(col_headers):
            v = cells[j] if j < len(cells) else ""
            row[ch] = v
            cellpool.add(v)
        rows.append(row)
    return {
        "col_headers": col_headers,
        "rows": rows,
        "cellpool": cellpool,          # 全部 cell 值（含列表式「北京、上海」整串）
        "num_cols": detect_numeric_cols(col_headers, data_rows),
    }


def detect_numeric_cols(col_headers, data_rows):
    """数值列检测：该列大多数 cell 是数字或数字区间的列。"""
    ncols = len(col_headers)
    num_cols = set()
    for j in range(ncols):
        vals = [r[j] for r in data_rows[:6] if j < len(r)]
        if not vals:
            continue
        n_num = sum(1 for v in vals if re.search(r"\d", v))
        if n_num >= max(1, len(vals) * 0.5):
            num_cols.add(col_headers[j])
    return num_cols


def parse_cell_number(cell):
    """cell → (lo, hi, unit)，支持 '350'、'20-50人'、'10 人以下'、'50 人以上'。"""
    if not cell or not re.search(r"\d", cell):
        return None
    nums = [float(m) for m in re.findall(r"\d+(?:\.\d+)?", cell)]
    if not nums:
        return None
    unit_m = re.search(r"([\u4e00-\u9fff%＄￥o]|[a-zA-Z]+)", cell.replace("箱)", "箱"))
    unit = unit_m.group(1) if unit_m else ""
    if "以下" in cell:
        return (None, nums[0], unit)
    if "以上" in cell:
        return (nums[0], None, unit)
    if len(nums) == 2:
        return (nums[0], nums[1], unit)
    return (nums[0], nums[0], unit)


# ---------- 2. 查询侧抽取（探针级规则，验证机制用） ----------

def extract_numbers(query):
    """query 数字 → [(num, unit)]"""
    out = []
    for m in re.finditer(r"(\d+)\s*([\u4e00-\u9fff%]+)?", query):
        n = int(m.group(1))
        u = (m.group(2) or "").strip()
        if u:
            out.append((n, u))
    return out


def extract_entities(query, extra):
    return list(extra)  # 探针内置实体清单（专名不靠分词，规则难抽）

# ---------- 3. 匹配 ----------

COL_SEM = {   # query 语义词 → 表头/标题命中词（迷你同义词典，验证用）
    "住宿": ["元/晚", "住宿", "城市"],
    "赔偿": ["赔偿", "责任", "赔偿标准", "赔偿比例"],
    "配纸": ["配纸", "打印纸", "纸"],
    "培训": ["培训", "适用场景", "会议室"],
    "预约": ["预约", "审批", "会议室"],
}

def match_table(tbl, qspec):
    """返回 (score, hits) — score>0 即具备候选资格。"""
    text_all = " ".join(tbl["cellpool"]) + " " + " ".join(tbl["col_headers"])
    heading = " ".join([str(tbl["heading"]), *[str(h) for h in (tbl["title_path"] or [])]])
    hits = {"value": [], "num": [], "col": []}

    # value 命中：实体专名 ∈ cell 值（子串/精确）
    for ent in qspec["entities"]:
        for v in tbl["cellpool"]:
            if v and ent in v:
                hits["value"].append((ent, v))
                break
    # num 命中：query 数值落在该表某数值列 cell 区间/等值
    for n, u in qspec["numbers"]:
        for row in tbl["rows"]:
            for ch, v in row.items():
                if ch not in tbl["num_cols"] or not re.search(r"\d", v):
                    continue
                rng = parse_cell_number(v)
                if not rng:
                    continue
                lo, hi, cunit = rng
                if u and cunit and u not in cunit and cunit not in u:
                    continue
                if (lo is None or n >= lo) and (hi is None or n <= hi):
                    hits["num"].append((n, u, ch, v))
    # col/标题 语义命中：query 语义词 → 表头 或 标题
    for kw in qspec["col_kw"]:
        for key in COL_SEM.get(kw, [kw]):
            hit = (key in text_all) or (key in heading)
            if hit:
                hits["col"].append((kw, key))
                break

    score = 3 * len(hits["value"]) + 2 * len(hits["num"]) + 1 * len(hits["col"])
    return score, hits


# ---------- 4. 探针用例 ----------

CASES = [
    {
        "id": "adm_q004", "cat": "table_numeric", "需要表fct": ["普通员工一类=350元/晚", "上海属于一类城市"],
        "query": "出差到上海，普通员工的住宿标准是多少元一晚？",
        "extra_entities": ["上海", "普通员工"],
        "col_kw": ["住宿"],
        "期望命中的表": "3.2 住宿费标准|3.1 城市分级",
    },
    {
        "id": "adm_q008", "cat": "fact_cross_doc",
        "需要表fct": ["疏忽大意30%/严重过失50%/故意100%"],
        "query": "员工出差期间，公司配备的笔记本电脑如果丢失了，应该怎么处理？涉及哪些制度？",
        "extra_entities": ["笔记本电脑"],
        "col_kw": ["赔偿"],
        "期望命中的表": "6.2 赔偿标准",
    },
    {
        "id": "adm_q013", "cat": "table_numeric",
        "需要表fct": ["20-50人=4箱", "25人在20-50区间"],
        "query": "一个25人的部门，每月可以领多少箱A4打印纸？",
        "extra_entities": [],
        "col_kw": ["配纸"],
        "期望命中的表": "2.3 部门公共用品（打印纸）",
    },
    {
        "id": "adm_q020", "cat": "fact_cross_doc",
        "需要表fct": ["大会议室40人", "适用全员大会/入职培训/季度总结"],
        "query": "新员工入职次日的培训在哪里举行？该会议室有什么特点、预约有什么要求？",
        "extra_entities": ["入职培训"],
        "col_kw": ["培训"],
        "期望命中的表": "2.1 会议室清单",
    },
    {
        "id": "adm_q010", "cat": "proper_noun",
        "需要表fct": [],  # 无表类事实
        "query": "什么是FAS？在什么语境下使用？",
        "extra_entities": [],
        "col_kw": [],
        "期望命中的表": None,
    },
    {
        "id": "adm_q024", "cat": "comparison",
        "需要表fct": [],  # 正文制度对比，无表
        "query": "标准工时制和弹性工作制有什么核心区别？",
        "extra_entities": [],
        "col_kw": [],
        "期望命中的表": None,
    },
]


def main():
    tables = parse_table_blocks()
    out_json = "tests/reports/probe_struct_table.json"
    results = {"collection": "eval_admin", "n_table_blocks": len(tables),
               "note": "结构化表索引探针：值/数值/列语义匹配能否给缺口表块候选资格。score>0=具备进池理由。",
               "cases": []}
    print(f"解析到表块 {len(tables)} 个（eval_admin 共 203 块）")
    print("=" * 78)
    for case in CASES:
        qspec = {
            "entities": extract_entities(case["query"], case["extra_entities"]),
            "numbers": extract_numbers(case["query"]),
            "col_kw": case["col_kw"],
        }
        scored = []
        for t in tables:
            score, hits = match_table(t, qspec)
            if score > 0:
                scored.append((score, t, hits))
        scored.sort(key=lambda x: -x[0])
        case_rep = {
            "id": case["id"], "cat": case["cat"], "query": case["query"],
            "qspec": qspec, "expect": case["期望命中的表"],
            "top_hits": [
                {"score": s, "heading": str(t["heading"]), "title_path": list(map(str, t["title_path"] or [])),
                 "value_hits": h["value"], "num_hits": h["num"], "col_hits": h["col"]}
                for s, t, h in scored[:4]
            ],
        }
        results["cases"].append(case_rep)
        print(f"\n### {case['id']} [{case['cat']}] {case['query'][:38]}…")
        print(f"  query抽取: entities={qspec['entities']} numbers={qspec['numbers']} col_kw={qspec['col_kw']}")
        if not scored:
            print("  → 结构化命中：无任何表块命中（该题无表类口径）")
            continue
        for score, t, hits in scored[:4]:
            print(f"  [score={score}] {t['heading']}")
            print(f"     值命中={hits['value']}  数值命中={hits['num']}  列/标题语义命中={hits['col']}")
        print(f"  > 期望命中：{case['期望命中的表']}")
    with open(out_json, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[report] save -> {out_json}")

if __name__ == "__main__":
    main()