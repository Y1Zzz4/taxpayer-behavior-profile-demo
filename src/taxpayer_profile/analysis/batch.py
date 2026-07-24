"""Bounded, resumable execution of model analysis batches."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

import httpx

from taxpayer_profile.analysis.cache import CachedModelResult, ModelExtractionCache
from taxpayer_profile.analysis.contracts import AnalysisClient, ModelExtraction
from taxpayer_profile.ingestion.fingerprint import source_record_fingerprint
from taxpayer_profile.ingestion.modes import InputMode
from taxpayer_profile.llm_client import (
    PROMPT_VERSION,
    REPEAT_PROMPT_VERSION,
    CallExtractionResult,
    RepeatIssueModelResult,
    build_call_payload,
)
from taxpayer_profile.normalization import NormalizedCallInput

ProgressCallback = Callable[[int, int, str], None]


def _model_cache_key(
    call: NormalizedCallInput, mode: InputMode, client: AnalysisClient
) -> tuple[str, str]:
    prompt_version = PROMPT_VERSION
    value = "|".join(
        (
            source_record_fingerprint(call),
            mode.value,
            client.model,
            prompt_version,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest(), prompt_version


def _extract_model_fields(
    call: NormalizedCallInput,
    mode: InputMode,
    client: AnalysisClient,
) -> ModelExtraction:
    payload = build_call_payload(
        transcript=call.transcript,
        business_content=call.business_content,
        answer_content=call.answer_content,
        core_question=call.core_question,
        topic_category=call.topic_category,
    )
    return client.analyze_call(payload)


def _is_model_pressure_error(error: Exception) -> bool:
    """Recognize retryable upstream pressure through wrapped exceptions."""

    current: BaseException | None = error
    while current is not None:
        if isinstance(current, httpx.TimeoutException):
            return True
        if isinstance(current, httpx.HTTPStatusError) and (
            current.response.status_code == 429
            or current.response.status_code >= 500
        ):
            return True
        current = current.__cause__
    return False


def prefetch_model_extractions(
    *,
    calls: list[tuple[NormalizedCallInput, InputMode]],
    existing_business_ids: set[str],
    client: AnalysisClient | None,
    workers: int,
    cache_path: Path | str | None,
    progress_callback: ProgressCallback | None,
) -> dict[str, ModelExtraction | Exception]:
    """Extract independent calls in parallel before transactional processing."""

    if client is None:
        return {}
    if not 1 <= workers <= 16:
        raise ValueError("模型并发数必须在 1—16 之间")
    pending: list[tuple[str, NormalizedCallInput, InputMode]] = []
    seen: set[str] = set()
    for call, mode in calls:
        business_id = call.business_id
        if (
            business_id is None
            or business_id in seen
            or business_id in existing_business_ids
            or call.phone is None
            or call.call_time is None
            or not (
                call.transcript
                or call.business_content
                or call.answer_content
                or call.core_question
                or call.topic_category
            )
        ):
            continue
        seen.add(business_id)
        pending.append((business_id, call, mode))
    if not pending:
        return {}

    cache = ModelExtractionCache(cache_path) if cache_path is not None else None
    results: dict[str, ModelExtraction | Exception] = {}
    futures: dict[Future[ModelExtraction], tuple[str, str, str]] = {}
    uncached: list[tuple[str, NormalizedCallInput, InputMode, str, str]] = []
    completed = 0
    total = len(pending)
    try:
        for business_id, call, mode in pending:
            cache_key, prompt_version = _model_cache_key(call, mode, client)
            cached = cache.get(cache_key, CallExtractionResult) if cache else None
            if cached is not None:
                results[business_id] = cached
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total, "模型缓存命中")
                continue
            uncached.append(
                (business_id, call, mode, cache_key, prompt_version)
            )

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="model") as pool:
            queued = iter(uncached)

            def submit_next() -> bool:
                try:
                    business_id, call, mode, cache_key, prompt_version = next(queued)
                except StopIteration:
                    return False
                future = pool.submit(_extract_model_fields, call, mode, client)
                futures[future] = (business_id, cache_key, prompt_version)
                return True

            for _ in range(min(workers, len(uncached))):
                submit_next()
            consecutive_failures = 0
            active_limit = workers
            success_streak = 0
            while futures:
                done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    business_id, cache_key, prompt_version = futures.pop(future)
                    try:
                        result = future.result()
                        results[business_id] = result
                        if cache is not None:
                            cache.put(
                                cache_key,
                                result,
                                model_name=client.model,
                                prompt_version=prompt_version,
                            )
                        status = "模型提取完成"
                        consecutive_failures = 0
                        success_streak += 1
                        if success_streak >= 25 and active_limit < workers:
                            active_limit += 1
                            success_streak = 0
                    except Exception as exc:
                        results[business_id] = exc
                        status = "模型提取失败"
                        consecutive_failures += 1
                        success_streak = 0
                        if _is_model_pressure_error(exc) and active_limit > 1:
                            active_limit -= 1
                            status = f"模型压力过高，并发已降至{active_limit}"
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, status)
                    if consecutive_failures >= 3:
                        for queued_future in futures:
                            queued_future.cancel()
                        raise RuntimeError(
                            "连续 3 条模型提取失败，已熔断"
                            + ("；成功结果已写入断点缓存" if cache else "")
                        ) from results[business_id]
                while len(futures) < active_limit and submit_next():
                    pass
    finally:
        if cache is not None:
            cache.close()
    return results


def _repeat_cache_key(
    payload: dict[str, object], client: AnalysisClient
) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    value = "|".join((canonical, client.model, REPEAT_PROMPT_VERSION))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prefetch_repeat_reviews(
    *,
    payloads: dict[str, dict[str, object]],
    client: AnalysisClient | None,
    workers: int,
    cache_path: Path | str | None,
    progress_callback: ProgressCallback | None,
) -> dict[str, RepeatIssueModelResult | Exception]:
    """Review ambiguous repeated-issue candidates with bounded concurrency."""

    if client is None or not payloads:
        return {}
    cache = ModelExtractionCache(cache_path) if cache_path is not None else None
    results: dict[str, RepeatIssueModelResult | Exception] = {}
    pending: list[tuple[str, dict[str, object], str]] = []
    completed = 0
    total = len(payloads)
    try:
        for business_id, payload in payloads.items():
            cache_key = _repeat_cache_key(payload, client)
            cached = cache.get(cache_key, RepeatIssueModelResult) if cache else None
            if isinstance(cached, RepeatIssueModelResult):
                results[business_id] = cached
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total, "重复诉求缓存命中")
            else:
                pending.append((business_id, payload, cache_key))

        futures: dict[Future[RepeatIssueModelResult], tuple[str, str]] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="repeat") as pool:
            queued = iter(pending)

            def submit_next() -> bool:
                try:
                    business_id, payload, cache_key = next(queued)
                except StopIteration:
                    return False
                future = pool.submit(client.analyze_repeat_issue, payload)
                futures[future] = (business_id, cache_key)
                return True

            for _ in range(min(workers, len(pending))):
                submit_next()
            active_limit = workers
            success_streak = 0
            consecutive_failures = 0
            while futures:
                done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    business_id, cache_key = futures.pop(future)
                    try:
                        result = future.result()
                        results[business_id] = result
                        if cache is not None:
                            cache.put(
                                cache_key,
                                result,
                                model_name=client.model,
                                prompt_version=REPEAT_PROMPT_VERSION,
                            )
                        status = "重复诉求复核完成"
                        consecutive_failures = 0
                        success_streak += 1
                        if success_streak >= 25 and active_limit < workers:
                            active_limit += 1
                            success_streak = 0
                    except Exception as exc:
                        results[business_id] = exc
                        status = "重复诉求复核失败"
                        consecutive_failures += 1
                        success_streak = 0
                        if _is_model_pressure_error(exc) and active_limit > 1:
                            active_limit -= 1
                            status = f"模型压力过高，并发已降至{active_limit}"
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, status)
                    if consecutive_failures >= 3:
                        for queued_future in futures:
                            queued_future.cancel()
                        raise RuntimeError(
                            "连续 3 条重复诉求模型复核失败，已熔断"
                            + ("；成功结果已写入断点缓存" if cache else "")
                        ) from results[business_id]
                while len(futures) < active_limit and submit_next():
                    pass
    finally:
        if cache is not None:
            cache.close()
    return results
