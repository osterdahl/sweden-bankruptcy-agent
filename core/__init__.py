"""
Core modules for the Nordic Bankruptcy Monitor.

Shared pipeline logic used by all country plugins.

Submodules:
    core.models       - BankruptcyRecord dataclass
    core.scoring      - NACE code maps, rule-based and AI scoring
    core.email_lookup - Trustee email extraction and Brave Search lookup
    core.pipeline     - Orchestrator: scrape -> dedup -> score -> lookup -> stage
    core.reporting    - Email report generation (HTML + plain text) and sending
"""
