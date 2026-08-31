# [PRODUCT]

[PRODUCT] is an AI admin assistant for real estate agents and their teams. It watches the tenant's Gmail and Google Calendar and turns routine admin work into tasks, calendar events, and drafts. This page is the product description for people considering the service and for reviewers of our Google API access.

[Privacy policy](privacy.md) · [Terms of service](terms.md)

## Who it is for

[PRODUCT] is for a real estate team that already works in Gmail and Google Calendar. The tenant is the brokerage or team that signs up. Staff on that tenant share a task list. Clients of that team are not users of [PRODUCT].

## What it does

These functions are in production.

**Unanswered mail becomes a task.** The app watches the tenant's inbox. When an important email sits unanswered past a window the tenant chooses, [PRODUCT] opens a tracked task with a deadline.

**Showing requests.** Showing requests that arrive through ShowingSmart get a property-info reply composed from the listing, and a calendar event. A message that would go to someone outside the team waits for staff sign-off before it is sent.

**Forwarded invites.** When someone on the team forwards a calendar invite to the connected inbox, the app parses it and places the event on the team's calendar.

**Meeting transcripts.** When a meeting transcript arrives, the app turns the action items into assigned tasks.

**Daily digest and a shared list.** Each morning the team can receive a digest email of open tasks. The same list is visible to the staff on that tenant.

**Lead follow-up.** The app tracks leads and can nudge the team when a lead has gone quiet.

## How approval works

By default [PRODUCT] acts only inside the tenant's own team: their inbox, their calendar, their staff. Configuration for that tenant decides what may run unattended inside that boundary.

Anything that leaves the team requires an explicit approval. Client-facing messages wait for sign-off. The app does not send mail to a client until someone on the staff accepts the draft.

Google data used by the app stays in the tenant's own Gmail, Calendar, Sheets, and Drive files the tenant connects. It is not mixed with another tenant's data.

## How onboarding works

A person on our side works with the tenant for about two weeks to match [PRODUCT] to that team's workflow: which inbox to watch, how long to wait before unanswered mail becomes a task, who gets the digest, and which lead sheet to use if the team keeps leads in Google Sheets.

After that, human support continues. The tenant can change the wait window and related settings as their workflow changes.

## Privacy and terms

Google API access is described in the [privacy policy](privacy.md), including each OAuth scope and what it is used for. Use of [PRODUCT] is governed by the [terms of service](terms.md).

Questions: [SUPPORT_EMAIL]
