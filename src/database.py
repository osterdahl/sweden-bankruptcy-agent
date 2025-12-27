"""
Database layer for storing and querying bankruptcy records.
Uses SQLite for simplicity and portability.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from .models import BankruptcyRecord, CompanyInfo, BankruptcyAdministrator, BankruptcyStatus


class BankruptcyDatabase:
    """SQLite database for bankruptcy records."""
    
    def __init__(self, db_path: str = "data/bankruptcies.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS bankruptcies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    org_number TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    case_number TEXT,
                    status TEXT,
                    declaration_date TEXT,
                    publication_date TEXT,
                    court TEXT,
                    
                    -- Company details
                    address TEXT,
                    postal_code TEXT,
                    city TEXT,
                    region TEXT,
                    sni_code TEXT,
                    business_type TEXT,
                    description TEXT,
                    employees INTEGER,
                    revenue REAL,
                    profit REAL,
                    assets REAL,
                    registration_date TEXT,
                    legal_form TEXT,
                    
                    -- Administrator
                    admin_name TEXT,
                    admin_title TEXT,
                    admin_law_firm TEXT,
                    admin_email TEXT,
                    admin_phone TEXT,
                    admin_address TEXT,
                    
                    -- Metadata
                    creditor_meeting_date TEXT,
                    source_url TEXT,
                    scraped_at TEXT,
                    matches_criteria INTEGER DEFAULT 0,
                    filter_notes TEXT,
                    raw_data TEXT,
                    
                    -- Indexes
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    
                    UNIQUE(org_number, declaration_date)
                );
                
                CREATE INDEX IF NOT EXISTS idx_org_number ON bankruptcies(org_number);
                CREATE INDEX IF NOT EXISTS idx_declaration_date ON bankruptcies(declaration_date);
                CREATE INDEX IF NOT EXISTS idx_matches_criteria ON bankruptcies(matches_criteria);
                CREATE INDEX IF NOT EXISTS idx_scraped_at ON bankruptcies(scraped_at);
                
                CREATE TABLE IF NOT EXISTS scrape_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    month TEXT,
                    year INTEGER,
                    total_found INTEGER DEFAULT 0,
                    total_matched INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    error_message TEXT
                );
            """)
    
    def upsert_record(self, record: BankruptcyRecord) -> int:
        """Insert or update a bankruptcy record."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO bankruptcies (
                    org_number, company_name, case_number, status,
                    declaration_date, publication_date, court,
                    address, postal_code, city, region,
                    sni_code, business_type, description,
                    employees, revenue, profit, assets,
                    registration_date, legal_form,
                    admin_name, admin_title, admin_law_firm,
                    admin_email, admin_phone, admin_address,
                    creditor_meeting_date, source_url, scraped_at,
                    matches_criteria, filter_notes, raw_data, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(org_number, declaration_date) DO UPDATE SET
                    company_name = excluded.company_name,
                    case_number = excluded.case_number,
                    status = excluded.status,
                    court = excluded.court,
                    address = excluded.address,
                    postal_code = excluded.postal_code,
                    city = excluded.city,
                    region = excluded.region,
                    sni_code = excluded.sni_code,
                    business_type = excluded.business_type,
                    description = excluded.description,
                    employees = excluded.employees,
                    revenue = excluded.revenue,
                    profit = excluded.profit,
                    assets = excluded.assets,
                    admin_name = excluded.admin_name,
                    admin_title = excluded.admin_title,
                    admin_law_firm = excluded.admin_law_firm,
                    admin_email = excluded.admin_email,
                    admin_phone = excluded.admin_phone,
                    admin_address = excluded.admin_address,
                    matches_criteria = excluded.matches_criteria,
                    filter_notes = excluded.filter_notes,
                    raw_data = excluded.raw_data,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                record.company.org_number,
                record.company.name,
                record.case_number,
                record.status.value,
                record.declaration_date.isoformat() if record.declaration_date else None,
                record.publication_date.isoformat() if record.publication_date else None,
                record.court,
                record.company.address,
                record.company.postal_code,
                record.company.city,
                record.company.region,
                record.company.sni_code,
                record.company.business_type,
                record.company.description,
                record.company.employees,
                record.company.revenue,
                record.company.profit,
                record.company.assets,
                record.company.registration_date.isoformat() if record.company.registration_date else None,
                record.company.legal_form,
                record.administrator.name if record.administrator else None,
                record.administrator.title if record.administrator else None,
                record.administrator.law_firm if record.administrator else None,
                record.administrator.email if record.administrator else None,
                record.administrator.phone if record.administrator else None,
                record.administrator.address if record.administrator else None,
                record.creditor_meeting_date.isoformat() if record.creditor_meeting_date else None,
                record.source_url,
                record.scraped_at.isoformat() if record.scraped_at else None,
                1 if record.matches_criteria else 0,
                record.filter_notes,
                record.to_json(),
            ))
            return cursor.lastrowid
    
    def _row_to_record(self, row: sqlite3.Row) -> BankruptcyRecord:
        """Convert database row to BankruptcyRecord."""
        def parse_date(val):
            return datetime.fromisoformat(val) if val else None
        
        company = CompanyInfo(
            org_number=row["org_number"],
            name=row["company_name"],
            address=row["address"],
            postal_code=row["postal_code"],
            city=row["city"],
            region=row["region"],
            sni_code=row["sni_code"],
            business_type=row["business_type"],
            description=row["description"],
            employees=row["employees"],
            revenue=row["revenue"],
            profit=row["profit"],
            assets=row["assets"],
            registration_date=parse_date(row["registration_date"]),
            legal_form=row["legal_form"],
        )
        
        administrator = None
        if row["admin_name"]:
            administrator = BankruptcyAdministrator(
                name=row["admin_name"],
                title=row["admin_title"],
                law_firm=row["admin_law_firm"],
                email=row["admin_email"],
                phone=row["admin_phone"],
                address=row["admin_address"],
            )
        
        return BankruptcyRecord(
            id=row["id"],
            case_number=row["case_number"],
            company=company,
            status=BankruptcyStatus(row["status"]) if row["status"] else BankruptcyStatus.UNKNOWN,
            declaration_date=parse_date(row["declaration_date"]),
            publication_date=parse_date(row["publication_date"]),
            court=row["court"],
            administrator=administrator,
            creditor_meeting_date=parse_date(row["creditor_meeting_date"]),
            source_url=row["source_url"],
            scraped_at=parse_date(row["scraped_at"]),
            matches_criteria=bool(row["matches_criteria"]),
            filter_notes=row["filter_notes"],
        )
    
    def get_by_org_number(self, org_number: str) -> list[BankruptcyRecord]:
        """Get all bankruptcy records for a company."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM bankruptcies WHERE org_number = ? ORDER BY declaration_date DESC",
                (org_number,)
            )
            return [self._row_to_record(row) for row in cursor.fetchall()]
    
    def get_by_date_range(
        self,
        start_date: datetime,
        end_date: datetime,
        matched_only: bool = False
    ) -> list[BankruptcyRecord]:
        """Get records within a date range."""
        query = """
            SELECT * FROM bankruptcies 
            WHERE declaration_date >= ? AND declaration_date <= ?
        """
        params = [start_date.isoformat(), end_date.isoformat()]
        
        if matched_only:
            query += " AND matches_criteria = 1"
        
        query += " ORDER BY declaration_date DESC"
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_record(row) for row in cursor.fetchall()]
    
    def get_for_month(self, year: int, month: int, matched_only: bool = False) -> list[BankruptcyRecord]:
        """Get all records for a specific month."""
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        return self.get_by_date_range(start, end, matched_only)
    
    def get_latest(self, limit: int = 100, matched_only: bool = False) -> list[BankruptcyRecord]:
        """Get most recent records."""
        query = "SELECT * FROM bankruptcies"
        if matched_only:
            query += " WHERE matches_criteria = 1"
        query += " ORDER BY declaration_date DESC LIMIT ?"
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, (limit,))
            return [self._row_to_record(row) for row in cursor.fetchall()]
    
    def start_scrape_run(self, month: str, year: int) -> int:
        """Record the start of a scrape run."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO scrape_runs (started_at, month, year) VALUES (?, ?, ?)",
                (datetime.now().isoformat(), month, year)
            )
            return cursor.lastrowid
    
    def complete_scrape_run(
        self,
        run_id: int,
        total_found: int,
        total_matched: int,
        status: str = "completed",
        error_message: Optional[str] = None
    ):
        """Record completion of a scrape run."""
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE scrape_runs 
                SET completed_at = ?, total_found = ?, total_matched = ?, status = ?, error_message = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), total_found, total_matched, status, error_message, run_id))
    
    def export_to_json(self, records: list[BankruptcyRecord], filepath: str):
        """Export records to JSON file."""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in records], f, ensure_ascii=False, indent=2)
    
    def export_to_csv(self, records: list[BankruptcyRecord], filepath: str):
        """Export records to CSV file."""
        import csv
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        if not records:
            return
        
        fieldnames = [
            "org_number", "company_name", "declaration_date", "court", "status",
            "city", "region", "business_type", "employees", "revenue",
            "administrator", "law_firm", "source_url"
        ]
        
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in records:
                writer.writerow({
                    "org_number": r.company.org_number,
                    "company_name": r.company.name,
                    "declaration_date": r.declaration_date.strftime("%Y-%m-%d") if r.declaration_date else "",
                    "court": r.court or "",
                    "status": r.status.value,
                    "city": r.company.city or "",
                    "region": r.company.region or "",
                    "business_type": r.company.business_type or "",
                    "employees": r.company.employees or "",
                    "revenue": r.company.revenue or "",
                    "administrator": r.administrator.name if r.administrator else "",
                    "law_firm": r.administrator.law_firm if r.administrator else "",
                    "source_url": r.source_url or "",
                })
