"""P0-2 验证：rrf 分支的分数/排序/去重/单通道与原 weighted 基线均正确。

数据：A/B 在两侧名次呈镜像（A=dense第1/lex第2，B=dense第2/lex第1），
RRF 分数必相等、顺序由稳定排序决定——这是 RRF 特性而非缺陷，测试据此做精确断言。
"""
from rag.hybrid import fuse

dense = [
    {"content": "A", "score": 0.9},
    {"content": "B", "score": 0.6},
    {"content": "C", "score": 0.4},
]
lexical = [
    {"content": "B", "score": 0.95},
    {"content": "A", "score": 0.5},
    {"content": "D", "score": 0.3},
]

w = fuse(dense, lexical, method="weighted")
r = fuse(dense, lexical, method="rrf", top_k=4)

# --- rrf：精确分数断言（k=60，score=Σ 1/(60+rank)）---
# dense rank: A1 B2 C3 ; lexical rank: B1 A2 D3
expect = {
    "A": round(1 / 61 + 1 / 62, 4),  # A: dense1 + lex2
    "B": round(1 / 62 + 1 / 61, 4),  # B: dense2 + lex1
    "C": round(1 / 63, 4),           # C: 仅 dense3
    "D": round(1 / 63, 4),           # D: 仅 lex3
}
got = {h["content"]: h["score"] for h in r}
assert set(got) == {"A", "B", "C", "D"}, "rrf 应汇聚两侧候选（去重）"
for c, e in expect.items():
    assert got[c] == e, (c, got[c], e)

# 镜像名次 → RRF 分数相等（稳定排序决定先后，不视为差异）
assert got["A"] == got["B"]

# --- weighted：回归基线，仍以语义(alpha=0.7)为主 ---
assert [h["content"] for h in w][0] == "A", "weighted 应保持旧行为"

# --- 单通道：rrf 仅依赖名次，顺序与 dense 一致 ---
only_d = fuse(dense, [], method="rrf")
assert [h["content"] for h in only_d] == ["A", "B", "C"]

# --- 仅依赖名次、对分数尺度不敏感：lex 分整体极小时 rrf 仍保留其高名次信息 ---
# （名次镜像时 RRF 分数精确相等，只打印演示，不做顺序硬断言）
dense_v = [{"content": "P", "score": 0.95}, {"content": "Q", "score": 0.90}]
lex_v = [{"content": "Q", "score": 0.02}, {"content": "P", "score": 0.01}]
print("value-case weighted:",
      [h["content"] for h in fuse(dense_v, lex_v, method="weighted", top_k=2)])
print("value-case rrf:",
      [h["content"] for h in fuse(dense_v, lex_v, method="rrf", top_k=2)])

print("P0-2 RRF 验证通过")