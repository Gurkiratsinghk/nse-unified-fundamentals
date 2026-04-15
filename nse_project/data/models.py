"""
ORM table definitions for the NSE fundamentals database.

Design decisions:
- companies          : master registry, one row per symbol ever seen
- nse100_constituents: audit trail of index additions / removals
- company_essentials : snapshot metrics from the #companyessentials block
                       (not year-indexed, one latest-row per company)
- yearly_financials  : single wide table that stores every numeric metric
                       from P&L, Balance Sheet, and Cash Flow in a
                       key-value (EAV) layout so that new metrics found
                       on the page are stored without schema changes
- rankings / portfolio: stub tables for future use
"""

from datetime import datetime, date

from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime,
    ForeignKey, UniqueConstraint, Text, Boolean,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ---------------------------------------------------------------------------
# Master company registry
# ---------------------------------------------------------------------------

class Company(Base):
    __tablename__ = "companies"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    symbol       = Column(String, unique=True, nullable=False, index=True)
    company_name = Column(String, nullable=True)
    sector       = Column(String, nullable=True)
    industry     = Column(String, nullable=True)
    isin         = Column(String, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)

    constituents  = relationship("NSE100Constituent", back_populates="company")
    essentials    = relationship("CompanyEssentials",  back_populates="company")
    financials    = relationship("YearlyFinancial",    back_populates="company")
    quarterly_financials = relationship("QuarterlyFinancial", back_populates="company")
    rankings      = relationship("Ranking",            back_populates="company")
    portfolios    = relationship("Portfolio",          back_populates="company")

    def __repr__(self):
        return f"<Company symbol={self.symbol} name={self.company_name}>"


# ---------------------------------------------------------------------------
# NSE100 constituent audit trail
# ---------------------------------------------------------------------------

class NSE100Constituent(Base):
    __tablename__ = "nse100_constituents"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    company_id   = Column(Integer, ForeignKey("companies.id"), nullable=False)
    added_date   = Column(Date, nullable=False)
    removed_date = Column(Date, nullable=True)   # NULL = currently active
    created_at   = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="constituents")

    def __repr__(self):
        return (
            f"<NSE100Constituent company_id={self.company_id} "
            f"added={self.added_date} removed={self.removed_date}>"
        )


# ---------------------------------------------------------------------------
# Company essentials snapshot (from #companyessentials block)
# Key-value layout so any label Data Source adds is captured automatically.
# ---------------------------------------------------------------------------

class CompanyEssentials(Base):
    __tablename__ = "company_essentials"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    company_id  = Column(Integer, ForeignKey("companies.id"), nullable=False)
    scraped_at  = Column(DateTime, default=datetime.utcnow)
    metric_name = Column(String, nullable=False)   # e.g. "market_cap", "pe_ratio"
    value_num   = Column(Float,  nullable=True)    # numeric value if parseable
    value_text  = Column(Text,   nullable=True)    # raw string otherwise
    is_synced   = Column(Boolean, default=False, index=True)  # Supabase sync flag
    created_at  = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("company_id", "metric_name", name="uq_essentials_company_metric"),
    )

    company = relationship("Company", back_populates="essentials")

    def __repr__(self):
        return (
            f"<CompanyEssentials company_id={self.company_id} "
            f"metric={self.metric_name} value={self.value_num or self.value_text}>"
        )


# ---------------------------------------------------------------------------
# Yearly financial data — EAV layout
#
# One row per (company, fiscal_year, source_table, metric_name).
# source_table is one of: "profit_loss", "balance_sheet", "cash_flow"
# This allows any metric Data Source exposes to be stored without a migration.
# ---------------------------------------------------------------------------

class YearlyFinancial(Base):
    __tablename__ = "yearly_financials"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    company_id   = Column(Integer, ForeignKey("companies.id"), nullable=False)
    fiscal_year  = Column(Integer, nullable=False)   # e.g. 2024
    source_table = Column(String,  nullable=False)   # profit_loss / balance_sheet / cash_flow
    metric_name  = Column(String,  nullable=False)   # snake_case label
    value_num    = Column(Float,   nullable=True)    # numeric value if parseable
    value_text   = Column(Text,    nullable=True)    # raw string otherwise
    is_synced    = Column(Boolean, default=False, index=True)  # Supabase sync flag
    scraped_at   = Column(DateTime, default=datetime.utcnow)
    created_at   = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "company_id", "fiscal_year", "source_table", "metric_name",
            name="uq_yearly_company_year_source_metric",
        ),
    )

    company = relationship("Company", back_populates="financials")

    def __repr__(self):
        return (
            f"<YearlyFinancial company_id={self.company_id} "
            f"fy={self.fiscal_year} src={self.source_table} "
            f"metric={self.metric_name} val={self.value_num or self.value_text}>"
        )


class QuarterlyFinancial(Base):
    __tablename__ = "quarterly_financials"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    company_id   = Column(Integer, ForeignKey("companies.id"), nullable=False)
    quarter_date = Column(String, nullable=False)    # e.g. "Dec 2025" or "Q3 2025"
    source_table = Column(String,  nullable=False)   # "quarterly_results"
    metric_name  = Column(String,  nullable=False)   # snake_case label
    value_num    = Column(Float,   nullable=True)    # numeric value if parseable
    value_text   = Column(Text,    nullable=True)    # raw string otherwise
    is_synced    = Column(Boolean, default=False, index=True)  # Supabase sync flag
    scraped_at   = Column(DateTime, default=datetime.utcnow)
    created_at   = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "company_id", "quarter_date", "source_table", "metric_name",
            name="uq_quarterly_company_date_source_metric",
        ),
    )

    company = relationship("Company", back_populates="quarterly_financials")

    def __repr__(self):
        return (
            f"<QuarterlyFinancial company_id={self.company_id} "
            f"q={self.quarter_date} src={self.source_table} "
            f"metric={self.metric_name} val={self.value_num or self.value_text}>"
        )


# ---------------------------------------------------------------------------
# Stub tables for future use
# ---------------------------------------------------------------------------

class Ranking(Base):
    __tablename__ = "rankings"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    company_id  = Column(Integer, ForeignKey("companies.id"), nullable=False)
    ranked_on   = Column(Date, nullable=False)
    config_used = Column(String, nullable=True)
    score       = Column(Float,  nullable=True)
    rank        = Column(Integer, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="rankings")


class Portfolio(Base):
    __tablename__ = "portfolio"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    company_id     = Column(Integer, ForeignKey("companies.id"), nullable=False)
    created_on     = Column(Date,    nullable=False)
    allocation_pct = Column(Float,   nullable=True)
    rationale      = Column(Text,    nullable=True)
    config_used    = Column(String,  nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="portfolios")