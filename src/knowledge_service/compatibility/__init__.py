"""The OpenAI-compatible adapter: OpenAI's protocol, translated to the native API.

ADR 0004: the native API keeps its own words, and only this package speaks
OpenAI's. Its routes live under `/openai/v1`, outside the native `/v1`
design, and stay out of the native OpenAPI document.
"""
