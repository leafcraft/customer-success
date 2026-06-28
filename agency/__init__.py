"""customer-success — agency package (peer-to-peer team, Rule 1).

YAML-first: 16 agents defined in configs/config.yaml. 6 pure-YAML LLM
agents (business rules live in their prompts), 3 connector agents (n8n →
your Pendo/Amplitude, Salesforce/Gainsight, Klue/Crayon stacks), 5
humans (SDK-handled), 1 external audit sink, and 1 programmatic EXECUTOR
(play_runner_agent) that actually RUNS the human-approved retention play
— SENDS the save-message or BOOKS the QBR, fail-closed in @chain.

Python modules (auto-bound by filename): the self-reliant LLM agents'
@pre_compose/@chain (health_score, risk_detector, save_play, renewals),
play_runner_agent (the executor), and _shared/audit_logger.py (the WORM
sink, registered in main.py).
"""
