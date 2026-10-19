"""
Latency fix: send independent model calls at the same time.

answer() starts calls that do not depend on each other together (prefetch),
then its original step-by-step logic runs unchanged. When a step makes a
call, invoke() returns the already-started result for that exact prompt.
Results a step never asks for are discarded.

Set PARALLEL_LLM_CALLS = False to go back to sequential calls.
"""
import contextlib
import contextvars
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

PARALLEL_LLM_CALLS = True
MAX_PARALLEL_CALLS = 6

_POOL = ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS, thread_name_prefix="llm-prefetch")
_PREFETCHED = contextvars.ContextVar("llm_prefetched", default=None)


def _key(prompt):
    if isinstance(prompt, str):
        return prompt
    return tuple((getattr(m, "type", ""), getattr(m, "content", str(m))) for m in prompt)


def invoke(prompt):
    """Use in place of st.session_state.openai_llm.invoke(prompt)."""
    pending = _PREFETCHED.get()
    if pending:
        future = pending.pop(_key(prompt), None)
        if future is not None:
            return future.result()
    return st.session_state.openai_llm.invoke(prompt)


def prefetch(prompts):
    """Start these prompts now, in parallel. None entries are skipped."""
    pending = _PREFETCHED.get()
    if not PARALLEL_LLM_CALLS or pending is None:
        return 0
    llm = st.session_state.get("openai_llm")
    if llm is None:
        return 0
    started = 0
    for prompt in prompts:
        if prompt is None:
            continue
        key = _key(prompt)
        if key in pending:
            continue
        pending[key] = _POOL.submit(llm.invoke, prompt)
        started += 1
    if started:
        print(f"[Latency] Prefetching {started} independent model call(s) in parallel")
    return started


@contextlib.contextmanager
def turn_scope():
    """Wrap one call to answer(); unused prefetches are dropped at the end."""
    token = _PREFETCHED.set({})
    try:
        yield
    finally:
        leftovers = _PREFETCHED.get() or {}
        _PREFETCHED.reset(token)
        for future in leftovers.values():
            future.cancel()
