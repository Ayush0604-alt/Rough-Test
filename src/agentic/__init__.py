"""Skill-pack-driven agentic system.

A *skill pack* is a directory of `SKILL.md` files plus a `pack.yaml` manifest that declares a
workflow. The manifest compiles into a LangGraph `StateGraph`, so adding a new pack (for example
test-case generation) requires no changes to the graph-wiring code.
"""

__version__ = "0.1.0"
