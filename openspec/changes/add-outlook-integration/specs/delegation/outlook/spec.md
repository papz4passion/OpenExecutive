# Spec Delta

## Purpose

Lets a teammate turn on "Act as me" against their own Outlook mailbox instead of Gmail, so the Executive can read their sent mail (for voice learning) and prepare drafts in their voice, without ever being able to send from their mailbox.

## ADDED Requirements

### Requirement: Per-person Outlook credential
The system SHALL store, at most, one Microsoft OAuth credential per person, associated with that person by their own address, independent of and never shared with the Executive's own Outlook mailbox credential.

#### Scenario: Credential file identifies its own address
- **WHEN** a person's Outlook credential is loaded
- **THEN** the stored address on the credential is compared to the person's People-entry email, and a mismatch (or a file naming a different address) is treated as no credential present

#### Scenario: The Executive's own address is refused
- **WHEN** a person's People-entry email equals the Executive's own configured Outlook/email address
- **THEN** Act as me for that person reports a "shared mailbox" status and is not usable, exactly as the Gmail delegation path does

### Requirement: Read-only and draft-only access
The system SHALL only ever read mail from, or create drafts in, a delegated Outlook mailbox. It SHALL NOT provide any way to send mail from a delegated mailbox.

#### Scenario: No send capability exists
- **WHEN** the delegated Outlook client is inspected or exercised
- **THEN** there is no code path, method, or tool that can send/deliver a message from the delegated mailbox — only drafts can be created

#### Scenario: Draft is created, never sent
- **WHEN** the ghostwriting flow prepares a reply or new message for a person using Act as me against Outlook
- **THEN** the result is a draft saved in that person's own Outlook mailbox (or a thread reply draft), and no message is transmitted to any recipient

### Requirement: Mailbox identity re-verified on every use
The system SHALL confirm, on every use of a delegated Outlook mailbox, that the credential still opens the mailbox belonging to the person's current People-entry address.

#### Scenario: Roster address changed since connecting
- **WHEN** a person's People-entry email has changed since they connected Outlook, and the stored credential now opens a mailbox with a different address than the current entry
- **THEN** the status check reports a mismatch and the delegated client is not used for that person

#### Scenario: Google as well as Outlook can be misconfigured independently
- **WHEN** a person has a Gmail Act as me credential but no Outlook credential, or vice versa
- **THEN** the ghostwriting flow uses whichever provider that person has a valid, connected credential for, and reports "not configured" for the other without affecting the connected one

### Requirement: Delegated mailbox unreachable from the general tool surface
The system SHALL only allow a delegated Outlook mailbox to be reached through the same fixed, typed handlers Gmail delegation exposes (e.g., the ghostwriting tool, the voice learner) — never through the general MCP/tool-calling surface available to arbitrary model turns.

#### Scenario: No generic tool can address a delegated mailbox
- **WHEN** the Executive's general tool-calling surface is enumerated for any turn
- **THEN** no tool on it can read or write to a person's delegated Outlook mailbox by arbitrary parameters; only the fixed handlers that call the delegation client directly can reach it
