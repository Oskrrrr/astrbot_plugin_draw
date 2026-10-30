"""读 angel_brush 的 providers.json，直接调 SiliconFlow 生图接口。

只依赖公开的 /images/generations 协议，不改 angel_brush 一行代码。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger


class ImageClientError(RuntimeError):
    pass


def _trim(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def mask_key(key: str) -> str:
    """页面上只显示 key 的头尾，避免整串明文贴在设置页里。"""
    key = _trim(key)
    if not key:
        return ""
    if len(key) <= 10:
        return "已配置"
    return f"{key[:6]}…{key[-4:]}"


VENDORS = ("siliconflow", "openai", "dashscope", "zhipu", "volcengine", "custom")


def vendor_of(provider: dict[str, Any]) -> str:
    """老文件里 provider_type 一律是 openai，靠 base_url 认出具体厂商。"""
    ptype = _trim(provider.get("provider_type")).lower()
    url = _trim(provider.get("base_url")).lower()
    if ptype in VENDORS and ptype != "openai":
        return ptype
    if "siliconflow" in url:
        return "siliconflow"
    if ptype == "openai":
        return "openai"
    return "custom"


class ImageClient:
    def __init__(
        self,
        providers_file: Path,
        *,
        watermark_off: bool = True,
        timeout: int = 120,
        model: str = "",
        style: str = "",
    ) -> None:
        self.providers_file = Path(providers_file)
        self.watermark_off = watermark_off
        self.timeout = max(10, int(timeout))
        self.model = _trim(model)
        self.style = _trim(style)

    # ------------------------------------------------------------------ 配置

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.providers_file.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise ImageClientError(f"读不到 providers.json: {exc}") from exc
        if not isinstance(data, dict):
            raise ImageClientError("providers.json 格式不对")
        return data

    @staticmethod
    def _enabled_models(provider: dict[str, Any]) -> list[dict[str, Any]]:
        models = [m for m in provider.get("models") or [] if isinstance(m, dict)]
        return [m for m in models if m.get("enabled", True)]

    def list_models(self) -> list[dict[str, Any]]:
        data = self._read()
        out: list[dict[str, Any]] = []
        for provider in data.get("providers") or []:
            if not isinstance(provider, dict) or not provider.get("enabled", True):
                continue
            provider_id = _trim(provider.get("id"))
            provider_name = _trim(provider.get("name")) or provider_id
            for model in self._enabled_models(provider):
                model_id = _trim(model.get("model")) or _trim(model.get("id"))
                if not model_id:
                    continue
                out.append(
                    {
                        "id": f"{provider_id}::{model_id}",
                        "label": f"{provider_name} · {model_id}",
                        "size": _trim(model.get("default_size")),
                    }
                )
        return out

    @staticmethod
    def _provider_type(provider: dict[str, Any]) -> str:
        return vendor_of(provider)

    @staticmethod
    def _supports_negative(provider_type: str) -> bool:
        """只有明确支持 negative_prompt 字段的接口才单独传；其余拼进 prompt。"""
        return provider_type in ("siliconflow", "custom", "")

    def _build_configs(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        # 画风优先取插件配置；老版本存在 providers.json 的 custom_style 作为兜底
        style = self.style or _trim(data.get("custom_style"))
        out: list[dict[str, Any]] = []
        for provider in data.get("providers") or []:
            if not isinstance(provider, dict) or not provider.get("enabled", True):
                continue
            keys = [_trim(k) for k in provider.get("api_keys") or [] if _trim(k)]
            base_url = _trim(provider.get("base_url")).rstrip("/")
            if not keys or not base_url:
                continue
            provider_id = _trim(provider.get("id"))
            ptype = self._provider_type(provider)
            for model in self._enabled_models(provider):
                model_id = _trim(model.get("model")) or _trim(model.get("id"))
                if not model_id:
                    continue
                out.append(
                    {
                        "provider_id": provider_id,
                        "provider": _trim(provider.get("name")) or provider_id,
                        "provider_type": ptype,
                        "base_url": base_url,
                        "api_key": keys[0],
                        "model": model_id,
                        "model_key": f"{provider_id}::{model_id}",
                        "size": _trim(model.get("default_size")) or "1024x1024",
                        "style": style,
                        "supports_negative": self._supports_negative(ptype),
                    }
                )
        return out

    def configs(self) -> list[dict[str, Any]]:
        return self._build_configs(self._read())

    def _no_model_error(self, data: dict[str, Any]) -> ImageClientError:
        all_providers = [p for p in data.get("providers") or [] if isinstance(p, dict)]
        if not all_providers:
            return ImageClientError("还没有配置图片接口，去「画图 → 设置」加一个提供方")
        enabled = [p for p in all_providers if p.get("enabled", True)]
        if not enabled:
            return ImageClientError("提供方都被停用了，启用一个再试")
        return ImageClientError("提供方还没有配置 API Key，或没有启用的模型")

    def load(self) -> dict[str, Any]:
        data = self._read()
        wanted = self.model or _trim(data.get("default_model_id"))
        configs = self._build_configs(data)
        if not configs:
            raise self._no_model_error(data)
        for cfg in configs:
            if wanted and wanted in (cfg["model_key"], cfg["model"]):
                return cfg
        return configs[0]

    def describe(self) -> dict[str, Any]:
        try:
            cfg = self.load()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        key = cfg["api_key"]
        masked = f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "已配置"
        return {
            "ok": True,
            "provider": cfg["provider"],
            "provider_type": cfg["provider_type"],
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "size": cfg["size"],
            "api_key": masked,
        }

    # ------------------------------------------------------------------ 页面读写

    def read_raw(self) -> dict[str, Any]:
        """给设置页用：文件不存在就返回空结构，不抛异常。"""
        try:
            return self._read()
        except ImageClientError:
            return {}

    def validate(self) -> None:
        """只校验结构和可读性：允许没有提供方、也允许暂时没有 API Key。"""
        data = self._read()
        providers = data.get("providers")
        if not isinstance(providers, list):
            raise ImageClientError("providers 需要是列表")
        for provider in providers:
            if not isinstance(provider, dict):
                raise ImageClientError("providers.json 里的 provider 格式不对")
            if not _trim(provider.get("base_url")):
                raise ImageClientError("有个 provider 没填 base_url")

    def save_raw(self, data: dict[str, Any]) -> None:
        """先写 .pending 并用它试解析，过了再原子替换原文件，并留一份 .bak。"""
        text = json.dumps(data, ensure_ascii=False, indent=2)
        pending = Path(str(self.providers_file) + ".pending")
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(text, encoding="utf-8")
        try:
            probe = ImageClient(
                pending,
                watermark_off=self.watermark_off,
                timeout=self.timeout,
                model=self.model,
            )
            probe.validate()
            if self.providers_file.is_file():
                backup = Path(str(self.providers_file) + ".bak")
                try:
                    shutil.copyfile(self.providers_file, backup)
                except Exception:
                    logger.debug("[draw] 备份 providers.json 失败", exc_info=True)
            os.replace(pending, self.providers_file)
        finally:
            try:
                if pending.exists():
                    pending.unlink()
            except Exception:
                pass

    def keys_for(self, provider_id: str) -> list[str]:
        wanted = _trim(provider_id)
        for provider in self.read_raw().get("providers") or []:
            if not isinstance(provider, dict):
                continue
            if _trim(provider.get("id")) == wanted:
                return [_trim(k) for k in provider.get("api_keys") or [] if _trim(k)]
        return []

    async def probe(self, base_url: str, api_key: str) -> dict[str, Any]:
        """连通性检查：只拉一次 /models，不产生生图费用。"""
        url = _trim(base_url).rstrip("/")
        key = _trim(api_key)
        if not url:
            return {"ok": False, "error": "还缺 base_url"}
        if not key:
            return {"ok": False, "error": "还缺 API Key"}
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.get(f"{url}/models", headers=headers) as resp:
                    status = resp.status
                    body = await resp.text()
        except Exception as exc:
            return {"ok": False, "error": f"连不上：{exc}"}
        if status >= 400:
            return {"ok": False, "status": status, "error": f"HTTP {status}：{body[:200]}"}
        ids: list[str] = []
        try:
            data = json.loads(body)
        except Exception:
            return {"ok": True, "status": status, "count": 0, "models": []}
        items = data.get("data") if isinstance(data, dict) else None
        for item in items or []:
            if isinstance(item, dict) and _trim(item.get("id")):
                ids.append(_trim(item.get("id")))
        return {"ok": True, "status": status, "count": len(ids), "models": ids[:300]}

    # ------------------------------------------------------------------ 请求

    async def generate(
        self,
        prompt: str,
        negative: str,
        size: str | None = None,
        *,
        with_style: bool = True,
        fallback: bool | None = None,
        style: str | None = None,
    ) -> list[bytes]:
        data = self._read()
        configs = self._build_configs(data)
        if not configs:
            raise self._no_model_error(data)
        wanted = self.model or _trim(data.get("default_model_id"))
        idx = 0
        for i, cfg in enumerate(configs):
            if wanted and wanted in (cfg["model_key"], cfg["model"]):
                idx = i
                break
        ordered = [configs[idx]] + [c for i, c in enumerate(configs) if i != idx]
        if fallback is None:
            fallback = bool(data.get("fallback_on_error"))
        if not fallback:
            ordered = ordered[:1]

        errors: list[str] = []
        for position, cfg in enumerate(ordered):
            try:
                return await self._generate_with(
                    cfg, prompt, negative, size, with_style=with_style, style=style
                )
            except ImageClientError as exc:
                errors.append(f"{cfg['provider']}/{cfg['model']}：{exc}")
                if not fallback or position == len(ordered) - 1:
                    break
                logger.warning("[draw] %s 失败，换下一个提供方重试", cfg["provider"])
        raise ImageClientError("；".join(errors)[:400] or "生图失败")

    async def _generate_with(
        self,
        cfg: dict[str, Any],
        prompt: str,
        negative: str,
        size: str | None,
        *,
        with_style: bool = True,
        style: str | None = None,
    ) -> list[bytes]:
        text = prompt.strip()
        style_text = cfg["style"] if style is None else _trim(style)
        if with_style and style_text:
            text = f"{text}，{style_text}"

        payload: dict[str, Any] = {
            "model": cfg["model"],
            "prompt": text,
            "n": 1,
            "size": size or cfg["size"],
        }
        if "gpt-image" not in cfg["model"].lower() and "dall-e" not in cfg["model"].lower():
            payload["response_format"] = "b64_json"
        neg = negative.strip()
        if neg:
            if cfg["supports_negative"]:
                # 拼进 prompt 的文字式"禁止"基本无效，必须走独立字段
                payload["negative_prompt"] = neg
            else:
                # 不支持 negative_prompt 的接口，只能退化成 prompt 里的文字约束
                payload["prompt"] = f"{text}。画面中不要出现：{neg}"

        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        if self.watermark_off:
            headers["X-Enable-Watermark"] = "0"
        url = f"{cfg['base_url']}/images/generations"

        images: list[bytes] = []
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        raise ImageClientError(
                            f"HTTP {resp.status}: {body[:300]}"
                        )
                    try:
                        data = json.loads(body)
                    except Exception as exc:
                        raise ImageClientError(f"生图返回无法解析: {body[:200]}") from exc
                    items = data.get("data") or data.get("images") or []
                    if not isinstance(items, list):
                        raise ImageClientError("生图返回里没有图片")
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        b64 = _trim(item.get("b64_json"))
                        if b64:
                            try:
                                images.append(base64.b64decode(b64))
                                continue
                            except Exception:
                                logger.warning("[draw] b64 解码失败，跳过一张")
                        image_url = _trim(item.get("url"))
                        if image_url:
                            async with session.get(image_url) as img_resp:
                                if img_resp.status < 400:
                                    images.append(await img_resp.read())
                                else:
                                    logger.warning("[draw] 下载生成图失败 HTTP %s", img_resp.status)
        except aiohttp.ClientError as exc:
            raise ImageClientError(f"生图请求失败: {exc}") from exc

        if not images:
            raise ImageClientError("生图没有返回可用图片")
        return images

