"""Provenance seed data for the go-to-market knowledge domain.

Authority ladder mirrors the memory admission ladder: raw web content
enters informational; curated courseware and operator archives are
guidance; outcomes Tyler verified in his own market are organizational.
"""

SEED_SOURCES: list[dict] = [
    {"canonical_id": "mit-ocw", "family": "COURSE", "authority_level": "guidance",
     "issuing_body": "MIT OpenCourseWare (Sloan)", "notes": "Open courseware: 15.390 New Enterprises and related; CC BY-NC-SA; full lecture notes and readings"},
    {"canonical_id": "yc-library", "family": "YC", "authority_level": "guidance",
     "issuing_body": "Y Combinator", "notes": "Startup School library and YC essays; the most GTM-practical open corpus"},
    {"canonical_id": "first-round-review", "family": "REVIEW", "authority_level": "guidance",
     "issuing_body": "First Round Capital", "notes": "Operator-grade essays on positioning, pricing, and early sales"},
    {"canonical_id": "pg-essays", "family": "ESSAY", "authority_level": "guidance",
     "issuing_body": "Paul Graham", "notes": "Canonical startup essays (paulgraham.com); plain-HTML, cleanly ingestable"},
    {"canonical_id": "operator-book", "family": "BOOK", "authority_level": "guidance",
     "issuing_body": "published operators", "notes": "Positioning/negotiation books (Dunford, Fisher & Ury, Voss); ingested as notes/excerpts where licensing permits"},
    {"canonical_id": "applied-outcome", "family": "OUTCOME", "authority_level": "organizational",
     "issuing_body": "user", "notes": "A framework Tyler applied in his own market with a verified result; outranks doctrine"},
    {"canonical_id": "general-web", "family": "WEB", "authority_level": "informational",
     "issuing_body": "web", "notes": "Uncurated web content; enters informational and must earn promotion"},
]
