"""
Scoring engine for the Nordic Bankruptcy Monitor.

Extracts and generalizes the scoring logic from bankruptcy_monitor.py.
Country plugins can override the default NACE code maps via get_industry_code_maps().
"""

import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from core.models import BankruptcyRecord

logger = logging.getLogger(__name__)


# ============================================================================
# DEFAULT NACE CODE MAPS
# ============================================================================
# These are the default maps used across all Nordic countries (NACE Rev. 2).
# Country plugins can override via get_industry_code_maps() for local variants.

DEFAULT_HIGH_VALUE_CODES: Dict[str, int] = {
    '58': 10,  # Publishing — text/media rights (books, journals, software)
    '59': 10,  # Film, video, sound production — media rights
    '60': 9,   # Broadcasting — audio/video content
    '62': 10,  # Computer programming/consultancy — source code, algorithms
    '63': 9,   # Information services — databases, data products
    '72': 10,  # Scientific R&D — research data, sensor data, datasets
    '742': 9,  # Photography — image libraries
    '90': 8,   # Creative arts — content rights, creative IP
    '91': 7,   # Libraries/archives/museums — collections, rights
    '26': 8,   # Computer/electronic manufacturing — firmware, embedded SW, CAD
    '71': 7,   # Architectural/engineering — CAD drawings, technical specs
    '73': 6,   # Advertising/market research — creative assets, research data
    '85': 6,   # Education — courseware, educational content
}

DEFAULT_LOW_VALUE_CODES: Dict[str, int] = {
    '56': 1,   # Food/beverage service
    '55': 1,   # Accommodation
    '45': 1,   # Motor vehicle retail/repair
    '47': 1,   # Retail
    '68': 1,   # Real estate
    '96': 1,   # Personal services (hair, laundry, etc.)
    '41': 2,   # Building construction
    '43': 2,   # Specialised construction
    '64': 2,   # Financial services (holding companies)
}

DEFAULT_ASSET_TYPE_MAP: Dict[str, str] = {
    '58': 'media',          # Publishing
    '59': 'media',          # Film/video/sound production
    '60': 'media',          # Broadcasting
    '62': 'code',           # Computer programming
    '63': 'database',       # Information services
    '72': 'database,sensor',# Scientific R&D
    '742': 'media',         # Photography
    '90': 'media',          # Creative arts
    '91': 'media,database', # Libraries/archives
    '26': 'code',           # Computer/electronic manufacturing
    '71': 'cad',            # Architectural/engineering
    '73': 'media,database', # Advertising/market research
    '85': 'media',          # Education
}

# Company name keywords that signal data asset potential.
# Note: 'forlag' is Swedish-specific; keep for now but don't add other languages yet.
_ASSET_KEYWORDS = [
    'data', 'tech', 'software', 'analytics', 'ai', 'cloud', 'digital',
    'media', 'photo', 'film', 'studio', 'content', 'publish', 'förlag',
    'sensor', 'robot', 'cad', 'design', 'research', 'lab',
]


# ============================================================================
# SCORING FUNCTIONS
# ============================================================================

def calculate_base_score(
    record: BankruptcyRecord,
    high_codes: Optional[Dict[str, int]] = None,
    low_codes: Optional[Dict[str, int]] = None,
    asset_map: Optional[Dict[str, str]] = None,
) -> int:
    """Rule-based scoring for Redpine data asset acquisition potential.

    If maps not provided, uses the DEFAULT maps (shared NACE Rev. 2).
    """
    if high_codes is None:
        high_codes = DEFAULT_HIGH_VALUE_CODES
    if low_codes is None:
        low_codes = DEFAULT_LOW_VALUE_CODES

    score = 3  # Low baseline — most bankruptcies are not relevant

    # Use industry_code (aliased as sni_code for SE backward compat)
    sni = record.industry_code
    if sni and sni != 'N/A' and len(sni) >= 2:
        sni_prefix = sni[:2]
        if sni_prefix in high_codes:
            score = high_codes[sni_prefix]
        elif sni_prefix in low_codes:
            score = low_codes[sni_prefix]
        if len(sni) >= 3 and sni[:3] in high_codes:
            score = high_codes[sni[:3]]

    # Size boost — more employees = more accumulated data assets
    if record.employees:
        if record.employees >= 50:
            score = min(score + 2, 10)
        elif record.employees >= 20:
            score = min(score + 1, 10)

    # Company name signals — Redpine-specific keywords
    if any(kw in record.company_name.lower() for kw in _ASSET_KEYWORDS):
        score = min(score + 1, 10)

    return score


