# Gmail setup: your own Google project

Iga reads and labels Gmail through a Google Cloud project that **you** own.
There is no shared Iga project and no hosted service, so your mail and your
tokens stay on your machine.

- **Who does this:** one technical person, once per household or team.
- **Time:** about 20 minutes.
- **You need:** a Google account to own the project (a personal Gmail is fine)
  and a GitHub account.
- **Everyone else** only does [step 8](#8-connect-a-gmail-account), which takes
  a minute per account.

All pages below are in the Google Cloud console,
<https://console.cloud.google.com>. Menu names are written the way the console
shows them.

## Why the project must be published

A new Google project starts in *Testing*. In Testing, Google expires every Gmail
token after **7 days**, so you would sign in again every week. Publishing the
project (step 7) removes that limit. Google's verification review is **not**
needed for personal use under 100 users. The only effect of skipping it is a
warning screen during sign-in (step 8).

## 1. Create the project

1. Open <https://console.cloud.google.com/projectcreate>.
2. Look at the avatar in the top right corner. It must be the account that
   should **own** the project. Switch accounts there if it is not.
3. **Project name:** any name, for example `Iga Mail`.
4. **Organisation / Parent resource:** a personal account shows
   *No organisation*. If the form offers your company's organisation instead,
   you are signed in with a work account, and your employer would own the
   project. This cannot be changed later, so switch accounts now if that is not
   what you want.
5. Press **Create**.

You should see the new project name in the project picker at the top left. If
not, pick it there. Every following step happens inside this project.

## 2. Enable the Gmail API

1. Open <https://console.cloud.google.com/apis/library/gmail.googleapis.com>.
2. Press **Enable**.

You should see the Gmail API page reload with **Status: Enabled**.

## 3. Create the consent screen

1. Open <https://console.cloud.google.com/auth/overview>.
2. Press **Get started**. A four-step form opens. Press **Next** after each step.

| Step | Field | What to enter |
|---|---|---|
| App information | App name | A neutral name such as `Iga Mail`. People see it on Google's sign-in page. |
| App information | User support email | Pick your address from the list. |
| Audience | User type | **External**. (*Internal* exists only for Google Workspace organisations and only lets members of that organisation sign in.) |
| Contact information | Email addresses | Your address again, then press Enter. |
| Finish | Checkbox | Read the Google API Services User Data Policy and tick the box. |

3. Press **Continue**, then **Create**.

You should see a left menu with **Branding**, **Audience**, **Clients** and
**Data access**.

## 4. Declare the scopes

Scopes are the permissions Iga will ask for.

1. Open **Data access** in the left menu.
2. Press **Add or remove scopes**. A panel opens on the right.
3. Scroll that panel to the bottom, to **Manually add scopes**.
4. Paste this one line into the box:

   ```
   https://www.googleapis.com/auth/gmail.modify, https://www.googleapis.com/auth/gmail.settings.basic, https://www.googleapis.com/auth/userinfo.email
   ```

5. Press **Add to table**, then **Update**. The panel closes.
6. Press **Save** at the bottom of the page.

You should see one entry under *Your non-sensitive scopes* (`userinfo.email`)
and two under *Your restricted scopes* (`gmail.modify`, `gmail.settings.basic`).

| Scope | What it lets Iga do |
|---|---|
| `gmail.modify` | Read mail, add and remove labels, archive, delete. |
| `gmail.settings.basic` | Create and list Gmail filters. |
| `userinfo.email` | Learn which address signed in, so a token is never saved under the wrong account. |

## 5. Create the Desktop client

1. Open **Clients** in the left menu and press **Create client**.
2. **Application type:** `Desktop app`.
3. **Name:** any name, for example `iga-email`. Only you see it.
4. Press **Create**. A dialog opens.
5. Press **Download JSON**, then close the dialog.

You should see the new client in the list, with type *Desktop*.

**Store the downloaded file safely.**

- Move it out of your Downloads folder, for example to
  `~/.local/share/iga-email/oauth-client.json`, and make it readable only by you
  (`chmod 600` on macOS and Linux).
- Do **not** put it in the `credentials/` folder next to it. That folder holds
  one token file per account, and Iga treats every file in it as an account.
- Never commit it, paste it into a chat, or send it over an open channel.

What the file is: it identifies your app to Google. It does not open any mailbox
by itself, because every mailbox owner still has to sign in and press Allow. But
someone who holds it can pose as your app, so keep it private.

## 6. Publish three public pages

Google will not let you publish (step 7) until the consent screen links to a
home page, a privacy policy and terms of service. If you open **Audience** now,
the **Publish app** button is greyed out with a note about the Branding page.

You do not need to buy a domain. A free GitHub Pages address works, and the Iga
repository already contains the three pages, written so that anyone can reuse
them unchanged (`docs/index.md`, `docs/privacy.md`, `docs/terms.md`).

**Put the pages online:**

1. Fork the Iga repository on GitHub.
2. In your fork, open **Settings**, then **Pages**.
3. Under **Source** choose *Deploy from a branch*. Branch `main`, folder `/docs`.
   Press **Save**.
4. Wait about a minute, then open these three addresses in a browser and check
   that each one loads:
   - `https://<your-github-user>.github.io/<your-fork-name>/`
   - `https://<your-github-user>.github.io/<your-fork-name>/privacy`
   - `https://<your-github-user>.github.io/<your-fork-name>/terms`

**Link them in the Google console:**

1. Open **Branding** in the left menu.
2. Scroll to **Authorised domains** and press **Add domain**.
3. Enter `<your-github-user>.github.io` (no `https://`, no path).
4. Scroll up to **App domain** and fill the three fields with the three
   addresses from above: home page, privacy policy, Terms of Service.
5. Press **Save** at the bottom.

You should see the message *Branding changes saved*.

This domain is only a list of addresses Google will accept in those three
fields. Iga never sends mail traffic through it. A Desktop client signs in
through a temporary address on your own computer and talks to Gmail directly.

## 7. Publish

1. Open **Audience** in the left menu. **Publish app** is now clickable.
2. Press **Publish app** and confirm.

You should see **Publishing status: In production** and a **Back to testing**
button. *Back to testing* undoes this step.

A yellow banner saying *Your app requires verification* also appears. That is
expected. For personal use under 100 users you can leave it as it is.

The one-time setup is done.

## 8. Connect a Gmail account

This is the only part each person repeats, and the only part a non-technical
person ever sees.

**The easy way:** with Iga running, say *"connect my Gmail account
`<address>`"*. Iga asks a yes/no question before each account, so you can first
bring the browser window where that account is signed in to the front.

**From a terminal**, the same thing is one command per account:

```
iga-mail auth --account <address> --client-secrets ~/.local/share/iga-email/oauth-client.json
```

**In the browser that opens:**

1. Pick the account Iga asked for.
2. Google shows **"Google hasn't verified this app"**. This is expected for an
   app you host yourself. Click **Advanced**, then **Go to `<app name>` (unsafe)**.
3. Click **Allow**. If Google lists the Gmail permissions with checkboxes, tick
   them first.

The page then says the account is connected.

**If you picked the wrong Google account**, the page says *Wrong Google account*
and names both addresses. Nothing is saved. Run the same command again and pick
the right account.

| Option | When to use it |
|---|---|
| `--no-browser` | Prints the sign-in link instead of opening a browser, so you can paste it into a specific browser profile. |
| `--client-secrets <file>` on an already connected account | Moves that account to a different Google project. |

When all accounts are connected, restart the email MCP server so it loads the
new tokens.

## When you will have to sign in again

Publishing removes the 7-day limit, not every limit. Google still drops a token
when:

- you change that account's password,
- you remove the app at <https://myaccount.google.com/permissions>,
- the token has not been used for six months.

In each case, connect that one account again (step 8).
