"""
Filtering logic for bankruptcy records.
Matches records against user-defined criteria.
"""
import logging
from typing import Callable

from .models import BankruptcyRecord
from config import FilterCriteria

logger = logging.getLogger(__name__)


class BankruptcyFilter:
    """Filter bankruptcy records based on criteria."""
    
    def __init__(self, criteria: FilterCriteria):
        self.criteria = criteria
    
    def matches(self, record: BankruptcyRecord) -> tuple[bool, str]:
        """
        Check if a record matches the filter criteria.
        
        Returns:
            Tuple of (matches: bool, reason: str)
        """
        reasons = []
        failed_reasons = []
        
        # Check employees
        if self.criteria.min_employees is not None:
            if record.company.employees is None:
                failed_reasons.append("employees unknown")
            elif record.company.employees < self.criteria.min_employees:
                failed_reasons.append(f"employees {record.company.employees} < min {self.criteria.min_employees}")
            else:
                reasons.append(f"employees {record.company.employees} >= {self.criteria.min_employees}")
        
        if self.criteria.max_employees is not None:
            if record.company.employees is not None and record.company.employees > self.criteria.max_employees:
                failed_reasons.append(f"employees {record.company.employees} > max {self.criteria.max_employees}")
        
        # Check revenue
        if self.criteria.min_revenue is not None:
            if record.company.revenue is None:
                failed_reasons.append("revenue unknown")
            elif record.company.revenue < self.criteria.min_revenue:
                failed_reasons.append(f"revenue {record.company.revenue} < min {self.criteria.min_revenue}")
            else:
                reasons.append(f"revenue {record.company.revenue} >= {self.criteria.min_revenue}")
        
        if self.criteria.max_revenue is not None:
            if record.company.revenue is not None and record.company.revenue > self.criteria.max_revenue:
                failed_reasons.append(f"revenue {record.company.revenue} > max {self.criteria.max_revenue}")
        
        # Check business types (SNI codes or descriptions)
        if self.criteria.business_types:
            biz_type = record.company.business_type or ""
            sni_code = record.company.sni_code or ""
            
            type_match = any(
                bt.lower() in biz_type.lower() or bt in sni_code
                for bt in self.criteria.business_types
            )
            
            if type_match:
                reasons.append(f"business type match: {biz_type or sni_code}")
            else:
                failed_reasons.append(f"business type {biz_type or 'unknown'} not in filter")
        
        # Check regions
        if self.criteria.regions:
            region = record.company.region or record.company.city or ""
            region_match = any(
                r.lower() in region.lower()
                for r in self.criteria.regions
            )
            
            if region_match:
                reasons.append(f"region match: {region}")
            else:
                failed_reasons.append(f"region {region or 'unknown'} not in filter")
        
        # Check exclude keywords
        if self.criteria.exclude_keywords:
            searchable_text = " ".join([
                record.company.name or "",
                record.company.description or "",
                record.company.business_type or "",
            ]).lower()
            
            for keyword in self.criteria.exclude_keywords:
                if keyword.lower() in searchable_text:
                    failed_reasons.append(f"contains excluded keyword: {keyword}")
                    break
        
        # Check include keywords (at least one must match if specified)
        if self.criteria.include_keywords:
            searchable_text = " ".join([
                record.company.name or "",
                record.company.description or "",
                record.company.business_type or "",
            ]).lower()
            
            keyword_match = any(
                kw.lower() in searchable_text
                for kw in self.criteria.include_keywords
            )
            
            if keyword_match:
                reasons.append("contains included keyword")
            else:
                failed_reasons.append("no included keywords found")
        
        # Determine overall match
        # Record matches if:
        # 1. No criteria specified (show all), OR
        # 2. All hard criteria pass (min/max values, exclude keywords), AND
        # 3. At least one soft criteria matches (business type, region, include keywords) if specified
        
        no_criteria = (
            self.criteria.min_employees is None and
            self.criteria.max_employees is None and
            self.criteria.min_revenue is None and
            self.criteria.max_revenue is None and
            not self.criteria.business_types and
            not self.criteria.regions and
            not self.criteria.exclude_keywords and
            not self.criteria.include_keywords
        )
        
        if no_criteria:
            return True, "no filter criteria - showing all"
        
        if failed_reasons:
            return False, "; ".join(failed_reasons)
        
        return True, "; ".join(reasons) if reasons else "passed all criteria"
    
    def filter_records(
        self,
        records: list[BankruptcyRecord],
        update_records: bool = True
    ) -> list[BankruptcyRecord]:
        """
        Filter a list of records.
        
        Args:
            records: List of records to filter
            update_records: If True, update matches_criteria and filter_notes on records
        
        Returns:
            List of matching records
        """
        matched = []
        
        for record in records:
            matches, reason = self.matches(record)
            
            if update_records:
                record.matches_criteria = matches
                record.filter_notes = reason
            
            if matches:
                matched.append(record)
                logger.debug(f"Record {record.display_name} MATCHED: {reason}")
            else:
                logger.debug(f"Record {record.display_name} FILTERED: {reason}")
        
        logger.info(f"Filtered {len(records)} records -> {len(matched)} matches")
        return matched


def create_default_filter() -> BankruptcyFilter:
    """Create a filter with sensible defaults for business leads."""
    criteria = FilterCriteria(
        min_employees=5,  # Focus on companies with some scale
        min_revenue=1000000,  # At least 1M SEK revenue
        exclude_keywords=["enskild firma", "privatperson"],  # Skip sole proprietors
    )
    return BankruptcyFilter(criteria)


def create_broad_filter() -> BankruptcyFilter:
    """Create a filter that accepts most records."""
    return BankruptcyFilter(FilterCriteria())
