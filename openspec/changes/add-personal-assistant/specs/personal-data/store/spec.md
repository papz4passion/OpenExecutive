# Spec Delta

## Purpose

Defines the storage and isolation contract for personal data (profile and documents) so it stays fully separate from all company data at every layer: filesystem, version control, and knowledge retrieval.

## ADDED Requirements

### Requirement: Personal data lives outside version control
The system SHALL store personal profile and document data in a location excluded from version control, the same way company data is excluded.

#### Scenario: Personal data directory is gitignored
- **WHEN** personal profile or document files are created on disk
- **THEN** they live under a path matched by `.gitignore`, so `git status`/`git add` never stages them

### Requirement: Personal data has its own dedicated storage, separate from company data
The system SHALL store personal profile data and personal documents in locations and a knowledge-retrieval collection that are entirely separate from the company profile, company documents, and every other existing knowledge collection.

#### Scenario: Personal profile stored separately from company profile
- **WHEN** the principal's personal profile is loaded
- **THEN** it is read from a personal-data location distinct from the company profile's location, and loading one never reads or requires the other

#### Scenario: Personal documents indexed into their own collection
- **WHEN** a personal document is uploaded and indexed for retrieval
- **THEN** it is stored in a knowledge-retrieval collection dedicated to personal data, never in the company documents collection, the built-in knowledge collection, or any other existing collection

#### Scenario: Personal document retrieval never returns company data
- **WHEN** the `personal_assistant` specialist retrieves knowledge for a query
- **THEN** only the personal-data collection is queried, and results never include company documents or built-in business knowledge

#### Scenario: Company document retrieval never returns personal data
- **WHEN** any business specialist retrieves knowledge for a query
- **THEN** the personal-data collection is never queried, and results never include personal documents

### Requirement: Personal profile is never included in shared or business-specialist context
The system SHALL only ever inject the personal profile into the `personal_assistant` specialist's own per-call context. It SHALL NOT include the personal profile in the Executive's cached system prompt blocks or in any business specialist's context.

#### Scenario: Personal profile absent from Executive's cache blocks
- **WHEN** the Executive's system prompt cache blocks are built for a turn
- **THEN** the personal profile does not appear in any of those blocks

#### Scenario: Personal profile absent from a business specialist's turn
- **WHEN** a business specialist is consulted in the same turn as the `personal_assistant` specialist
- **THEN** the business specialist's context contains no personal profile content
