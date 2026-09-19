"""Agent framework: bounded, tool-using agents over deterministic assessments.

An agent always computes its assessment deterministically first
(``BaseAgent.assess``); the model may only explain it, choose among the
candidate actions it produced, and cite evidence that already exists. See
``docs/architecture/agent-protocol.md``.
"""
