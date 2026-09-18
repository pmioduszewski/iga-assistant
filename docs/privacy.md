---
layout: page
title: Privacy policy
permalink: /privacy
---

_Last updated: 2026-09-18_

Iga is software you install and run yourself. This policy describes what the
software does with Google user data. The person who set up the Google Cloud
project you are signing in to is the operator of that installation. In almost
every case that person is you.

## What Iga accesses

When you connect a Gmail account, Iga asks for these permissions:

- `gmail.modify`: read messages, apply and remove labels, archive, and delete
  messages when you ask it to.
- `gmail.settings.basic`: create and list Gmail filters.
- `userinfo.email` and `openid`: learn which address was connected.

Iga does not send mail on your behalf. Its delete command should be treated as
permanent: it does not go through the Trash folder.

## Where your data goes

- **Your computer.** Iga runs locally. Access tokens are stored in a file on
  your machine that only your user account can read. Mail content is read into
  memory to be processed and is not copied to any server run by the Iga project.
- **The AI model you chose.** To sort mail, Iga sends a short extract of each
  message to the AI provider configured on your machine: the sender, the
  subject, up to 200 characters of the body, the snippet, existing labels and
  whether there is an attachment. Which provider receives this is your choice,
  and that provider's own terms apply to it.
- **Nowhere else.** The Iga project has no servers, no analytics and no
  telemetry. The maintainers cannot see your mail, your tokens or your usage.

## Google API Services User Data Policy

Iga's use and transfer of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements. Google user data is used only to provide
the mail features you asked for, is not sold, is not used for advertising, and is
not used to train generalized AI models by the Iga project.

## Keeping and deleting data

Iga keeps your tokens until you remove them. To revoke access, delete the
credential files from your machine and remove the app at
<https://myaccount.google.com/permissions>. Labels and filters Iga created stay
in your mailbox until you delete them in Gmail.

## Contact

Questions about a specific installation go to the person who operates it. For
the software itself, open an issue at
<https://github.com/pmioduszewski/iga-assistant/issues>.
