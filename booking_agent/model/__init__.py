"""A second reader, which asks a language model what a message means.

Added to the public reconstruction, like the golden calls and the MCP server:
the original read with rules, and so does this repository unless told
otherwise. This reader is chosen on purpose, asks an Ollama server running on
the same machine, and needs no account and no key. Nothing in clinic/,
conversation/ or channels/ imports it, and there is a test that says so.
"""
