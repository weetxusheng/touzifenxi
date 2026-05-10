"""单个 LLM batch 的请求、重试、截断拆分和 checkpoint 处理。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ..llm.client import OpenAIResponsesClient, ProviderRequestError
from ..llm.parser import ResponseParseError, coerce_json_object_payload, parse_response_payload
from ..llm.repair import build_repair_record, repair_response_payload
from ..runtime.call_governance import CallTimeline, classify_exception_status
from ..runtime.call_policy import CallPolicy
from ..runtime.checkpoint import PairCheckpointStore, atomic_write_json
from ..runtime.config import FileComparisonRuntimeConfig
from ..runtime.fallbacks import run_batch_fallback
from ..runtime.recovery import decide_batch_recovery
from .chunking import split_compare_block_by_items
from .models import ChapterBatch, ComparisonRow, PairMatch
from .postprocess import normalize_block_operations_payload, rows_from_llm_payload
from .response_readable import write_readable_response_file as _write_readable_response_file

ARK_REQUEST_SHAPE_ERROR_RE = re.compile(
    r"unknown field|unknown parameter|unsupported field|unsupported parameter|unrecognized field|unrecognized parameter",
    re.I,
)

def _process_llm_batch(
    *,
    pair: PairMatch,
    batch: ChapterBatch,
    batch_index: int,
    llm_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None,
    client: OpenAIResponsesClient,
    provider_chain,
    item_lookup_blocks: tuple[Any, ...] | None = None,
) -> tuple[str, list[ComparisonRow]]:
    """执行单个章节批次的模型比较、重试和规则回退。"""
    lookup_compare_blocks = item_lookup_blocks if item_lookup_blocks is not None else batch.compare_blocks
    batch_dir = llm_dir / batch.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    timeline = CallTimeline(batch_dir, batch_id=batch.batch_id)
    atomic_write_json(
        batch_dir / "batch_input.json",
        {
            "batch_id": batch.batch_id,
            "chapter_numbers": list(batch.chapter_numbers),
            "compare_blocks": [
                {
                    "block_id": block.block_id,
                    "chapter_number": block.chapter_number,
                    "chapter_title": block.chapter_title,
                    "parent_path": block.parent_path,
                    "old_items": [{"item_id": item.item_id, "text": item.text} for item in block.old_items],
                    "new_items": [{"item_id": item.item_id, "text": item.text} for item in block.new_items],
                }
                for block in batch.compare_blocks
            ],
            "old_sections": [
                {"number": section.number, "title": section.title, "body": section.body}
                for section in batch.old_sections
            ],
            "new_sections": [
                {"number": section.number, "title": section.title, "body": section.body}
                for section in batch.new_sections
            ],
        },
    )
    timeline.record(
        "prepared",
        details={
            "chapter_numbers": list(batch.chapter_numbers),
            "compare_block_count": len(batch.compare_blocks),
        },
    )
    routed_provider_chain = _provider_chain_for_batch(
        provider_chain=provider_chain,
        batch_index=batch_index,
        runtime_config=runtime_config,
    )
    policy = CallPolicy.from_runtime(runtime_config, tuple(routed_provider_chain))
    rows: list[ComparisonRow] = []
    last_error = ""
    attempt_index = 0
    plain_json_providers: set[str] = set()
    while True:
        attempt_index += 1
        provider_config = policy.provider_for_attempt(attempt_index)
        prompt_mode = policy.prompt_mode_for_attempt(attempt_index)
        if provider_config.provider in plain_json_providers:
            prompt_mode = "plain_json"
        base_request_payload = client.build_request_payload(
            pair_id=pair.pair_id,
            batch=batch,
            provider_config=provider_config,
        )
        request_payload = _apply_prompt_mode(base_request_payload, prompt_mode=prompt_mode)
        request_context = _request_context(pair, batch, runtime_config, provider_config, attempt_index)
        request_context["prompt_mode"] = prompt_mode
        client.write_request(
            batch_dir / f"request.attempt-{attempt_index:02d}.{provider_config.provider}.json",
            request_payload,
        )
        timeline.record(
            "requesting",
            provider=provider_config.provider,
            attempt_index=attempt_index,
            details=request_context,
        )
        attempt_started_at = time.perf_counter()
        try:
            raw_response, normalized_response = _post_with_raw(client, request_payload, provider_config)
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            _write_json_file(batch_dir / f"response.raw.attempt-{attempt_index:02d}.{provider_config.provider}.json", raw_response)
            _write_json_file(batch_dir / f"response.normalized.attempt-{attempt_index:02d}.{provider_config.provider}.json", normalized_response)
            _write_readable_response_file(
                batch_dir / f"response.readable.attempt-{attempt_index:02d}.{provider_config.provider}.json",
                normalized_response,
            )
            timeline.record(
                "responded",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
            )
            timeline.record(
                "normalized",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
            )
            truncation_reason = _response_truncation_reason(raw_response, normalized_response)
            if truncation_reason:
                child_batches = _split_batch_after_truncation(batch)
                if child_batches:
                    timeline.record(
                        "parse_error",
                        provider=provider_config.provider,
                        attempt_index=attempt_index,
                        duration_ms=attempt_duration_ms,
                        error=f"响应被长度限制截断: {truncation_reason}",
                        retry_class="parse",
                        details={
                            "response_truncated": True,
                            "split_batch_ids": [child_batch.batch_id for child_batch in child_batches],
                        },
                    )
                    split_root_dir = batch_dir / "splits"
                    split_root_dir.mkdir(parents=True, exist_ok=True)
                    split_rows: list[ComparisonRow] = []
                    for child_index, child_batch in enumerate(child_batches):
                        _child_batch_id, child_rows = _process_llm_batch(
                            pair=pair,
                            batch=child_batch,
                            batch_index=batch_index + child_index,
                            llm_dir=split_root_dir,
                            runtime_config=runtime_config,
                            pair_store=None,
                            client=client,
                            provider_chain=provider_chain,
                            item_lookup_blocks=item_lookup_blocks,
                        )
                        split_rows.extend(child_rows)
                    combined_payload = _combine_split_batch_payloads(
                        split_root_dir=split_root_dir,
                        child_batches=child_batches,
                    )
                    combined_payload = normalize_block_operations_payload(
                        combined_payload, compare_blocks=lookup_compare_blocks
                    )
                    parsed_filename = f"parsed.attempt-{attempt_index:02d}.{provider_config.provider}.split.json"
                    _write_json_file(batch_dir / parsed_filename, combined_payload)
                    _record_checkpoint(
                        pair_store,
                        batch_id=batch.batch_id,
                        status="success",
                        provider=provider_config.provider,
                        request_context=request_context,
                        duration_ms=attempt_duration_ms,
                        provider_available=True,
                        result={
                            "row_count": len(split_rows),
                            "split_from_truncation": True,
                            "child_batch_count": len(child_batches),
                        },
                        call_status="succeeded",
                        resume_from="reuse_parsed",
                    )
                    timeline.record(
                        "succeeded",
                        provider=provider_config.provider,
                        attempt_index=attempt_index,
                        duration_ms=attempt_duration_ms,
                        details={
                            "row_count": len(split_rows),
                            "split_from_truncation": True,
                            "child_batch_count": len(child_batches),
                        },
                    )
                    timeline.finalize(
                        "succeeded",
                        attempt_count=attempt_index,
                        provider=provider_config.provider,
                        recoverable=False,
                        next_resume_step="reuse_parsed",
                        parsed_file=parsed_filename,
                    )
                    return batch.batch_id, split_rows
            parsed = _parse_or_repair_payload(
                normalized_response=normalized_response,
                batch_dir=batch_dir,
                provider_name=provider_config.provider,
                attempt_index=attempt_index,
                default_chapter=_batch_title(batch),
                timeline=timeline,
            )
            parsed = normalize_block_operations_payload(parsed, compare_blocks=lookup_compare_blocks)
            parsed_filename = f"parsed.attempt-{attempt_index:02d}.{provider_config.provider}.json"
            _write_json_file(batch_dir / parsed_filename, parsed)
            batch_rows = rows_from_llm_payload(parsed, compare_blocks=lookup_compare_blocks)
            rows.extend(batch_rows)
            _record_checkpoint(
                pair_store,
                batch_id=batch.batch_id,
                status="success",
                provider=provider_config.provider,
                request_context=request_context,
                duration_ms=attempt_duration_ms,
                provider_available=True,
                result={"row_count": len(batch_rows)},
                call_status="succeeded",
                repair_used=(batch_dir / "repair.json").exists(),
                resume_from="reuse_parsed",
            )
            timeline.record(
                "succeeded",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
                details={"row_count": len(batch_rows)},
            )
            timeline.finalize(
                "succeeded",
                attempt_count=attempt_index,
                provider=provider_config.provider,
                recoverable=False,
                next_resume_step="reuse_parsed",
                parsed_file=parsed_filename,
            )
            return batch.batch_id, rows
        except (json.JSONDecodeError, ResponseParseError) as exc:
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"{type(exc).__name__}: {exc}"
            policy.record_failure("parse")
            _record_provider_unavailable(client, provider_config, reason="parse_error")
            timeline.record(
                "parse_error",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
                error=last_error,
                retry_class="parse",
            )
            _record_checkpoint(
                pair_store,
                batch_id=batch.batch_id,
                status="parse_error",
                provider=provider_config.provider,
                request_context=request_context,
                duration_ms=attempt_duration_ms,
                provider_available=False,
                error=last_error,
                call_status="parse_error",
            )
        except ValueError as exc:
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"ValueError: {exc}"
            policy.record_failure("postprocess")
            _record_provider_unavailable(client, provider_config, reason="postprocess_error")
            timeline.record(
                "postprocess_error",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
                error=last_error,
                retry_class="postprocess",
            )
            _record_checkpoint(
                pair_store,
                batch_id=batch.batch_id,
                status="postprocess_error",
                provider=provider_config.provider,
                request_context=request_context,
                duration_ms=attempt_duration_ms,
                provider_available=False,
                error=last_error,
                call_status="postprocess_error",
            )
        except Exception as exc:  # noqa: BLE001
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"{type(exc).__name__}: {exc}"
            if should_downgrade_to_plain_json(provider_config.provider, exc):
                plain_json_providers.add(provider_config.provider)
            call_status, retry_class = classify_exception_status(exc)
            policy.record_failure(retry_class)
            _record_provider_unavailable(client, provider_config, reason=call_status)
            timeline.record(
                call_status,
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
                error=last_error,
                retry_class=retry_class,
            )
            _record_checkpoint(
                pair_store,
                batch_id=batch.batch_id,
                status="error",
                provider=provider_config.provider,
                request_context=request_context,
                duration_ms=attempt_duration_ms,
                provider_available=False,
                error=last_error,
                call_status=call_status,
            )
        if policy.should_fallback(attempt_index):
            break
    fallback_result = run_batch_fallback(batch)
    _write_fallback_diagnostic(batch_dir, fallback_result)
    if fallback_result.success:
        _record_checkpoint(
            pair_store,
            batch_id=batch.batch_id,
            status="error",
            provider="fallback-rule",
            request_context={
                "pair_id": pair.pair_id,
                "chapter_range": list(batch.chapter_numbers),
                "old_section_titles": [section.title for section in batch.old_sections]
                or [block.chapter_title for block in batch.compare_blocks],
                "new_section_titles": [section.title for section in batch.new_sections]
                or [block.chapter_title for block in batch.compare_blocks],
                "compare_block_ids": [block.block_id for block in batch.compare_blocks],
                "compare_block_count": len(batch.compare_blocks),
                "provider": "fallback-rule",
                "model": runtime_config.llm.model,
                "batch_size": runtime_config.llm.chapter_batch_size,
                "attempt_index": attempt_index,
            },
            duration_ms=0,
            provider_available=False,
            result={"row_count": len(fallback_result.rows), "fallback": True},
            error="本地 fallback 仅用于诊断，不能作为正式对照内容",
            call_status="failed",
            fallback_name=fallback_result.name,
            resume_from="continue",
        )
        timeline.record(
            "failed",
            provider="fallback-rule",
            attempt_index=attempt_index,
            error="本地 fallback 仅用于诊断，不能作为正式对照内容",
            details={
                "row_count": len(fallback_result.rows),
                "fallback": fallback_result.name,
                "diagnostic_path": "fallback.diagnostic.json",
            },
        )
        timeline.finalize(
            "failed",
            attempt_count=attempt_index,
            provider="fallback-rule",
            fallback=fallback_result.name,
            error=(last_error + "\n" if last_error else "") + "本地 fallback 仅用于诊断，不能作为正式对照内容",
            recoverable=True,
            next_resume_step="continue",
        )
        raise RuntimeError("本地 fallback 仅用于诊断，不能作为正式对照内容")
    timeline.record("failed", attempt_index=attempt_index, error=last_error)
    timeline.finalize(
        "failed",
        attempt_count=attempt_index,
        error=last_error,
        recoverable=True,
        next_resume_step="continue",
    )
    raise RuntimeError(last_error or "batch 未获得可用结果")


def _write_fallback_diagnostic(batch_dir: Path, fallback_result) -> None:
    """把本地 fallback 结果写成诊断文件，但不允许进入正式 rows。"""
    _write_json_file(
        batch_dir / "fallback.diagnostic.json",
        {
            "fallback": fallback_result.name,
            "success": fallback_result.success,
            "row_count": len(fallback_result.rows),
            "error": fallback_result.error,
            "formal_result_allowed": False,
            "rows": [
                {
                    "chapter": row.chapter,
                    "subchapter": row.subchapter,
                    "old_text": row.old_text,
                    "new_text": row.new_text,
                }
                for row in fallback_result.rows
            ],
        },
    )


def _provider_chain_for_batch(*, provider_chain, batch_index: int, runtime_config: FileComparisonRuntimeConfig):
    """按批次序号和路由配置生成本批首发 provider 顺序。"""
    if not runtime_config.llm.task_routing.enabled or not runtime_config.llm.task_routing.batch_compare:
        return tuple(provider_chain)
    providers_by_name = {provider.provider: provider for provider in provider_chain}
    configured_names = [name for name in runtime_config.llm.task_routing.batch_compare if name in providers_by_name]
    if not configured_names:
        return tuple(provider_chain)
    start_index = batch_index % len(configured_names)
    rotated_names = configured_names[start_index:] + configured_names[:start_index]
    ordered_chain = [providers_by_name[name] for name in rotated_names]
    ordered_names = set(rotated_names)
    ordered_chain.extend(provider for provider in provider_chain if provider.provider not in ordered_names)
    return tuple(ordered_chain)


def _batch_title(batch: ChapterBatch) -> str:
    """返回 batch 中最适合作为修复默认章节名的标题。"""
    if batch.compare_blocks:
        return batch.compare_blocks[0].chapter_title
    if batch.compare_units:
        return batch.compare_units[0].chapter_title
    if batch.old_sections:
        return batch.old_sections[0].title
    if batch.new_sections:
        return batch.new_sections[0].title
    return ""


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    """用统一格式写 JSON 文件。"""
    atomic_write_json(path, payload)


def _request_context(pair: PairMatch, batch: ChapterBatch, runtime_config: FileComparisonRuntimeConfig, provider_config, attempt_index: int) -> dict[str, Any]:
    """构造 checkpoint、timeline 和日志共享的请求上下文。"""
    return {
        "pair_id": pair.pair_id,
        "chapter_range": list(batch.chapter_numbers),
        "old_section_titles": [section.title for section in batch.old_sections]
        or [block.chapter_title for block in batch.compare_blocks]
        or [unit.chapter_title for unit in batch.compare_units],
        "new_section_titles": [section.title for section in batch.new_sections]
        or [block.chapter_title for block in batch.compare_blocks]
        or [unit.chapter_title for unit in batch.compare_units],
        "compare_block_ids": [block.block_id for block in batch.compare_blocks],
        "compare_block_count": len(batch.compare_blocks),
        "provider": provider_config.provider,
        "model": provider_config.model,
        "batch_size": runtime_config.llm.chapter_batch_size,
        "attempt_index": attempt_index,
    }


def _apply_prompt_mode(payload: dict[str, Any], *, prompt_mode: str) -> dict[str, Any]:
    """根据治理层 prompt 模式调整请求文本，不向 provider 发送调试字段。"""
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    if prompt_mode == "plain_json":
        instructions = str(payload.pop("instructions", "")).strip()
        input_payload = str(payload.get("input", "")).strip()
        schema_payload = payload.pop("text", None)
        schema_hint = json.dumps(schema_payload, ensure_ascii=False) if schema_payload else ""
        prompt_parts = [
            instructions,
            "请严格输出一个 JSON 对象，不要输出 Markdown，不要解释。",
            f"JSON 输出格式要求：{schema_hint}" if schema_hint else "",
            "输入数据如下：",
            input_payload,
        ]
        payload["input"] = "\n\n".join(part for part in prompt_parts if part)
        return payload
    if prompt_mode != "minimal":
        return payload
    if isinstance(payload.get("messages"), list) and payload["messages"]:
        payload["messages"][-1]["content"] = (
            str(payload["messages"][-1].get("content", ""))
            + "\n\n## 最小 JSON 输出要求\n"
            "- 只返回 add/delete/replace；完全一致或仅编号顺延不要返回。\n"
            "- 输出前逐项核对每个 compare block 的所有 old_items/new_items。\n"
            "- 未匹配的 old_item 必须 delete，未匹配的 new_item 必须 add。\n"
            "- 定义项按冒号前名称优先对齐。\n"
            "- 只保留 blocks、block_id、chapter、parent_path、operations、type、old_item_ids、new_item_ids、old_focus_text、new_focus_text、confidence、reason。"
        )
    elif isinstance(payload.get("input"), str):
        payload["input"] = (
            payload["input"]
            + "\n\n## 最小 JSON 输出要求\n"
            "- 只返回 add/delete/replace；完全一致或仅编号顺延不要返回。\n"
            "- 输出前逐项核对所有 old_items/new_items。\n"
            "- 未匹配 old_item 必须 delete，未匹配 new_item 必须 add。"
        )
    return payload


def should_downgrade_to_plain_json(provider_name: str, exc: Exception) -> bool:
    """判断 provider 是否因为请求字段兼容问题需要降级为普通 JSON prompt。"""
    if provider_name.strip().lower() != "deepseek-ark":
        return False
    if not isinstance(exc, ProviderRequestError):
        return False
    message = str(exc)
    return bool(ARK_REQUEST_SHAPE_ERROR_RE.search(message))


def _post_with_raw(client: OpenAIResponsesClient, request_payload: dict[str, Any], provider_config) -> tuple[dict[str, Any], dict[str, Any]]:
    """兼容新旧 client，返回 raw 与 normalized 两份响应。"""
    if hasattr(client, "post_with_raw"):
        return client.post_with_raw(request_payload, provider_config=provider_config)
    normalized = client.post(request_payload, provider_config=provider_config)
    return normalized, normalized


def _response_truncation_reason(raw_response: dict[str, Any], normalized_response: dict[str, Any]) -> str:
    """识别 provider 是否因为输出长度限制截断了响应。"""
    for payload in (raw_response, normalized_response):
        if not isinstance(payload, dict):
            continue
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0] if isinstance(choices[0], dict) else {}
            finish_reason = str(first_choice.get("finish_reason", "")).strip().lower()
            if finish_reason == "length":
                return "finish_reason:length"
        finish_reason = str(payload.get("finish_reason", "")).strip().lower()
        if finish_reason == "length":
            return "finish_reason:length"
        status = str(payload.get("status", "")).strip().lower()
        if status == "incomplete":
            incomplete_details = payload.get("incomplete_details")
            if isinstance(incomplete_details, dict):
                reason = str(incomplete_details.get("reason", "")).strip()
                return f"incomplete:{reason}" if reason else "status:incomplete"
            return "status:incomplete"
    return ""


def _chapter_numbers_for_blocks(blocks: list[Any]) -> tuple[str, ...]:
    """按出现顺序返回 blocks 覆盖的章节号。"""
    return tuple(dict.fromkeys(str(block.chapter_number) for block in blocks))


def _filter_batch_sections(sections: tuple[Any, ...], chapter_numbers: tuple[str, ...]) -> tuple[Any, ...]:
    """按章节号筛出子 batch 仍需要的 section。"""
    if not sections:
        return ()
    chapter_number_set = set(chapter_numbers)
    return tuple(section for section in sections if getattr(section, "number", "") in chapter_number_set)


def _split_batch_after_truncation(batch: ChapterBatch) -> list[ChapterBatch]:
    """仅对当前被截断的 batch 做局部细拆。"""
    blocks = list(batch.compare_blocks)
    if len(blocks) > 1:
        midpoint = max(1, len(blocks) // 2)
        chunks = [blocks[:midpoint], blocks[midpoint:]]
    elif blocks:
        block = blocks[0]
        block_chars = max(1, sum(len(item.text) for item in block.old_items) + sum(len(item.text) for item in block.new_items))
        split_limit = max(800, block_chars // 2)
        split_blocks = split_compare_block_by_items(block, max_compare_block_chars=split_limit)
        if len(split_blocks) <= 1:
            return []
        chunks = [[child_block] for child_block in split_blocks]
    else:
        return []
    refined: list[ChapterBatch] = []
    for index, chunk in enumerate(chunks, start=1):
        if not chunk:
            continue
        chapter_numbers = _chapter_numbers_for_blocks(chunk)
        refined.append(
            ChapterBatch(
                batch_id=f"{batch.batch_id}-split-{index:03d}",
                chapter_numbers=chapter_numbers,
                old_sections=_filter_batch_sections(batch.old_sections, chapter_numbers),
                new_sections=_filter_batch_sections(batch.new_sections, chapter_numbers),
                compare_blocks=tuple(chunk),
            )
        )
    return refined


def _combine_split_batch_payloads(*, split_root_dir: Path, child_batches: list[ChapterBatch]) -> dict[str, Any]:
    """把局部细拆后的子 batch 结构化结果合并回父 batch。"""
    merged_blocks: list[dict[str, Any]] = []
    for child_batch in child_batches:
        child_batch_dir = split_root_dir / child_batch.batch_id
        recovery = decide_batch_recovery(child_batch_dir)
        payload = recovery.payload if isinstance(recovery.payload, dict) else {}
        if isinstance(payload.get("blocks"), list):
            merged_blocks.extend(payload["blocks"])
    return {"blocks": merged_blocks}


def _record_provider_unavailable(client: OpenAIResponsesClient, provider_config, *, reason: str) -> None:
    """通知 client 某 provider 返回不可用，用于触发 provider 级失败冷却。"""
    if hasattr(client, "record_provider_unavailable"):
        client.record_provider_unavailable(provider_config, reason=reason)


def _parse_or_repair_payload(
    *,
    normalized_response: dict[str, Any],
    batch_dir: Path,
    provider_name: str,
    attempt_index: int,
    default_chapter: str,
    timeline: CallTimeline,
) -> dict[str, Any]:
    """解析模型响应；失败时尝试结构修复并落 repair 文件。"""
    try:
        return parse_response_payload(normalized_response)
    except ResponseParseError as parse_error:
        try:
            output_text = normalized_response.get("output_text", "")
            raw_payload = coerce_json_object_payload(output_text) if isinstance(output_text, str) else normalized_response
            repaired = repair_response_payload(raw_payload, default_chapter=default_chapter)
        except Exception as repair_error:  # noqa: BLE001
            repair_record = build_repair_record(
                repair_type="none",
                before_payload=normalized_response,
                success=False,
                error=f"{type(repair_error).__name__}: {repair_error}",
            )
            _write_json_file(batch_dir / f"repair.attempt-{attempt_index:02d}.{provider_name}.json", repair_record)
            _write_json_file(batch_dir / "repair.json", repair_record)
            raise parse_error from repair_error
        repair_record = build_repair_record(
            repair_type="wrap_subsections",
            before_payload=raw_payload,
            success=True,
            payload=repaired,
        )
        _write_json_file(batch_dir / f"repair.attempt-{attempt_index:02d}.{provider_name}.json", repair_record)
        _write_json_file(batch_dir / "repair.json", repair_record)
        timeline.record(
            "repaired",
            provider=provider_name,
            attempt_index=attempt_index,
            details={"repair_type": repair_record["repair_type"]},
        )
        return repaired


def _record_checkpoint(
    pair_store: PairCheckpointStore | None,
    *,
    batch_id: str,
    status: str,
    provider: str,
    request_context: dict[str, Any],
    duration_ms: int,
    provider_available: bool,
    result: dict[str, Any] | None = None,
    error: str = "",
    call_status: str = "",
    repair_used: bool = False,
    fallback_name: str = "",
    resume_from: str = "",
) -> None:
    """向 pair checkpoint 写入一次尝试，并附加治理字段。"""
    if pair_store is None:
        return
    enriched_context = dict(request_context)
    if call_status:
        enriched_context["call_status"] = call_status
    if resume_from:
        enriched_context["resume_from"] = resume_from
    enriched_result = dict(result or {})
    if repair_used:
        enriched_result["repair_used"] = True
    if fallback_name:
        enriched_result["fallback_name"] = fallback_name
    pair_store.record_entry(
        entry_id=batch_id,
        status=status,
        result=enriched_result or None,
        error={"message": error} if error else None,
        provider=provider,
        request_context=enriched_context,
        duration_ms=duration_ms,
        provider_available=provider_available,
        call_status=call_status,
        repair_used=repair_used,
        fallback_name=fallback_name,
        resume_from=resume_from,
    )