def validate_with_ai(record: BankruptcyRecord) -> Tuple[int, str]:
    """Score a record with an AI model, identifying Redpine-relevant asset types.

    Provider is selected via AI_PROVIDER env var: 'openai' or 'anthropic' (default).
    The prompt is country-aware — uses record.country to determine nationality context.
    """
    # Determine country name for the prompt
    country_names = {
        'se': 'Swedish', 'no': 'Norwegian', 'dk': 'Danish', 'fi': 'Finnish',
    }
    country_adj = country_names.get(record.country, 'Swedish')  # default to Swedish for backward compat

    prompt = f"""You assess bankrupt {country_adj} companies for Redpine, which acquires data assets for AI training and licensing.

Redpine buys:
- code: software, firmware, ML models, algorithms, APIs
- media: books, articles, images, photos, video, audio (with rights)
- cad: engineering drawings, 3D models, technical specifications
- sensor: sensor recordings, robotics data, scientific measurements
- database: annotated datasets, research databases, domain corpora

Company: {record.company_name}
Industry: [{record.industry_code}] {record.industry_name}
Employees: {record.employees}
Revenue: {record.net_sales}
Assets: {record.total_assets}
Region: {record.region}

Score 1-10 acquisition value (10=must contact, 1=no interest).
Pick asset types from: code, media, cad, sensor, database, none.

Reply ONLY: SCORE:N ASSETS:type1,type2 REASON:one sentence"""

    provider = os.getenv('AI_PROVIDER', 'anthropic').lower()

    try:
        if provider == 'openai':
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                return (record.ai_score, record.ai_reason or "Rule-based only (no OPENAI_API_KEY)")
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=os.getenv('AI_MODEL', 'gpt-4o-mini'),
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            response = resp.choices[0].message.content.strip()
        else:
            api_key = os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                return (record.ai_score, record.ai_reason or "Rule-based only (no ANTHROPIC_API_KEY)")
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=os.getenv('AI_MODEL', 'claude-haiku-4-5-20251001'),
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            response = resp.content[0].text.strip()

        score_match  = re.search(r'SCORE:(\d+)', response)
        assets_match = re.search(r'ASSETS:([\w,]+)', response)
        reason_match = re.search(r'REASON:(.+)', response)

        ai_score = max(1, min(10, int(score_match.group(1)))) if score_match else record.ai_score
        if assets_match and assets_match.group(1) != 'none':
            record.asset_types = assets_match.group(1)
        ai_reason = reason_match.group(1).strip() if reason_match else response

        return (ai_score, ai_reason)

    except Exception as e:
        logger.warning(f"AI scoring failed for {record.company_name}: {e}")
        return (record.ai_score, f"[AI failed: {type(e).__name__}] {record.ai_reason or 'Rule-based only'}")


def score_bankruptcies(
    records: List[BankruptcyRecord],
    country_plugin=None,
) -> List[BankruptcyRecord]:
    """Score all records for Redpine data asset acquisition value.

    Rule-based scoring always runs. AI scoring runs on ALL records when
    AI_SCORING_ENABLED=true and the relevant API key is set.

    If country_plugin is provided, its industry code maps override the defaults.
    """
    # Get maps from country plugin or use defaults
    high_codes = DEFAULT_HIGH_VALUE_CODES
    low_codes = DEFAULT_LOW_VALUE_CODES
    asset_map = DEFAULT_ASSET_TYPE_MAP

    if country_plugin is not None:
        try:
            high_codes, low_codes, asset_map = country_plugin.get_industry_code_maps()
        except Exception as e:
            logger.warning(f"Failed to get maps from country plugin: {e}; using defaults")

    # Rule-based always runs
    for record in records:
        base_score = calculate_base_score(record, high_codes, low_codes, asset_map)
        record.ai_score = base_score
        if base_score >= 8:
            record.priority = "HIGH"
            record.ai_reason = "High-value data asset profile"
        elif base_score >= 5:
            record.priority = "MEDIUM"
            record.ai_reason = "Potential data assets"
        else:
            record.priority = "LOW"
            record.ai_reason = "Limited data asset potential"

        # Infer asset types from industry code (AI may override this later)
        if not record.asset_types and record.industry_code and record.industry_code != 'N/A':
            sni = record.industry_code
            record.asset_types = (
                asset_map.get(sni[:3])
                or asset_map.get(sni[:2])
            )

    # AI scoring: all records, not just HIGH
    ai_enabled = os.getenv('AI_SCORING_ENABLED', 'false').lower() == 'true'
    if not ai_enabled:
        logger.info("AI scoring disabled (AI_SCORING_ENABLED != true) — rule-based scores only")
        return records

    provider = os.getenv('AI_PROVIDER', 'anthropic').lower()
    key_name = 'OPENAI_API_KEY' if provider == 'openai' else 'ANTHROPIC_API_KEY'
    if not os.getenv(key_name):
        logger.warning(
            f"AI_SCORING_ENABLED=true but {key_name} is not set — "
            "falling back to rule-based scores."
        )
        return records

    # Proactive rate limiting — avoids 429s and the retry penalty.
    # Default 0.5s works for OpenAI (500+ RPM). Set AI_RATE_DELAY=12 for
    # Anthropic free/Tier-1 (~5 RPM).
    rate_delay = float(os.getenv('AI_RATE_DELAY', '0.5'))
    provider = os.getenv('AI_PROVIDER', 'anthropic')
    model = os.getenv('AI_MODEL', 'gpt-4o-mini' if provider == 'openai' else 'claude-haiku-4-5-20251001')
    logger.info(
        f"AI scoring {len(records)} records via {provider}/{model} "
        f"(~{len(records) * rate_delay / 60:.1f} min — set AI_RATE_DELAY in .env to adjust)"
    )
    ai_ok = 0
    ai_failed = 0
    for i, record in enumerate(records):
        if i > 0:
            time.sleep(rate_delay)
        ai_score, ai_reason = validate_with_ai(record)
        record.ai_score = ai_score
        record.ai_reason = ai_reason
        if ai_score >= 8:
            record.priority = "HIGH"
        elif ai_score >= 5:
            record.priority = "MEDIUM"
        else:
            record.priority = "LOW"
        if ai_reason.startswith("[AI failed"):
            ai_failed += 1
        else:
            ai_ok += 1

    high = sum(1 for r in records if r.priority == "HIGH")
    med = sum(1 for r in records if r.priority == "MEDIUM")
    logger.info(
        f"Scoring complete: {high} HIGH, {med} MEDIUM, {len(records)-high-med} LOW — "
        f"AI scored {ai_ok}/{len(records)}"
        + (f", {ai_failed} failed (rule-based fallback)" if ai_failed else "")
    )
    if ai_failed == len(records):
        logger.warning(
            f"AI scoring failed for ALL {ai_failed} records — check ANTHROPIC_API_KEY is valid "
            "and the Anthropic API is reachable."
        )
    return records
