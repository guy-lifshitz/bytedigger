"""Reference LLM backend adapters for OSS users (#302 seam).

These are optional — core engine_py runs without them. Import a backend
module to register it with llm_subprocess.register_backend so
invoke_llm_subprocess(backend=<name>, ...) dispatches to it.

Registration is guarded (run.py wraps each import in try/except): if a
backend's dependencies are not importable, its register() call is silently
skipped rather than crashing the engine. When installing a reference
backend's package, install it into the venv that HAL_BUILD_PYTHON points
at — the interpreter the engine subprocess actually runs under — not
necessarily your shell's currently active venv, or the guarded import will
keep silently skipping registration. If an unknown/unregistered backend
name is passed to invoke_llm_subprocess, the resulting E_LLM_BACKEND_UNKNOWN
error message now includes an install hint when the name matches a known
reference backend (see llm_subprocess._REFERENCE_BACKEND_INSTALL_HINTS).
"""
