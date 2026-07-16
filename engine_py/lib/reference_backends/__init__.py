"""Reference LLM backend adapters for OSS users (#302 seam).

These are optional — core engine_py runs without them. Import a backend
module to register it with llm_subprocess.register_backend so
invoke_llm_subprocess(backend=<name>, ...) dispatches to it.
"""
