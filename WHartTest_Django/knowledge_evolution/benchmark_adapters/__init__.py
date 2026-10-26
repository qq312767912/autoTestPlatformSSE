"""公开集适配器注册表：每套基准各自独立实现，不共享映射逻辑。"""

from __future__ import annotations

from .aacr_bench import AacrBenchAdapter
from .base import (
    AdapterDependencyError,
    AdapterError,
    BenchmarkAdapter,
    ConversionOutcome,
)
from .swe_care import SweCareAdapter
from .swr_bench import SwrBenchAdapter

_ADAPTERS: dict[str, type[BenchmarkAdapter]] = {
    adapter.benchmark_id: adapter
    for adapter in (SweCareAdapter, SwrBenchAdapter, AacrBenchAdapter)
}


def get_adapter(benchmark_id: str) -> BenchmarkAdapter:
    try:
        return _ADAPTERS[benchmark_id]()
    except KeyError as exc:
        raise AdapterError(f"未注册的公开测评集适配器：{benchmark_id}") from exc


def adapter_registry() -> dict[str, dict[str, object]]:
    return {
        key: {"version": cls.version, "requires": list(cls.requires)}
        for key, cls in _ADAPTERS.items()
    }


__all__ = [
    "AdapterDependencyError",
    "AdapterError",
    "BenchmarkAdapter",
    "ConversionOutcome",
    "adapter_registry",
    "get_adapter",
]
