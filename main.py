"""说说画师：把原先打在 angel_brush 里的 /画图、负面词、去水印、最近生成图记录，
做成一个独立插件；同时提供 LLM 工具，让模型在聊天里也能自己画。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .client import ImageClient, ImageClientError, mask_key, vendor_of

PLUGIN_NAME = "astrbot_plugin_draw"
DEFAULT_NEGATIVE = (
    "人物, 人, 少女, 少年, 人脸, 肖像, 手, 腿, 人形剪影, 人群, 生物, 卡通角色, "
    "文字, 汉字, 字母, 字幕, 水印, logo, 招牌文字, 乱码, 界面UI"
)

TOOL_DESCRIPTION = (
    "根据一段详细的中文画面描述生成一张图片，自动保存到插件数据目录并返回图片的绝对路径。"
    "当用户在聊天里让你画图、要一张配图、或说「画个…」时使用。"
    "生成后必须调用 send_message_to_user 把图片发给用户："
    '{"messages": [{"type": "image", "path": "<返回的图片路径>"}]}；'
    "不要只在文字里写路径，也不要把它当成 markdown 链接。"
)

try:
    from quart import jsonify as _jsonify
    from quart import request as _request
except Exception:  # pragma: no cover
    _jsonify = None
    _request = None


def _data_dir() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

    return Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME


def _default_providers_file() -> str:
    return str(_data_dir() / "providers.json")


def _last_image_file() -> Path:
    return _data_dir() / "last_gen_image.json"


def _options_from_config(config) -> dict[str, Any]:
    def get(key: str, default: Any) -> Any:
        try:
            return config.get(key, default)
        except Exception:
            return default

    def as_int(key: str, default: int) -> int:
        try:
            return int(get(key, default) or default)
        except Exception:
            return default

    return {
        "enabled": bool(get("enabled", True)),
        "providers_file": str(get("providers_file", "") or _default_providers_file()),
        "model": str(get("model", "") or "").strip(),
        "size": str(get("size", "") or "").strip(),
        "negative_prompt": str(get("negative_prompt", "") or ""),
        "style": str(get("style", "") or ""),
        "watermark_off": bool(get("watermark_off", True)),
        "timeout": as_int("timeout", 120),
        "max_prompt_chars": as_int("max_prompt_chars", 600),
    }


def _normalize_options(data: dict[str, Any]) -> dict[str, Any]:
    def as_bool(key: str, default: bool) -> bool:
        value = data.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def as_int(key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(float(data.get(key, default)))
        except Exception as exc:
            raise ValueError(f"{key} 需要是数字") from exc
        return max(low, min(high, value))

    providers_file = str(data.get("providers_file", "") or "").strip()
    if not providers_file:
        raise ValueError("provider 配置文件路径不能为空")

    return {
        "enabled": as_bool("enabled", True),
        "providers_file": providers_file,
        "model": str(data.get("model", "") or "").strip()[:160],
        "size": str(data.get("size", "") or "").strip()[:40],
        "negative_prompt": str(data.get("negative_prompt", "") or "")[:1000],
        "style": str(data.get("style", "") or "")[:1000],
        "watermark_off": as_bool("watermark_off", True),
        "timeout": as_int("timeout", 120, 20, 600),
        "max_prompt_chars": as_int("max_prompt_chars", 600, 50, 2000),
    }


PROVIDER_TYPES = ("openai", "siliconflow", "dashscope", "zhipu", "volcengine", "custom")


def _provider_type(value: Any) -> str:
    text = str(value or "").strip().lower()[:40]
    return text if text in PROVIDER_TYPES else "openai"


def _provider_id(index: int, existing: str = "") -> str:
    value = str(existing or "").strip()
    if value:
        return value
    return f"image-provider-openai-{int(time.time() * 1000)}-{index}"


def _normalize_providers(data: dict[str, Any]) -> dict[str, Any]:
    """把设置页提交的提供方表单校验/规整成 providers.json 的字段。"""
    rows = data.get("providers")
    if not isinstance(rows, list):
        raise ValueError("providers 字段格式不对")

    def as_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(float(value))))
        except Exception:
            return default

    providers: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()[:60]
        if not name:
            raise ValueError(f"第 {index + 1} 个提供方还没有名字")
        base_url = str(row.get("base_url") or "").strip()[:300].rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"提供方「{name}」的 Base URL 要以 http:// 或 https:// 开头")

        models: list[dict[str, Any]] = []
        for model in row.get("models") or []:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("model") or model.get("id") or "").strip()[:160]
            if not model_id:
                continue
            size = str(model.get("default_size") or "").strip()[:20]
            if size and not re.fullmatch(r"\d{2,5}x\d{2,5}", size):
                raise ValueError(f"「{model_id}」的默认尺寸要写成 1024x1024 这种格式")
            entry: dict[str, Any] = {
                "id": str(model.get("id") or "").strip()[:160] or model_id,
                "name": str(model.get("name") or "").strip()[:80] or model_id,
                "model": model_id,
                "enabled": bool(model.get("enabled", True)),
            }
            if size:
                entry["default_size"] = size
            models.append(entry)
        if not models:
            raise ValueError(f"提供方「{name}」至少要有一个模型")

        raw_key = row.get("api_key")
        if isinstance(raw_key, str):
            keys = [k.strip() for k in re.split(r"[,\s]+", raw_key) if k.strip()]
        elif isinstance(raw_key, list):
            keys = [str(k).strip() for k in raw_key if str(k).strip()]
        else:
            keys = []

        provider: dict[str, Any] = {
            "id": _provider_id(index, str(row.get("id") or "")),
            "name": name,
            "provider_type": _provider_type(row.get("provider_type")),
            "enabled": bool(row.get("enabled", True)),
            "base_url": base_url,
            "api_keys": keys,
            "api_key_clear": bool(row.get("api_key_clear")),
            "models": models,
        }
        # 超时/去水印是 providers.json 里的历史字段，页面上不暴露：没提交就保留原值
        if row.get("timeout_seconds") not in (None, ""):
            provider["timeout_seconds"] = as_int(row.get("timeout_seconds"), 600, 10, 1800)
        if "watermark" in row:
            provider["watermark"] = bool(row.get("watermark"))
        providers.append(provider)

    default_model_id = str(data.get("default_model_id") or "").strip()[:200]
    known = {
        f"{p['id']}::{m['model']}" for p in providers for m in p["models"] if m["enabled"]
    }
    if default_model_id not in known:
        default_model_id = next(iter(sorted(known)), "")

    return {
        "providers": providers,
        "default_model_id": default_model_id,
        "fallback_on_error": bool(data.get("fallback_on_error")),
    }


def _thumbnail_data_url(path: Path, max_side: int = 640, quality: int = 78) -> str:
    """页面上只放缩略图，避免几 MB 的原图把 JSON 撑爆。"""
    try:
        import io

        from PIL import Image

        with Image.open(path) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=quality)
            return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


@dataclass
class DrawTool(FunctionTool[AstrAgentContext]):
    name: str = "image_draw"
    description: str = TOOL_DESCRIPTION
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "画面描述：主体、场景、光线、风格、构图、情绪，越具体越好；"
                        "比如「窗台上蹲着一只橘猫，午后阳光，水彩质感，暖色调」。"
                    ),
                },
                "size": {
                    "type": "string",
                    "description": "可选，图片尺寸，例如 1024x1024；不填用默认。",
                },
            },
            "required": ["prompt"],
            "additionalProperties": False,
        }
    )
    plugin: Any = None

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        prompt = str(kwargs.get("prompt") or "").strip()
        size = str(kwargs.get("size") or "").strip() or None
        if not prompt:
            return json.dumps({"ok": False, "error": "prompt 不能为空"}, ensure_ascii=False)
        try:
            paths = await self.plugin.generate(prompt, size=size)
        except Exception as exc:
            logger.warning("[draw] 工具生成失败：%s", exc)
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": True,
                "images": paths,
                "message": (
                    "图片已生成并保存。必须用 send_message_to_user 发送给用户，示例："
                    '{"messages": [{"type": "image", "path": "<上面的路径>"}]}；'
                    "不要在文字里只写路径。"
                ),
            },
            ensure_ascii=False,
        )


class DrawPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self._opts = _options_from_config(config)
        self._apply_options(self._opts)
        self._out_dir = _data_dir() / "generated"
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

        self.tool = DrawTool(plugin=self)
        try:
            self.context.add_llm_tools(self.tool)
        except Exception:
            logger.warning("[draw] 注册 LLM 工具失败", exc_info=True)
        self._register_web_apis()

    def _apply_options(self, values: dict[str, Any]) -> None:
        self._opts = dict(values)
        self.enabled = bool(values.get("enabled"))
        self.providers_file = str(values.get("providers_file") or _default_providers_file())
        self.negative = str(values.get("negative_prompt") or "") or DEFAULT_NEGATIVE
        self.size = str(values.get("size") or "").strip()
        self.watermark_off = bool(values.get("watermark_off"))
        self.timeout = max(10, int(values.get("timeout") or 120))
        self.max_prompt = max(20, int(values.get("max_prompt_chars") or 600))
        self.model = str(values.get("model") or "").strip()
        self.style = str(values.get("style") or "").strip()
        self._client = ImageClient(
            Path(self.providers_file),
            watermark_off=self.watermark_off,
            timeout=self.timeout,
            model=self.model,
            style=self.style,
        )

    # ------------------------------------------------------------------ web api

    def _register_web_apis(self) -> None:
        routes = (
            ("selftest", self.api_selftest, ["GET"], "说说画师：自检"),
            ("page/state", self.api_page_state, ["GET"], "说说画师：页面状态与最近作品"),
            ("page/generate", self.api_page_generate, ["POST"], "说说画师：页面生成"),
            ("page/config", self.api_page_config, ["GET"], "说说画师：读取设置"),
            ("page/save", self.api_page_save, ["POST"], "说说画师：保存设置"),
            ("page/providers", self.api_page_providers, ["GET"], "说说画师：读取提供方"),
            (
                "page/providers/save",
                self.api_page_providers_save,
                ["POST"],
                "说说画师：保存提供方",
            ),
            (
                "page/providers/test",
                self.api_page_providers_test,
                ["POST"],
                "说说画师：测试提供方连通性",
            ),
            (
                "page/providers/clear-key",
                self.api_page_providers_clear_key,
                ["POST"],
                "说说画师：清除提供方 API Key",
            ),
            (
                "page/providers/delete",
                self.api_page_providers_delete,
                ["POST"],
                "说说画师：删除提供方",
            ),
            (
                "page/providers/set-key",
                self.api_page_providers_set_key,
                ["POST"],
                "说说画师：保存提供方 API Key",
            ),
        )
        for endpoint, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/{endpoint}", handler, methods, desc
            )

    async def _body(self) -> dict:
        if _request is None:
            return {}
        try:
            data = await _request.get_json(silent=True)
        except TypeError:
            data = await _request.get_json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _history_path(self) -> Path:
        return _data_dir() / "history.json"

    def _load_history(self) -> list[dict]:
        try:
            if self._history_path().is_file():
                raw = json.loads(self._history_path().read_text(encoding="utf-8-sig"))
                if isinstance(raw, list):
                    return [item for item in raw if isinstance(item, dict)]
        except Exception:
            pass
        return []

    def _push_history(self, prompt: str, paths: list[str]) -> None:
        try:
            history = self._load_history()
            now = int(time.time())
            for path in paths:
                history.append({"path": path, "prompt": prompt, "ts": now})
            history = history[-40:]
            self._history_path().write_text(
                json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.debug("[draw] 写历史失败", exc_info=True)

    @staticmethod
    def _image_payload(entry: dict) -> dict | None:
        path = Path(str(entry.get("path") or ""))
        try:
            if not path.is_file():
                return None
            data = _thumbnail_data_url(path)
        except Exception:
            return None
        return {
            "path": str(path),
            "prompt": str(entry.get("prompt") or ""),
            "ts": int(entry.get("ts") or 0),
            "data": data,
        }

    def _state_payload(self) -> dict:
        entries = [e for e in self._load_history() if e.get("path")][-6:][::-1]
        images = [payload for payload in (self._image_payload(e) for e in entries) if payload]
        model = ""
        try:
            model = self._client.load().get("model") or ""
        except Exception:
            model = ""
        return {"enabled": self.enabled, "model": model, "images": images}

    async def api_page_state(self):
        try:
            return await self._json({"ok": True, "data": self._state_payload()})
        except Exception as exc:
            return await self._json({"ok": False, "error": {"message": str(exc)}})

    async def api_page_generate(self):
        body = await self._body()
        prompt = str(body.get("prompt") or "").strip()
        size = str(body.get("size") or "").strip() or None
        if not prompt:
            return await self._json({"ok": False, "error": {"message": "先写点画面描述"}})
        started = time.time()
        try:
            paths = await self.generate(prompt, size=size)
        except Exception as exc:
            logger.warning("[draw] 页面生成失败：%s", exc)
            return await self._json({"ok": False, "error": {"message": str(exc)}})
        entry = {"path": paths[0], "prompt": prompt, "ts": int(time.time())} if paths else {}
        image = self._image_payload(entry) if entry else None
        return await self._json(
            {
                "ok": True,
                "data": {
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "image": image,
                    "state": self._state_payload(),
                },
            }
        )

    async def api_page_config(self):
        try:
            models = self._client.list_models()
        except Exception:
            models = []
        return await self._json(
            {
                "ok": True,
                "data": {
                    "values": self._options_payload(),
                    "options": {
                        "models": models,
                        "provider": self._client.describe(),
                        "defaults": {"negative_prompt": DEFAULT_NEGATIVE},
                    },
                },
            }
        )

    async def api_page_save(self):
        body = await self._body()
        try:
            values = _normalize_options(body)
        except ValueError as exc:
            return await self._json({"ok": False, "error": {"message": str(exc)}})
        # 这里不校验接口能不能用：允许先保存、生成时再提示
        self._persist_config_values(values)
        self._apply_options(values)
        logger.info("[draw] 设置已保存（model=%s）", self.model or "默认")
        return await self._json(
            {
                "ok": True,
                "data": {
                    "values": self._options_payload(),
                    "provider": self._client.describe(),
                },
            }
        )

    # -------------------------------------------------- 提供方（providers.json）

    def _providers_payload(self) -> dict[str, Any]:
        raw = self._client.read_raw()
        providers: list[dict[str, Any]] = []
        for item in raw.get("providers") or []:
            if not isinstance(item, dict):
                continue
            keys = [str(k).strip() for k in item.get("api_keys") or [] if str(k).strip()]
            models: list[dict[str, Any]] = []
            for model in item.get("models") or []:
                if not isinstance(model, dict):
                    continue
                model_id = str(model.get("model") or model.get("id") or "").strip()
                if not model_id:
                    continue
                models.append(
                    {
                        "id": str(model.get("id") or model_id),
                        "name": str(model.get("name") or model_id),
                        "model": model_id,
                        "enabled": bool(model.get("enabled", True)),
                        "default_size": str(model.get("default_size") or ""),
                    }
                )
            providers.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "provider_type": vendor_of(item),
                    "enabled": bool(item.get("enabled", True)),
                    "base_url": str(item.get("base_url") or ""),
                    "api_key_masked": mask_key(keys[0]) if keys else "",
                    "api_key_count": len(keys),
                    "timeout_seconds": int(item.get("timeout_seconds") or 600),
                    "watermark": bool(item.get("watermark", False)),
                    "models": models,
                }
            )
        path = Path(self.providers_file)
        return {
            "path": str(path),
            "exists": path.is_file(),
            "providers": providers,
            "default_model_id": str(raw.get("default_model_id") or ""),
            "fallback_on_error": bool(raw.get("fallback_on_error", False)),
        }

    async def api_page_providers(self):
        try:
            return await self._json({"ok": True, "data": self._providers_payload()})
        except Exception as exc:
            return await self._json({"ok": False, "error": {"message": str(exc)}})

    async def api_page_providers_save(self):
        body = await self._body()
        try:
            payload = _normalize_providers(body)
        except ValueError as exc:
            return await self._json({"ok": False, "error": {"message": str(exc)}})

        raw = self._client.read_raw() or {}
        existing = {
            str(item.get("id") or ""): item
            for item in raw.get("providers") or []
            if isinstance(item, dict)
        }
        merged: list[dict[str, Any]] = []
        for row in payload["providers"]:
            base = dict(existing.get(row["id"]) or {})
            base.update(
                {k: v for k, v in row.items() if k not in ("api_keys", "api_key_clear")}
            )
            provided = [str(k).strip() for k in row.get("api_keys") or [] if str(k).strip()]
            cleared = bool(row.get("api_key_clear"))
            if provided:
                keys = provided
            elif cleared:
                keys = []
            else:
                keys = [
                    str(k).strip()
                    for k in base.get("api_keys") or []
                    if str(k).strip()
                ]
            base["api_keys"] = keys
            base.setdefault("comfyui_workflow_json", "")
            base.setdefault("comfyui_mapping", {})
            merged.append(base)

        raw["providers"] = merged
        raw["default_model_id"] = payload["default_model_id"]
        raw["fallback_on_error"] = payload["fallback_on_error"]
        try:
            self._client.save_raw(raw)
        except Exception as exc:
            return await self._json(
                {"ok": False, "error": {"message": f"保存失败：{exc}"}}
            )
        self._apply_options(self._opts)
        logger.info("[draw] providers.json 已更新：%s 个提供方", len(merged))
        return await self._json({"ok": True, "data": self._providers_payload()})

    async def api_page_providers_clear_key(self):
        """只清掉某个提供方的 key，立刻落盘，不等「保存设置」。"""
        body = await self._body()
        provider_id = str(body.get("id") or "").strip()
        if not provider_id:
            return await self._json(
                {"ok": False, "error": {"message": "还没保存过的提供方，直接清空输入框即可"}}
            )
        raw = self._client.read_raw() or {}
        found = False
        for item in raw.get("providers") or []:
            if isinstance(item, dict) and str(item.get("id") or "") == provider_id:
                item["api_keys"] = []
                found = True
                break
        if not found:
            return await self._json(
                {"ok": False, "error": {"message": "找不到这个提供方，刷新页面再试"}}
            )
        try:
            self._client.save_raw(raw)
        except Exception as exc:
            return await self._json(
                {"ok": False, "error": {"message": f"清除失败：{exc}"}}
            )
        self._apply_options(self._opts)
        logger.info("[draw] 已清除提供方 %s 的 API Key", provider_id)
        return await self._json({"ok": True, "data": self._providers_payload()})

    async def api_page_providers_set_key(self):
        """只保存某个提供方的 key，立刻落盘。"""
        body = await self._body()
        provider_id = str(body.get("id") or "").strip()
        api_key = str(body.get("api_key") or "").strip()
        if not provider_id:
            return await self._json(
                {"ok": False, "error": {"message": "新提供方请先点「保存设置」创建"}}
            )
        if not api_key:
            return await self._json({"ok": False, "error": {"message": "请先填入 API Key"}})
        raw = self._client.read_raw() or {}
        found = False
        for item in raw.get("providers") or []:
            if isinstance(item, dict) and str(item.get("id") or "") == provider_id:
                item["api_keys"] = [api_key]
                found = True
                break
        if not found:
            return await self._json(
                {"ok": False, "error": {"message": "找不到这个提供方，刷新页面再试"}}
            )
        try:
            self._client.save_raw(raw)
        except Exception as exc:
            return await self._json(
                {"ok": False, "error": {"message": f"保存失败：{exc}"}}
            )
        self._apply_options(self._opts)
        logger.info("[draw] 已更新提供方 %s 的 API Key", provider_id)
        return await self._json({"ok": True, "data": self._providers_payload()})

    async def api_page_providers_delete(self):
        """立刻删除一个提供方（连同它的 key），不等「保存设置」。"""
        body = await self._body()
        provider_id = str(body.get("id") or "").strip()
        if not provider_id:
            return await self._json(
                {"ok": False, "error": {"message": "还没保存过的提供方，直接移除卡片即可"}}
            )
        raw = self._client.read_raw() or {}
        providers = [p for p in raw.get("providers") or [] if isinstance(p, dict)]
        kept = [p for p in providers if str(p.get("id") or "") != provider_id]
        if len(kept) == len(providers):
            return await self._json(
                {"ok": False, "error": {"message": "找不到这个提供方，刷新页面再试"}}
            )
        raw["providers"] = kept
        if not any(
            str(p.get("id") or "") == str(raw.get("default_model_id") or "").split("::")[0]
            for p in kept
        ):
            raw["default_model_id"] = ""
        try:
            self._client.save_raw(raw)
        except Exception as exc:
            return await self._json(
                {"ok": False, "error": {"message": f"删除失败：{exc}"}}
            )
        self._apply_options(self._opts)
        logger.info("[draw] 已删除提供方 %s（剩余 %s 个）", provider_id, len(kept))
        return await self._json({"ok": True, "data": self._providers_payload()})

    async def api_page_providers_test(self):
        body = await self._body()
        provider_id = str(body.get("id") or "")
        base_url = str(body.get("base_url") or "").strip()
        api_key = str(body.get("api_key") or "").strip()
        if not api_key and provider_id:
            keys = self._client.keys_for(provider_id)
            api_key = keys[0] if keys else ""
        result = await self._client.probe(base_url, api_key)
        return await self._json({"ok": True, "data": result})

    def _options_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "providers_file": self.providers_file,
            "model": self.model,
            "size": self.size,
            "negative_prompt": str(self._opts.get("negative_prompt") or ""),
            "style": self.style,
            "watermark_off": self.watermark_off,
            "timeout": self.timeout,
            "max_prompt_chars": self.max_prompt,
        }

    def _persist_config_values(self, values: dict[str, Any]) -> None:
        try:
            self.config.update(values)
            self.config.save_config()
        except Exception:
            logger.warning("[draw] 写回配置失败", exc_info=True)

    @staticmethod
    async def _json(payload: dict) -> Any:
        if _jsonify is None:
            return payload
        return _jsonify(payload)

    async def api_selftest(self):
        flag = ""
        if _request is not None:
            try:
                flag = str((_request.args or {}).get("tool", ""))
            except Exception:
                flag = ""
        started = time.time()
        try:
            if flag == "1":
                raw = await self.tool.call(None, prompt="窗台上蹲着一只橘猫，午后阳光，水彩质感")
                return await self._json({"ok": True, "data": {"tool_result": raw}})
            paths = await self.generate("窗台上蹲着一只橘猫，午后阳光，水彩质感")
        except Exception as exc:
            logger.exception("[draw] 自检失败")
            return await self._json({"ok": False, "error": {"message": str(exc)}})
        return await self._json(
            {
                "ok": True,
                "data": {
                    "count": len(paths),
                    "paths": paths,
                    "elapsed_ms": int((time.time() - started) * 1000),
                },
            }
        )

    # ------------------------------------------------------------------ 生成

    async def generate(
        self,
        prompt: str,
        size: str | None = None,
        *,
        style: str | None = None,
        negative: str | None = None,
    ) -> list[str]:
        """style/negative 传 None 用插件配置；传空串表示这次不要拼风格。

        给其它插件（比如说说配图）按需接管风格用；聊天里的 /画图和 LLM 工具
        不传这两个参数，行为跟以前完全一样。
        """
        if not self.enabled:
            raise ImageClientError("说说画师已关闭")
        prompt = prompt.strip()[: self.max_prompt]
        if not prompt:
            raise ImageClientError("提示词是空的")
        negative_text = self.negative if negative is None else str(negative)
        async with self._lock:
            images = await self._client.generate(
                prompt,
                negative_text,
                size or self.size or None,
                style=style,
            )
            stamp = time.strftime("%Y%m%d-%H%M%S")
            paths: list[str] = []
            for idx, data in enumerate(images):
                path = self._out_dir / f"{stamp}-{idx}.png"
                path.write_bytes(data)
                paths.append(str(path))
            if paths:
                self._remember(paths[0])
                self._push_history(prompt, paths)
            logger.info("[draw] 生成完成：%s 张", len(paths))
            return paths

    def _remember(self, path: str) -> None:
        """把最近生成图写到插件数据目录，给其它插件按需读取。"""
        try:
            target = _last_image_file()
            data: dict[str, Any] = {}
            if target.is_file():
                try:
                    loaded = json.loads(target.read_text(encoding="utf-8-sig"))
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception:
                    data = {}
            data["*"] = {"path": path, "ts": time.time()}
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
        except Exception:
            logger.debug("[draw] 记录最近生成图失败", exc_info=True)

    # ------------------------------------------------------------------ 指令

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("画图", alias={"生图"})
    async def cmd_draw(self, event):
        text = ""
        try:
            text = event.message_str.partition(" ")[2].strip()
        except Exception:
            text = ""
        if not text:
            yield event.plain_result("要画什么？用「画图 描述」的格式")
            return
        yield event.plain_result("画着呢，稍等喵…")
        try:
            paths = await self.generate(text)
        except Exception as exc:
            yield event.plain_result(f"画失败了：{exc}")
            return
        for path in paths:
            yield event.image_result(path)
