"""封装 file-comparison skill 的多 provider 结构化模型客户端。"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..compare.models import ChapterBatch
from ..runtime.config import FileComparisonRuntimeConfig, LLMProviderConfig, resolve_provider_api_key
from .parallel import compute_backoff_delay, create_provider_semaphores
from .providers import (
    build_compare_request_payload,
    classify_http_error,
    normalize_provider_response,
    provider_endpoint,
    provider_headers,
)
from .retry import RetryClassifier, RetryClassifierConfig, classify_retry_class
from .schema import FILE_COMPARISON_SCHEMA


class ProviderRequestError(RuntimeError):
    """表示一次 provider 请求失败及其重试分类。"""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        infrastructure_error: bool,
        retry_class: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        """保存 provider 名称、错误级别和可能的 Retry-After 信息。"""
        super().__init__(message)
        self.provider = provider
        self.infrastructure_error = infrastructure_error
        self.retry_class = retry_class
        self.retry_after_seconds = retry_after_seconds


class OpenAIResponsesClient:
    """负责构造请求、执行 provider 内重试并返回归一化响应。"""

    def __init__(self, runtime_config: FileComparisonRuntimeConfig) -> None:
        """根据运行配置初始化 provider 链、限流器和退避分类器。"""
        self.runtime_config = runtime_config
        self.providers = runtime_config.llm.providers
        self.current_provider_name = self.providers[0].provider
        self._retry_classifier = RetryClassifier(
            RetryClassifierConfig(
                infra_max_attempts=runtime_config.llm.infra_max_attempts,
                parse_max_attempts=runtime_config.llm.parse_max_attempts,
                postprocess_max_attempts=runtime_config.llm.postprocess_max_attempts,
                fatal_max_attempts=runtime_config.llm.fatal_max_attempts,
            )
        )
        self._provider_semaphores = create_provider_semaphores(self.providers)
        self._rate_limit_lock = threading.Lock()
        self._last_request_started_at_by_provider = {provider.provider: 0.0 for provider in self.providers}
        self._next_allowed_at_by_provider = {provider.provider: 0.0 for provider in self.providers}

    def build_request_payload(
        self,
        *,
        pair_id: str,
        batch: ChapterBatch,
        provider_config: LLMProviderConfig | None = None,
    ) -> dict[str, Any]:
        """为某个章节批次构造结构化比较请求体。"""
        selected_provider = provider_config or self.providers[0]
        old_sections = [
            {"number": section.number, "title": section.title, "body": section.body}
            for section in batch.old_sections
        ]
        new_sections = [
            {"number": section.number, "title": section.title, "body": section.body}
            for section in batch.new_sections
        ]
        compare_blocks = [
            {
                "block_id": block.block_id,
                "chapter_number": block.chapter_number,
                "chapter_title": block.chapter_title,
                "parent_path": block.parent_path,
                "old_items": [{"item_id": item.item_id, "text": item.text} for item in block.old_items],
                "new_items": [{"item_id": item.item_id, "text": item.text} for item in block.new_items],
            }
            for block in getattr(batch, "compare_blocks", ())
        ]
        return build_compare_request_payload(
            selected_provider.provider,
            model=selected_provider.model,
            schema_name=FILE_COMPARISON_SCHEMA["name"],
            schema=FILE_COMPARISON_SCHEMA["schema"],
            strict=FILE_COMPARISON_SCHEMA["strict"],
            instructions=(
                "你是文件修订对照助手。请只返回 JSON，不要输出 Markdown 或解释。\n\n"
                "## 任务\n"
                "- 输入包含 compare_blocks；每个 compare block 内有 old_items 和 new_items。\n"
                "- 你负责在同一个 compare block 内完成条目映射，只输出有实质文本变化的操作。\n"
                "- 不要直接生成最终对照表正文；程序会按 block_id + item_id 回查原文。\n\n"
                "## 工作步骤\n"
                "1. 对每个 compare block 建立覆盖清单，逐项扫描所有 old_items 和 new_items。\n"
                "2. 先找完全一致或仅编号顺延的条目，这些条目不要输出。\n"
                "3. 再按语义和定义名称匹配剩余条目，判断 replace、delete、add。\n"
                "4. 输出前必须自检：所有非完全一致、非仅编号顺延的 old_item/new_item，"
                "都必须出现在某个 operation 的 old_item_ids 或 new_item_ids 中。\n\n"
                "## 匹配规则\n"
                "- 不要假设相同编号就是同一条，也不要因为编号相邻就强行匹配。\n"
                "- 如果插入或删除一条导致后续编号顺延，只输出真实新增、删除或实质替换的条目；不要输出后续纯顺延条目。\n"
                "- 定义项必须优先按冒号或中文冒号前的定义名称对齐。例如“44、认购：...”的定义名称是“认购”。\n"
                "- 同名定义项即使编号变化也优先匹配。\n"
                "- 不同定义名称默认不要 replace；只有能判断为同一业务概念改名时，才允许 replace，否则按 delete + add 处理。\n\n"
                "## 通用判断示例\n"
                "- 旧侧“1、定义A：旧说明”在新侧没有同名或同义定义时，返回 delete；不能因为附近有相关但不同名的定义就跳过。\n"
                "- 新侧“2、定义B：新说明”在旧侧没有同名或同义定义时，返回 add。\n"
                "- 旧侧“3、定义C：旧说明”和新侧“3、定义C：新说明”名称相同但说明变化时，返回 replace。\n"
                "- 旧侧“4、定义D：说明”和新侧“5、定义D：说明”名称相同、正文相同、只有编号变化时，不要输出。\n"
                "- 旧侧“5、定义E：...”和新侧“5、定义F：...”名称不同，即使位置相同或正文有相似词，也默认拆成 delete + add；只有能证明是同一业务概念改名时才返回 replace。\n"
                "- 新侧插入“6、条款G”导致后续条款编号顺延时，只输出“条款G”的 add；后续正文未变的顺延条目不要输出。\n\n"
                "## 覆盖规则\n"
                "- 每个 compare block 必须做覆盖检查。\n"
                "- 排除完全一致或仅编号顺延的匹配项后，仍未匹配的 old_item 必须返回 delete。\n"
                "- 排除完全一致或仅编号顺延的匹配项后，仍未匹配的 new_item 必须返回 add。\n"
                "- 不允许省略单侧独有条目。\n\n"
                "## 输出规则\n"
                "- 顶层只返回 blocks。\n"
                "- operation.type 只使用 add、delete、replace。\n"
                "- old_focus_text/new_focus_text 只作为变化锚点和排查线索，不会作为最终展示文本；不确定可留空。\n\n"
                "## 返回结构示例\n"
                "{\n"
                '  "blocks": [\n'
                "    {\n"
                '      "block_id": "输入中的 block_id",\n'
                '      "chapter": "章节标题",\n'
                '      "parent_path": "父标题路径",\n'
                '      "operations": [\n'
                "        {\n"
                '          "type": "replace",\n'
                '          "old_item_ids": ["old_item_id"],\n'
                '          "new_item_ids": ["new_item_id"],\n'
                '          "old_focus_text": "旧侧变化锚点",\n'
                '          "new_focus_text": "新侧变化锚点",\n'
                '          "confidence": 0.95,\n'
                '          "reason": "简要说明判断依据"\n'
                "        },\n"
                "        {\n"
                '          "type": "delete",\n'
                '          "old_item_ids": ["old_item_id"],\n'
                '          "new_item_ids": [],\n'
                '          "old_focus_text": "旧侧删除锚点",\n'
                '          "new_focus_text": "",\n'
                '          "confidence": 0.95,\n'
                '          "reason": "新侧无对应条目"\n'
                "        },\n"
                "        {\n"
                '          "type": "add",\n'
                '          "old_item_ids": [],\n'
                '          "new_item_ids": ["new_item_id"],\n'
                '          "old_focus_text": "",\n'
                '          "new_focus_text": "新侧新增锚点",\n'
                '          "confidence": 0.95,\n'
                '          "reason": "旧侧无对应条目"\n'
                "        }\n"
                "      ]\n"
                "    }\n"
                "  ]\n"
                "}"
            ),
            input_payload={
                "pair_id": pair_id,
                "batch_id": batch.batch_id,
                "chapter_numbers": list(batch.chapter_numbers),
                "old_sections": old_sections,
                "new_sections": new_sections,
                "compare_blocks": compare_blocks,
            },
        )

    def provider_chain_for_attempts(self) -> tuple[LLMProviderConfig, ...]:
        """返回当前客户端持有的 provider 链顺序。"""
        return self.providers

    def post(self, payload: dict[str, Any], *, provider_config: LLMProviderConfig | None = None) -> dict[str, Any]:
        """向指定 provider 发起请求，并返回归一化响应。"""
        _raw_body, normalized_body = self.post_with_raw(payload, provider_config=provider_config)
        return normalized_body

    def post_with_raw(self, payload: dict[str, Any], *, provider_config: LLMProviderConfig | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """向指定 provider 发起请求，并同时返回原始响应和归一化响应。"""
        selected_provider = provider_config or self.providers[0]
        api_key = resolve_provider_api_key(selected_provider)
        if not api_key:
            raise RuntimeError(
                f"missing API key in provider {selected_provider.provider} config.api_key "
                f"or environment variable {selected_provider.api_key_env}"
            )
        request = Request(
            url=provider_endpoint(selected_provider.provider, selected_provider.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers=provider_headers(selected_provider.provider, api_key),
            method="POST",
        )
        last_error: ProviderRequestError | None = None
        max_attempts = max(
            self._retry_classifier.max_attempts_for("infra", default=selected_provider.max_retries + 1),
            self._retry_classifier.max_attempts_for("fatal", default=1),
        )
        for attempt in range(max_attempts):
            self._respect_min_interval(selected_provider)
            try:
                with self._provider_semaphores[selected_provider.provider]:
                    with urlopen(request, timeout=selected_provider.timeout_seconds) as response:  # noqa: S310
                        raw_body = response.read().decode("utf-8")
                body = json.loads(raw_body)
                self.current_provider_name = selected_provider.provider
                return body, normalize_provider_response(selected_provider.provider, body)
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="ignore")
                infrastructure_error, message, retry_after_seconds = classify_http_error(selected_provider.provider, error, detail)
                last_error = ProviderRequestError(
                    selected_provider.provider,
                    message,
                    infrastructure_error=infrastructure_error,
                    retry_class=classify_retry_class(infrastructure_error=infrastructure_error),
                    retry_after_seconds=retry_after_seconds,
                )
                self.record_provider_unavailable(selected_provider, reason=last_error.retry_class)
                if not self._retry_classifier.should_retry(
                    last_error.retry_class,
                    attempt_index=attempt,
                    default=(selected_provider.max_retries + 1 if infrastructure_error else 1),
                ):
                    raise last_error from error
            except json.JSONDecodeError as error:
                last_error = ProviderRequestError(
                    selected_provider.provider,
                    f"{selected_provider.provider} 响应不是合法 JSON: {error}",
                    infrastructure_error=False,
                    retry_class="fatal",
                )
                self.record_provider_unavailable(selected_provider, reason="fatal")
                raise last_error from error
            except (URLError, socket.timeout, TimeoutError, ConnectionResetError) as error:
                last_error = ProviderRequestError(
                    selected_provider.provider,
                    f"{selected_provider.provider} 请求失败: {error}",
                    infrastructure_error=True,
                    retry_class="infra",
                )
                self.record_provider_unavailable(selected_provider, reason="infra")
                if not self._retry_classifier.should_retry(
                    "infra",
                    attempt_index=attempt,
                    default=selected_provider.max_retries + 1,
                ):
                    raise last_error from error
            delay_seconds = compute_backoff_delay(
                base_delay_seconds=selected_provider.retry_backoff_seconds,
                attempt_index=attempt,
                retry_after_seconds=(
                    last_error.retry_after_seconds
                    if selected_provider.honor_retry_after and last_error is not None
                    else None
                ),
                jitter_seconds=selected_provider.jitter_seconds,
            )
            time.sleep(delay_seconds)
        raise last_error or ProviderRequestError(
            selected_provider.provider,
            f"{selected_provider.provider} 请求失败。",
            infrastructure_error=True,
            retry_class="infra",
        )

    def write_request(self, path: Path, payload: dict[str, Any]) -> None:
        """把单次请求体以 JSON 形式落盘。"""
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_response(self, path: Path, payload: dict[str, Any]) -> None:
        """把单次响应体以 JSON 形式落盘。"""
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def record_provider_unavailable(self, provider_config: LLMProviderConfig, *, reason: str = "") -> None:
        """记录 provider 本次不可用，并设置下一次同 provider 请求的冷却时间。"""
        cooldown_seconds = provider_config.failure_cooldown_seconds
        if cooldown_seconds <= 0:
            return
        with self._rate_limit_lock:
            next_allowed_at = time.time() + cooldown_seconds
            current_next_allowed_at = self._next_allowed_at_by_provider.get(provider_config.provider, 0.0)
            self._next_allowed_at_by_provider[provider_config.provider] = max(current_next_allowed_at, next_allowed_at)

    def _respect_min_interval(self, provider_config: LLMProviderConfig) -> None:
        """按 provider 的最小发起间隔和失败冷却限制请求节奏。"""
        min_interval_seconds = provider_config.min_interval_seconds
        with self._rate_limit_lock:
            now = time.time()
            last_started_at = self._last_request_started_at_by_provider.get(provider_config.provider, 0.0)
            next_allowed_at = self._next_allowed_at_by_provider.get(provider_config.provider, 0.0)
            wait_until = max(last_started_at + min_interval_seconds, next_allowed_at)
            wait_seconds = max(0.0, wait_until - now)
            scheduled_started_at = now + wait_seconds
            self._last_request_started_at_by_provider[provider_config.provider] = scheduled_started_at
        if wait_seconds > 0:
            time.sleep(wait_seconds)
