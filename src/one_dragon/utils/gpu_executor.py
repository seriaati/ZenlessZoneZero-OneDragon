import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from one_dragon.utils import thread_utils
from one_dragon.utils.log_utils import log

T = TypeVar("T")
_THREAD_PREFIX = "od_gpu"
_DML_PROVIDER = "DmlExecutionProvider"
_executor_local = threading.local()
_cuda_dlls_lock = threading.Lock()
_cuda_dlls_preloaded: bool = False


def _mark_executor_thread() -> None:
    _executor_local.is_gpu_executor_thread = True


# 限制只能有一个方法访问 DirectML GPU 避免多 session 并发崩溃
_executor = ThreadPoolExecutor(
    thread_name_prefix=_THREAD_PREFIX,
    max_workers=1,
    initializer=_mark_executor_thread,
)


def is_executor_thread() -> bool:
    return bool(getattr(_executor_local, "is_gpu_executor_thread", False))


def submit(fn: Callable[..., T], /, *args, **kwargs) -> Future[T]:
    f = _executor.submit(fn, *args, **kwargs)
    f.add_done_callback(thread_utils.handle_future_result)
    return f


def run_sync(fn: Callable[..., T], /, *args, **kwargs) -> T:
    if is_executor_thread():
        return fn(*args, **kwargs)
    return submit(fn, *args, **kwargs).result()


def preload_cuda_dlls() -> None:
    """
    预加载 CUDA / cuDNN 动态库 在创建 CUDA 会话前调用 只会加载一次

    Linux 下 onnxruntime-gpu 用的 CUDA / cuDNN 来自 pip 的 nvidia-* 包 不在系统库搜索路径里
    不预加载的话 CUDAExecutionProvider 会加载失败 (缺 libcublasLt.so.12 等) 并回退到 CPU。
    Windows 下不需要。
    """
    global _cuda_dlls_preloaded
    if sys.platform != 'linux':
        return
    with _cuda_dlls_lock:
        if _cuda_dlls_preloaded:
            return
        _cuda_dlls_preloaded = True
        try:
            import onnxruntime
            onnxruntime.preload_dlls()
        except Exception:
            log.warning('预加载CUDA动态库失败 CUDA不可用时会回退到CPU', exc_info=True)


def should_serialize_providers(providers: Sequence[str] | None) -> bool:
    return providers is not None and _DML_PROVIDER in providers


def create_onnx_session(
        factory: Callable[[], T],
        providers: Sequence[str] | None,
) -> T:
    if should_serialize_providers(providers):
        return run_sync(factory)
    return factory()


def should_serialize_session(session) -> bool:
    try:
        providers = session.get_providers()
    except Exception:
        log.warning('获取ONNX Runtime执行提供程序失败，将保守串行化执行', exc_info=True)
        return True
    return should_serialize_providers(providers)


def run_session(session, output_names, input_feed=None, **kwargs):
    if should_serialize_session(session):
        return run_sync(session.run, output_names, input_feed, **kwargs)
    return session.run(output_names, input_feed, **kwargs)


def shutdown(wait: bool = True) -> None:
    _executor.shutdown(wait=wait)
