"""
可选的模型层（OpenAI 兼容接口）。

存在意义只有一个：把规则生成的四段式草稿"写顺"，
而不是让它凭空产生事实。

所以调用约束极严——只允许改写语气与组织，不允许引入新材料里没有的信息；
返回的每条判断必须带 facts 引用，引用不存在的编号整条丢弃。
没有配置密钥时，本模块完全不被调用，全链路照常运行。
"""
from __future__ import annotations

import json
import re

from ..config import CFG
from ..core import context, net


def available() -> bool:
    return CFG.llm_enabled


def chat(messages: list[dict], *, temperature: float = 0.2, max_tokens: int = 1600) -> str:
    if not CFG.llm_enabled:
        raise RuntimeError("未配置模型")
    return chat_with(CFG.llm_base, CFG.llm_key, CFG.llm_model, CFG.llm_timeout,
                     messages, temperature=temperature, max_tokens=max_tokens)


def chat_with(base: str, key: str, model: str, timeout: float, messages: list[dict], *,
              temperature: float = 0.2, max_tokens: int = 1600) -> str:
    """按显式配置调用一次 OpenAI 兼容接口；供运行时热更新与连接测试复用。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    url = base.rstrip("/") + "/chat/completions"
    raw = net.post_json(url, payload, headers={"Authorization": f"Bearer {key}"},
                        timeout=timeout, retries=1)
    data = json.loads(raw)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"模型未返回 choices：{str(data)[:200]}")
    return (choices[0].get("message") or {}).get("content", "") or ""


def polish_judgments(draft: list[dict], facts: list[dict]) -> list[dict] | None:
    """
    让模型润色"判断"段。返回 None 表示放弃使用（调用失败或校验不通过）。
    校验：每条判断的 citations 必须是真实事实编号的子集。
    """
    if not CFG.llm_enabled or not draft:
        return None
    valid_ids = {f["id"] for f in facts}
    # 事实清单按上下文预算装配：超长只截尾部，编号与顺序不变
    fact_lines, _ctx = context.facts_block(facts)
    draft_lines = "\n".join(
        f"- {d['text']} 引用:{','.join(d.get('citations', []))}" for d in draft)

    sys = ("你是产业情报分析师。仅基于给定事实改写判断句，使其更简洁、更具体。"
           "严禁引入事实清单之外的任何信息、数字或公司名。"
           "每条判断必须保留原有引用编号，不得新增或删除引用。"
           "输出严格 JSON 数组，元素形如 {\"text\":\"...\",\"citations\":[\"F01\"]}，不要输出其它内容。")
    usr = f"事实清单：\n{fact_lines}\n\n待改写判断：\n{draft_lines}"
    try:
        out = chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}])
    except Exception:
        return None

    m = re.search(r"\[[\s\S]*\]", out)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return None

    result, seen = [], set()
    for item in arr:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        cites = [c for c in (item.get("citations") or []) if c in valid_ids]
        if not text or not cites:
            continue           # 无引用即丢弃，这是硬约束
        key = text[:20]
        if key in seen:
            continue
        seen.add(key)
        result.append({"text": text, "citations": cites, "source": "llm"})
    return result or None
