"""Canonical history reconstruction and dry-run diagnostics for DUON Gaz."""
from __future__ import annotations

from typing import Any

from .canonical_builder import async_build_canonical_bundle
from .canonical_history import CanonicalHistoryResult


async def async_build_canonical_history(
    runtime,
) -> tuple[CanonicalHistoryResult, dict[str, Any]]:
    """Build settled canonical history and diagnostics without persisting it."""
    build = await async_build_canonical_bundle(runtime)
    return build.settled, build.summary


async def async_rebuild_canonical_preview(runtime) -> dict[str, Any]:
    """Build canonical history without publishing any Recorder statistics."""
    build = await async_build_canonical_bundle(runtime)
    runtime.data["canonical_preview"] = build.summary
    await runtime.async_save()
    runtime.async_notify()
    return build.summary
