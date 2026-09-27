# integrations/outlook-inbox Specification

## Purpose

Lets the Executive read its own Outlook inbox and reply, draft, or send mail through it, with the same roster-gated outbound safety and inbound triage behavior the Gmail integration already provides.

## Requirements

### Requirement: Outlook inbound polling
The system SHALL poll the Executive's own Outlook mailbox for unread mail on a configurable interval, and route each qualifying message to the Executive for handling, mirroring the existing Gmail poller's behavior (no roster gate on inbound; a `[POLICY]` notice is added for non-roster and contact senders; automated and self-sent senders are filtered; the message is marked read after handling).

#### Scenario: Unread mail is routed to the Executive
- **WHEN** the poller runs and finds an unread message in the Executive's Outlook inbox that is not from an automated sender and not self-addressed
- **THEN** the Executive receives the message content as an inbound turn, and the message is marked read once handling completes

#### Scenario: Operator pause holds the inbox untouched
- **WHEN** the Executive is paused
- **THEN** the poller does not fetch or mark any Outlook message as read, and unread mail remains unread until after resume

#### Scenario: Redirect-steering headers are stripped before the Executive sees the message
- **WHEN** an inbound message carries a `Reply-To` (or equivalent folded/aliased) header
- **THEN** that header is removed from the content the Executive sees, so the Executive cannot be steered into replying to an address the sender chose instead of the sender's own address

#### Scenario: Non-roster sender gets a policy notice, not silence
- **WHEN** an inbound message's sender address does not match any People-roster row
- **THEN** the Executive still receives the message, prefixed with a notice stating that an outbound reply to that address will be blocked, and an `integration_inbound` audit row is recorded with `outcome=accepted_non_roster`

#### Scenario: Mail from a contact is private to the principal
- **WHEN** an inbound message's sender resolves to one of the principal's contacts (not a team member)
- **THEN** the turn and every audit row it produces are private to the principal, and the Executive is instructed not to reply to the contact directly

### Requirement: Outlook outbound roster gate
The system SHALL refuse to send, reply to, or forward mail through the Executive's own Outlook mailbox unless every `to`/`cc`/`bcc` recipient resolves (case-insensitively) to a People-roster row's email address or to the Executive's own configured address, mirroring the Gmail outbound gate (`_check_gmail_recipients`).

#### Scenario: Send is blocked to an unknown recipient
- **WHEN** the Executive attempts to send Outlook mail to an address that is neither a People-roster row nor the Executive's own address
- **THEN** the send is refused, an `integration_outbound_blocked` audit row is recorded, and the Executive receives an error result instead of a delivered message

#### Scenario: Send succeeds to a roster recipient
- **WHEN** every recipient on an outbound Outlook send resolves to a People-roster row or the Executive's own address
- **THEN** the message is sent (or drafted, per the requested action) through the Executive's own Outlook mailbox

#### Scenario: A contact is only reachable when the principal is present
- **WHEN** an outbound Outlook send names one of the principal's contacts as a recipient
- **THEN** the send is allowed only when the current turn is one the principal started on a verified, private surface (or an explicit contact-egress grant resolving to the principal); every other case (inbound email turn, teammate turn, unattended run) is refused exactly as for an unknown recipient

### Requirement: Outlook connection configuration and status
The system SHALL treat the Executive's own Outlook integration as optional: absent configuration disables it without affecting any other integration, and its connection state is observable.

#### Scenario: Unconfigured Outlook does not affect other integrations
- **WHEN** no Microsoft OAuth configuration is present
- **THEN** the Outlook poller and outbound tools are not started/offered, and Gmail, Slack, Discord, Telegram, and Google Chat continue to operate exactly as before

#### Scenario: Connection status is visible
- **WHEN** an operator views the Executive's integration connection status
- **THEN** the Outlook connection state (connected, not configured, needs reconnect, or error) is shown alongside the existing Gmail connection state
