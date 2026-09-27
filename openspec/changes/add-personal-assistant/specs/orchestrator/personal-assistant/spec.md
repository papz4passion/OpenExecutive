# Spec Delta

## Purpose

Defines the personal-life specialist that the Executive can consult for personal life-admin/scheduling and family/relationship matters, and the routing/isolation guarantees that keep it separate from the business specialists.

## ADDED Requirements

### Requirement: Personal assistant specialist is registered and consultable
The system SHALL register a `personal_assistant` specialist in the same specialist registry as the business specialists, consultable by the Executive through the existing `consult_specialist` tool.

#### Scenario: Executive routes a personal-life question
- **WHEN** the principal asks a question about personal scheduling, a family event, or a personal reminder
- **THEN** the Executive consults the `personal_assistant` specialist and synthesizes its answer into its own reply, the same way it does for a business specialist

#### Scenario: Personal assistant listed as an available area
- **WHEN** the chat UI needs to name an area whose specialist could not answer
- **THEN** the `personal_assistant` specialist has a corresponding plain-words area name, consistent with every other registered specialist

### Requirement: Personal assistant covers life-admin/scheduling and family/relationships
The `personal_assistant` specialist SHALL handle requests in two domains: (1) general personal life-admin and scheduling — appointments, reminders, travel, household tasks; and (2) family and relationships — birthdays, family events, social commitments, gift reminders.

#### Scenario: Life-admin request
- **WHEN** the principal asks the Executive to remind them of an upcoming personal appointment
- **THEN** the `personal_assistant` specialist is the one consulted, not a business specialist

#### Scenario: Family/relationship request
- **WHEN** the principal asks about an upcoming family member's birthday or a family event
- **THEN** the `personal_assistant` specialist is the one consulted, not a business specialist

### Requirement: Personal assistant is isolated from business specialist context and knowledge
The `personal_assistant` specialist SHALL NOT receive the company profile, company documents, or any other business specialist's retrieved knowledge. Business specialists SHALL NOT receive personal data or the `personal_assistant` specialist's retrieved knowledge.

#### Scenario: Business specialist consulted alongside personal assistant in one turn
- **WHEN** a single user turn triggers both a business specialist (e.g. `cfo`) and the `personal_assistant` specialist in parallel
- **THEN** neither specialist's retrieved knowledge, profile data, or context is included in the other's system or user-turn context

#### Scenario: Personal data never enters the Executive's shared company-context cache block
- **WHEN** the Executive builds its cached system prompt for a turn
- **THEN** no personal data appears in that cache block; personal context reaches the `personal_assistant` specialist only through that specialist's own user-turn context, never through a block shared with business specialists or the Executive's company-context block

### Requirement: Personal assistant failures do not break the turn
A failure in the `personal_assistant` specialist SHALL be isolated the same way a business specialist failure is isolated, and SHALL NOT prevent other specialists' results or the Executive's overall reply.

#### Scenario: Personal assistant call fails
- **WHEN** the `personal_assistant` specialist's call raises an error or times out
- **THEN** the Executive's turn completes using the other specialists' results, with the personal assistant marked unavailable for that turn
