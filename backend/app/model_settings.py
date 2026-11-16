"""Local credentials protected by Windows DPAPI or an explicit container secret."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import threading
from ctypes import wintypes
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.config import settings

settings_lock = threading.RLock()


class SecretStorageError(RuntimeError):
    """Safe-to-display storage error; never contains credential contents."""


class ModelSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    thinking_mode: Literal["auto", "enabled", "disabled"] = "auto"
    reasoning_effort: Literal["low", "high", "max"] = "low"

    @model_validator(mode="after")
    def validate_thinking_support(self):
        if self.thinking_mode != "auto" and urlsplit(self.base_url).hostname != "api.deepseek.com":
            raise ValueError("显式思考模式目前仅支持官方 DeepSeek 接口，其他服务请选择自动兼容。")
        return self

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        try:
            url = urlsplit(value)
            host = url.hostname
            _ = url.port
        except ValueError as exc:
            raise ValueError("接口地址格式不正确") from exc
        if (not host or url.username is not None or url.password is not None
                or url.query or url.fragment or "\\" in value or any(c.isspace() for c in value)):
            raise ValueError("接口地址不能包含账号、密码、查询参数、片段或空白")
        try:
            local = ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if url.scheme != "https" and not (url.scheme == "http" and local):
            raise ValueError("外部接口必须使用 HTTPS，HTTP 仅允许本机模型")
        if url.path.endswith("/chat/completions"):
            raise ValueError("请填写基础地址，去掉末尾的 /chat/completions")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("请填写有效的模型名称")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        raw = value.get_secret_value().strip()
        if len(raw) > 8192 or any(ord(c) < 32 for c in raw):
            raise ValueError("密钥格式不正确")
        return SecretStr(raw)


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if settings.local_secret_key_file is not None:
        try:
            if settings.local_secret_key_file.stat().st_size != 44:
                raise ValueError("invalid key length")
            cipher = Fernet(settings.local_secret_key_file.read_bytes())
            return cipher.decrypt(data) if decrypt else cipher.encrypt(data)
        except (OSError, ValueError, InvalidToken):
            raise SecretStorageError("容器加密密钥不可用或配置损坏，未保存明文密钥。") from None
    if sys.platform != "win32":
        raise SecretStorageError("网页密钥保存目前需要 Windows 系统加密支持，未保存任何明文密钥。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    protect = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    protect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    protect.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    # UI_FORBIDDEN; do not use LOCAL_MACHINE, which permits other Windows users.
    if not protect(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise SecretStorageError("系统密钥加解密失败，请在当前 Windows 账号下重新填写模型设置。")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def _config_path():
    name = "model-settings.container.bin" if settings.local_secret_key_file else "model-settings.bin"
    path = settings.local_data_dir.resolve() / name
    if path.resolve() != path:
        raise SecretStorageError("模型配置路径异常，请移除配置文件的链接后重试。")
    return path


def load_saved_settings() -> ModelSettingsInput | None:
    with settings_lock:
        path = _config_path()
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 65536:
                raise ValueError("oversized encrypted settings")
            config = ModelSettingsInput.model_validate_json(_dpapi(path.read_bytes(), decrypt=True))
            if config.api_key is None:
                raise ValueError("missing key")
            return config
        except SecretStorageError:
            raise
        except Exception as exc:
            raise SecretStorageError("无法读取加密模型设置，请重新填写并保存。") from exc


def resolve_model_input(config: ModelSettingsInput) -> ModelSettingsInput:
    with settings_lock:
        if config.api_key is not None:
            return config
        saved = load_saved_settings()
        old_url = saved.base_url if saved else settings.llm_base_url
        old_key = saved.api_key if saved else (
            SecretStr(settings.llm_api_key) if settings.llm_provider == "openai_compatible" and settings.llm_api_key else None
        )
        if not old_key or not old_url or old_url.rstrip("/") != config.base_url:
            raise ValueError("首次配置或更换接口地址时，请重新输入 API 密钥。")
        return config.model_copy(update={"api_key": old_key})


def save_model_settings(config: ModelSettingsInput) -> None:
    with settings_lock:
        config = resolve_model_input(config)
        payload = json.dumps({
            **config.model_dump(exclude={"api_key"}),
            "api_key": config.api_key.get_secret_value(),
        }).encode("utf-8")
        encrypted = _dpapi(payload)
        path = _config_path()
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as file:
                temporary = file.name
                file.write(encrypted)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise SecretStorageError("模型配置未能保存，原配置已保留，请检查磁盘空间和目录权限。") from exc
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def get_model_status() -> dict:
    saved = load_saved_settings()
    if saved:
        return {**saved.model_dump(exclude={"api_key"}), "provider": "openai_compatible",
                "thinking_supported": urlsplit(saved.base_url).hostname == "api.deepseek.com",
                "has_api_key": True, "configured": True, "encrypted": True, "source": "web"}
    provider = settings.llm_provider
    key = {"openai_compatible": settings.llm_api_key, "groq": settings.groq_api_key,
           "gemini": settings.gemini_api_key}.get(provider)
    model = {"openai_compatible": settings.llm_model, "groq": settings.groq_model,
             "gemini": settings.primary_model}.get(provider)
    ready = bool(key and model and (provider != "openai_compatible" or settings.llm_base_url))
    return {"provider": provider, "base_url": settings.llm_base_url or "", "model": model or "",
            "thinking_mode": "auto", "reasoning_effort": "low",
            "thinking_supported": provider == "openai_compatible" and urlsplit(settings.llm_base_url or "").hostname == "api.deepseek.com",
            "has_api_key": bool(key), "configured": ready, "encrypted": False,
            "source": "env" if key or settings.llm_base_url or settings.llm_model else "none"}
