"""The proofreading exchange: the steps, the connection they run on, and the runner.

``port`` is the connection (Ollama, LangChain, a recording or a replaying one), ``runner`` sends
one request of one step with the retry and repair rules, and ``exchanges`` holds the exchanges
themselves.  The GUI, the DOCX builder and the cache see only the domain result.
"""
