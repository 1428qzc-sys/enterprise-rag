"""RAG 评估脚本。

指标：
- Hit@k：top-k 命中率（返回片段中是否包含标注的相关关键词/答案要点）
- MRR：首个命中片段的平均倒数排名
- （可选）RAGAS：faithfulness / answer_relevancy / context_precision，需安装 ragas

用法：
    # 先确保后端已启动，并已在某知识库导入 sample-docs
    python scripts/evaluate.py --api http://localhost:8000 --kb-id <KB_ID> \
        --dataset scripts/sample_eval.jsonl --top-k 5
    # 附加答案质量评估（需 pip install -r requirements-optional.txt 且配置 LLM）
    python scripts/evaluate.py --kb-id <KB_ID> --dataset scripts/sample_eval.jsonl --ragas

数据集格式（JSONL，每行一条）：
    {"question": "年假多少天", "relevant": ["15 天", "年假"], "ground_truth": "每年15天带薪年假"}
"""

from __future__ import annotations

import argparse
import json
from typing import Dict, List

import httpx


def load_dataset(path: str) -> List[Dict]:
    items: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def retrieve(api: str, kb_id: str, query: str, top_k: int) -> List[Dict]:
    resp = httpx.post(
        f"{api}/api/retrieve",
        json={"kb_id": kb_id, "query": query, "top_k": top_k},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["results"]


def chat(api: str, kb_id: str, question: str) -> Dict:
    resp = httpx.post(
        f"{api}/api/chat",
        json={"kb_id": kb_id, "question": question, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _first_hit_rank(results: List[Dict], relevant: List[str]) -> int:
    """返回首个命中片段的 1-based 排名，未命中返回 0。"""
    for rank, item in enumerate(results, start=1):
        haystack = (item.get("content", "") + " " + item.get("document_name", "")).lower()
        if any(kw.lower() in haystack for kw in relevant):
            return rank
    return 0


def evaluate_retrieval(api: str, kb_id: str, dataset: List[Dict], top_k: int) -> None:
    hits = 0
    rr_sum = 0.0
    print("=" * 68)
    print(f"检索评估  |  样本数={len(dataset)}  top_k={top_k}")
    print("=" * 68)
    for i, item in enumerate(dataset, start=1):
        q = item["question"]
        relevant = item.get("relevant") or ([item["ground_truth"]] if item.get("ground_truth") else [])
        results = retrieve(api, kb_id, q, top_k)
        rank = _first_hit_rank(results, relevant)
        hit = rank > 0
        hits += int(hit)
        rr_sum += (1.0 / rank) if rank else 0.0
        flag = f"✓ rank={rank}" if hit else "✗ miss"
        print(f"[{i:>2}] {flag:<12} {q}")
    n = len(dataset) or 1
    print("-" * 68)
    print(f"Hit@{top_k} = {hits}/{len(dataset)} = {hits / n:.2%}")
    print(f"MRR       = {rr_sum / n:.4f}")
    print()


def evaluate_ragas(api: str, kb_id: str, dataset: List[Dict], top_k: int) -> None:
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, faithfulness
    except Exception:
        print("[RAGAS] 未安装，跳过答案质量评估。安装：pip install -r requirements-optional.txt")
        return

    rows = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    for item in dataset:
        q = item["question"]
        results = retrieve(api, kb_id, q, top_k)
        ans = chat(api, kb_id, q)["answer"]
        rows["question"].append(q)
        rows["answer"].append(ans)
        rows["contexts"].append([r["content"] for r in results])
        rows["ground_truth"].append(item.get("ground_truth", ""))

    ds = Dataset.from_dict(rows)
    result = evaluate(ds, metrics=[faithfulness, answer_relevancy, context_precision])
    print("=" * 68)
    print("RAGAS 答案质量评估")
    print("=" * 68)
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Enterprise RAG 评估")
    parser.add_argument("--api", default="http://localhost:8000", help="后端地址")
    parser.add_argument("--kb-id", required=True, help="目标知识库 id")
    parser.add_argument("--dataset", default="scripts/sample_eval.jsonl", help="评测集 JSONL")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ragas", action="store_true", help="附加 RAGAS 答案质量评估")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    evaluate_retrieval(args.api, args.kb_id, dataset, args.top_k)
    if args.ragas:
        evaluate_ragas(args.api, args.kb_id, dataset, args.top_k)


if __name__ == "__main__":
    main()
