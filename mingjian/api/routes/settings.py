"""接入中心路由：模型接入与工商数据源的保存、启用、删除与连接测试。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ...infra import llmcfg, registry, store

router = APIRouter(tags=["接入中心"])


@router.get("/api/access/llm/config")
def get_llm_config():
    return llmcfg.public()


@router.post("/api/access/llm/config")
def save_llm_config(data: dict | None = Body(default=None)):
    data = data or {}
    actor = str(data.get("actor") or "").strip() or "业务用户"
    action = str(data.get("action") or "").strip()
    target = str(data.get("name") or data.get("id") or "").strip()
    try:
        if action == "activate":
            out = llmcfg.activate(str(data.get("id") or ""))
            store.add_audit("", "", "llm_activate", target or out.get("model") or "",
                            reason="启用模型配置", actor=actor)
        elif action == "delete":
            out = llmcfg.delete_profile(str(data.get("id") or ""))
            store.add_audit("", "", "llm_delete", target,
                            reason="删除模型配置", actor=actor)
        elif action in ("", "save") and not data.get("clear"):
            out = llmcfg.save_profile(data)
            store.add_audit("", "", "llm_config", out.get("model") or "",
                            reason=out.get("base") or "", actor=actor)
        elif action in ("", "clear") and data.get("clear"):
            out = llmcfg.clear()
            store.add_audit("", "", "llm_clear", "", reason="清除模型配置",
                            actor=actor)
        else:
            raise HTTPException(400, "不支持的操作")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"配置保存失败：{type(e).__name__}: {e}")
    return {"ok": True, **out}


@router.post("/api/access/llm/test")
def test_llm(data: dict | None = Body(default=None)):
    data = data or {}
    try:
        return llmcfg.probe(data.get("base") or "", data.get("key") or "",
                            data.get("model") or "", data.get("timeout"),
                            profile_id=str(data.get("profile_id") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/access/registry/config")
def get_registry_config():
    return registry.public()


@router.post("/api/access/registry/config")
def save_registry_config(data: dict | None = Body(default=None)):
    """工商数据源配置。密钥只落库，前端只拿得到尾号。"""
    data = data or {}
    actor = str(data.get("actor") or "").strip() or "业务用户"
    action = str(data.get("action") or "").strip()
    target = str(data.get("name") or data.get("id") or "").strip()
    try:
        if action == "activate":
            out = registry.activate(str(data.get("id") or ""))
            store.add_audit("", "", "registry_activate", target or out.get("active_id") or "",
                            reason="启用工商数据源", actor=actor)
        elif action == "delete":
            out = registry.delete_profile(str(data.get("id") or ""))
            store.add_audit("", "", "registry_delete", target,
                            reason="删除工商数据源配置", actor=actor)
        elif action == "clear":
            out = registry.clear()
            store.add_audit("", "", "registry_clear", "", reason="停用工商数据源", actor=actor)
        else:
            out = registry.save_profile(data)
            store.add_audit("", "", "registry_config", target or out.get("active_id") or "",
                            reason=str(data.get("base") or ""), actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"配置保存失败：{type(e).__name__}: {e}")
    return {"ok": True, **out}


@router.post("/api/access/registry/test")
def test_registry(data: dict | None = Body(default=None)):
    data = data or {}
    try:
        return registry.probe(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
