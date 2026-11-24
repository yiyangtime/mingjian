"""
上下文预算：把长材料按优先级裁剪到模型窗口可承受的长度。

规则：
  · 估算按「中文 1 字 ≈ 1 token，其它 4 字符 ≈ 1 token」，宁保守勿乐观；
  · 单段超预算时截断并标注，不静默丢内容；
  · 只做文本装配，不改变事实编号与引用关系，模型仍只能引用事实清单内的编号。
"""
from __future__ import annotations

import re

TOKEN_BUDGET = 6000


def estimate(text: str) -> int:
    """粗估 token 数；对中文按字计，对英文/数字按 4 字符计。"""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return int(cjk + other / 4) + 1


def facts_block(facts: list[dict], budget: int = TOKEN_BUDGET) -> tuple[str, dict]:
    """事实清单：保持原有编号与顺序，超预算时只截尾部并注明。"""
    lines, used, cut = [], 0, False
    for f in facts:
        line = f"[{f['id']}] ({f['type_label']}) {f['statement']}"
        cost = estimate(line) + 1
        if lines and used + cost > budget:
            cut = True
            break
        lines.append(line)
        used += cost
    text = "\n".join(lines)
    if cut:
        text += f"\n…（事实清单按上下文预算截断，共 {len(facts)} 条）"
    return text, {"facts": len(lines), "total": len(facts), "tokens": used,
                  "budget": budget, "truncated": cut}
