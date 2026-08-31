# Privacy policy

Last updated: 30 August 2026

This policy describes how [PRODUCT], the service operated at yamin.io, handles information, including information received from Google APIs. It is written for tenants, their staff, and Google API verification reviewers.

Contact: [SUPPORT_EMAIL]

[Product description](index.md) · [Terms of service](terms.md)

## What this policy covers

[PRODUCT] is an AI admin assistant for a real estate team. A tenant connects their own Google account so the app can watch that team's Gmail and calendar, keep a shared task list, track leads, and send mail the tenant has allowed.

This policy covers that service and the public pages on yamin.io. It does not cover Google's own handling of Gmail, Calendar, Sheets, or Drive. Google's policies apply to those products directly.

## Google user data we access

The tenant grants access by signing in with Google and accepting the OAuth consent screen. [PRODUCT] requests only the scopes below. Each scope is tied to a function described on the [product page](index.md).

### Gmail: read and modify (`gmail.modify`)

Full scope: `https://www.googleapis.com/auth/gmail.modify`

**Data accessed.** Message and thread content, headers such as From, To, Cc, Subject, Date, and Message-ID, labels, and mailbox metadata in the connected Gmail account.

**Used for.**

- Detecting important mail that has gone unanswered past the tenant's wait window, so the app can open a tracked task with a deadline.
- Reading ShowingSmart showing requests so a property-info reply can be composed and a calendar event prepared.
- Reading forwarded calendar invites so they can be parsed and placed on the calendar.
- Reading meeting transcripts that arrive by email so action items can become assigned tasks.
- Applying labels or otherwise marking a message the app has already handled, so the same item is not opened as a new task again.

### Gmail: send (`gmail.send`)

Full scope: `https://www.googleapis.com/auth/gmail.send`

**Data accessed.** The app can send email from the connected Gmail account. It reads enough addressing and subject information to send those messages.

**Used for.**

- Sending the daily task digest to the tenant's staff.
- Sending quiet-lead nudges to the tenant's staff.
- Sending a ShowingSmart property-info reply to the requesting agent, using property information the tenant prepared and approved in advance.

Showing-request replies use tenant-pre-approved content and send automatically on a matching request. Other client-facing mail is not sent until a staff member approves the draft. Sends and approval decisions are recorded.

### Calendar events (`calendar.events`)

Full scope: `https://www.googleapis.com/auth/calendar.events`

**Data accessed.** Event titles, times, locations, descriptions, attendee lists, and related event metadata on calendars the connected account can write.

**Used for.**

- Creating a calendar event for a ShowingSmart showing request.
- Placing a forwarded invite on the team's calendar.

### Google Sheets (`spreadsheets`)

Full scope: `https://www.googleapis.com/auth/spreadsheets`

**Data accessed.** Cell values and sheet structure in Google Sheets the tenant connects for lead tracking, including names, contact details, status, last-touch dates, and other columns in that sheet.

**Used for.** Lead tracking and quiet-lead nudges, so the app can see which leads have gone quiet and write status updates the tenant keeps in that sheet.

### Drive file access (`drive.file`)

Full scope: `https://www.googleapis.com/auth/drive.file`

**Data accessed.** Only Google Drive files the tenant creates with [PRODUCT] or opens with [PRODUCT]. This is not access to the tenant's entire Drive.

**Used for.** Opening and updating the lead spreadsheet the tenant connects. That file lives on Google Drive. This scope is how the app reaches that file without a full Drive grant.

## Other information we handle

The tenant also has application records that are not Google API payloads: the shared task list, deadlines, digest membership, approval decisions, and configuration. Configuration includes the wait window, connected inbox, digest recipients, and which sheet to use. Those records are stored per tenant.

During onboarding, which lasts about two weeks, and later for support, our staff work with the tenant on that configuration. Staff use tenant information only to provide the service.

The public yamin.io pages do not require a Google sign-in. Ordinary web-server logs on those pages may include IP address, user agent, and time of request.

## How Google user data is used and not used

Google user data is processed per tenant to provide the functions above. The app may send relevant message, transcript, or sheet text to a language-model provider acting as a processor, solely so the app can classify mail, compose drafts for approval, extract event details, and turn transcripts into tasks.

We do not sell Google user data. We do not share it with other tenants. We do not use it for advertising. We do not use it to build marketing profiles.

The app's use of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements.

## Sharing

We share Google user data only as follows.

- **Processors.** Hosting, email delivery through Gmail as the tenant connected it, and the language-model provider mentioned above, each acting on our instructions to run [PRODUCT] for that tenant.
- **The tenant's own staff.** People on the tenant can see tasks, digests, approvals, and other records for their team.
- **Legal demand.** If we are required by law to disclose information, we will disclose only what that demand covers.

We do not sell information. We do not share it for advertising.

## Retention and deletion

Gmail messages, calendar events, Sheets rows, and Drive files remain in the tenant's Google accounts. [PRODUCT] does not become the system of record for that data.

Tasks opened from unanswered mail, digest history, approval decisions, and lead-nudge state are kept in that tenant's isolated store while the tenant uses the service.

To stop Google API access, the tenant can disconnect [PRODUCT] under Google Account, Security, Third-party access. The app then cannot call Gmail, Calendar, Sheets, or Drive for that account.

To delete tenant-side copies, email [SUPPORT_EMAIL]. We delete that tenant's application records when the tenant asks, or when the tenant ends service, except for records we must keep for a limited time to meet a legal obligation such as a billing dispute. Deleting application records does not empty the tenant's Gmail, Calendar, or Sheets. Those remain in the tenant's Google accounts.

## Children

[PRODUCT] is for real estate businesses. It is not directed at children under 13, and we do not knowingly collect information from them.

## Changes

If this policy changes, we will update the date at the top of this page. Material changes that affect Google user data will be reflected here before we rely on a new use.

## Contact

[SUPPORT_EMAIL]
