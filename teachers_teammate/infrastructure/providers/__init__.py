"""Provider sub-package: one module per LLM integration.

Each module exposes a single ``create(model, **kwargs) -> BaseChatModel`` function.
The active provider is selected at runtime by :func:`~teachers_teammate.infrastructure.llm_factory.build_llm`.
"""
