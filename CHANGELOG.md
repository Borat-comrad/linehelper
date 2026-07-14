# Changelog

## Unreleased

- Added a deterministic organization structure parser for
  `data/raw_docs/bvr_company_structure_instruction_v2 (2).txt`.
- Added semantic chunk generation for organization overview, units, employees,
  responsibility routes, vacancies, inactive bodies, and role combinations.
- Added idempotent organization import into `MemoryStore` using scoped metadata
  deletion for `organization_structure` chunks of source version `2025-12-17`.
- Added contact normalization for Russian work phones and email addresses.
- Added CLI import script and a separate smoke test database for organization
  retrieval checks.
- Added parser, indexer, retrieval, and MemoryStore tests for the organization
  contour.
