"""火山引擎豆包语音：大模型录音文件极速版识别（flash）。"""

from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 文档：https://www.volcengine.com/docs/6561/1631584?lang=zh
VOLC_BIGMODEL_FLASH_RECOGNIZE_URL = (
    "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
)
VOLC_BIGASR_RESOURCE_ID = "volc.bigasr.auc_turbo"


@dataclass(frozen=True)
class VolcSpeechCredentials:
    """新版控制台：仅 X-Api-Key；旧版：X-Api-App-Key + X-Api-Access-Key。"""

    uid: str
    api_key: str | None = None
    app_key: str | None = None
    access_key: str | None = None

    def build_headers(self, *, request_id: str | None = None) -> dict[str, str]:
        rid = request_id or str(uuid.uuid4())
        headers: dict[str, str] = {
            "X-Api-Resource-Id": VOLC_BIGASR_RESOURCE_ID,
            "X-Api-Request-Id": rid,
            "X-Api-Sequence": "-1",
        }
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        else:
            if not self.app_key or not self.access_key:
                raise ValueError("volc speech: missing app_key/access_key for legacy credentials")
            headers["X-Api-App-Key"] = self.app_key
            headers["X-Api-Access-Key"] = self.access_key
        return headers


def resolve_volc_speech_credentials_from_environ() -> VolcSpeechCredentials | None:
    """
    环境变量（任选其一）：
    - 新版：VOLC_SPEECH_API_KEY；可选 VOLC_SPEECH_UID（默认与 API Key 相同）
    - 旧版：VOLC_SPEECH_APP_KEY + VOLC_SPEECH_ACCESS_KEY；可选 VOLC_SPEECH_UID
    """
    api_key = str(os.environ.get("VOLC_SPEECH_API_KEY") or "").strip()
    if api_key:
        uid = str(os.environ.get("VOLC_SPEECH_UID") or "").strip() or api_key
        return VolcSpeechCredentials(uid=uid, api_key=api_key)
    app_key = str(os.environ.get("VOLC_SPEECH_APP_KEY") or "").strip()
    access_key = str(os.environ.get("VOLC_SPEECH_ACCESS_KEY") or "").strip()
    if app_key and access_key:
        uid = str(os.environ.get("VOLC_SPEECH_UID") or "").strip() or app_key
        return VolcSpeechCredentials(uid=uid, app_key=app_key, access_key=access_key)
    return None


def resolve_volc_speech_credentials_from_mapping(
    config: Mapping[str, Any] | None,
) -> VolcSpeechCredentials | None:
    """
    从配置 dict 读取：
    - volc_speech_api_key（新控制台 X-Api-Key）
    - 或 volc_speech_app_key + volc_speech_access_key（旧控制台）
    - 可选 volc_speech_uid（默认与 api_key 或 app_key 相同）
    """
    if not config:
        return None
    api_key = str(config.get("volc_speech_api_key") or "").strip()
    if api_key:
        uid = str(config.get("volc_speech_uid") or "").strip() or api_key
        return VolcSpeechCredentials(uid=uid, api_key=api_key)
    app_key = str(config.get("volc_speech_app_key") or "").strip()
    access_key = str(config.get("volc_speech_access_key") or "").strip()
    if app_key and access_key:
        uid = str(config.get("volc_speech_uid") or "").strip() or app_key
        return VolcSpeechCredentials(uid=uid, app_key=app_key, access_key=access_key)
    return None


def resolve_volc_speech_credentials(
    *,
    config: Mapping[str, Any] | None = None,
) -> VolcSpeechCredentials | None:
    """仅从 ``config`` 解析；不读环境变量。"""
    return resolve_volc_speech_credentials_from_mapping(config)


def bigmodel_flash_recognize_file(
    audio_path: Path,
    creds: VolcSpeechCredentials,
    *,
    timeout_seconds: float = 300.0,
) -> tuple[dict[str, Any], dict[str, str]]:
    """
    上传本地音频（Base64）。返回 (response_json, response_headers_lowercase)。
    成功时响应头含 X-Api-Status-Code: 20000000。
    """
    raw = audio_path.read_bytes()
    body: dict[str, Any] = {
        "user": {"uid": creds.uid},
        "audio": {"data": base64.b64encode(raw).decode("ascii")},
        "request": {"model_name": "bigmodel"},
    }
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = creds.build_headers()
    headers["Content-Type"] = "application/json; charset=utf-8"
    req = Request(
        VOLC_BIGMODEL_FLASH_RECOGNIZE_URL,
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            out_headers = {k.lower(): v for k, v in resp.headers.items()}
            try:
                parsed: dict[str, Any] = json.loads(text) if text else {}
            except json.JSONDecodeError:
                parsed = {"_raw": text}
            return parsed, out_headers
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"volc speech HTTP {exc.code}: {detail or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"volc speech network error: {exc}") from exc


def recognize_flash_text(
    audio_path: Path,
    *,
    config: Mapping[str, Any] | None = None,
    timeout_seconds: float = 300.0,
) -> str:
    """从配置解析密钥并识别，返回 result.text；失败抛错。"""
    creds = resolve_volc_speech_credentials(config=config)
    if not creds:
        raise RuntimeError(
            "volc speech: set volc_speech_api_key, or "
            "volc_speech_app_key + volc_speech_access_key in config"
        )
    data, headers = bigmodel_flash_recognize_file(
        audio_path,
        creds,
        timeout_seconds=timeout_seconds,
    )
    code = str(headers.get("x-api-status-code") or "")
    if code and code != "20000000":
        msg = headers.get("x-api-message") or ""
        raise RuntimeError(f"volc speech status {code}: {msg} body={data!r}")
    result = data.get("result")
    if isinstance(result, dict):
        text = str(result.get("text") or "").strip()
        return text
    return ""
