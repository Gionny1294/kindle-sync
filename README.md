# Kindle Sync

**Your books, your computer, your language.** A local bookshelf, paginated reader and Send to Kindle app with multiple accounts with optional password protection.

Search and read your books in a browser, send individual titles to your Kindle, or enable scheduled sending. Each account has separate books, email settings and sending history.

**Six interface languages:** 🇬🇧 English · 🇫🇷 Français · 🇪🇸 Español · 🇮🇹 Italiano · 🇨🇳 中文（简体 / Simplified Chinese） · 🇷🇺 Русский. Choose a flag on first opening and change later in **Settings → Language**. Books keep their original language.

[Install](#quick-installation) · [Updates](#update-from-kindle-sync) · [Gmail setup](#google--gmail-setup) · [Privacy](#privacy-and-network-connections) · [Troubleshooting](#troubleshooting)

## Quick installation

You only need **Docker with Docker Compose v2**. No separate Python installation or cloud hosting is required. Telegram downloads optionally require your Telegram API credentials.

1. Install and start Docker:
   - **Windows:** [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/). Complete its WSL 2 setup if prompted; use **Linux containers**.
   - **macOS:** [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/), choosing Apple silicon or Intel for your Mac.
   - **Linux:** [Docker Engine](https://docs.docker.com/engine/install/) and the [Compose plugin](https://docs.docker.com/compose/install/linux/). Your user must be allowed to run Docker.
2. Download **`kindle-sync-local.zip`** from the latest release. Extract the whole ZIP into a permanent, writable folder. Avoid cloud-synced folders if you want all app data to remain on your computer.
3. Start the extracted copy:

   | System | Start |
   | --- | --- |
   | Windows | Double-click **`Start.cmd`**. |
   | macOS | Double-click **`Start.command`**, or open Terminal in the folder and run `sh scripts/start.sh`. |
   | Linux | Open a terminal in the folder and run **`sh scripts/start.sh`**. |

4. The launcher prepares storage, builds the app, starts both containers and opens **[http://localhost:1997](http://localhost:1997)**. The first build downloads document converters and may take several minutes. Later starts reuse those downloads.
5. Keep Docker running while using the app. Closing the browser or terminal does not stop the worker. Run `docker compose stop` in the installation folder to stop everything.

Run from the extracted folder, not inside the ZIP. The release contains source code and launchers, rather than a signed native `.exe` or macOS app. Your system may ask you to review downloaded scripts: inspect the launcher and use the normal file-approval process if needed. On macOS, the Terminal command is available if double-clicking is blocked.

For later visits, use the same launcher or open `http://localhost:1997` if already running. Verify downloads against the matching `.sha256` release asset: `Get-FileHash .\kindle-sync-local.zip -Algorithm SHA256` on Windows, `shasum -a 256 kindle-sync-local.zip` on macOS, or `sha256sum kindle-sync-local.zip` on Linux.

## First opening and accounts

1. Choose your language using the flags.
2. Create an account with a name and an optional password. There is no minimum password length (maximum 256 characters). Leave it blank to open that account without a password. This is separate from your email app password.
3. Choose **reading only** without email, or enter your **Kindle destination**, **sender email**, **provider** and **email app password** using the instructions below.
4. Approve your sender in Amazon, select the approval checkbox, then **Save and verify**. This checks TLS and SMTP authentication without sending a book. Leave automatic sending off unless you want it.
5. Upload a book. **Read** opens it locally; **Send now** sends that title through email.

A valid session reopens your account automatically and keeps the requested page or book. When signed out, choose an account without a password, sign into a protected account, or create another one. Select **Remember me on this device** to stay signed in for 30 days; leave it off for a browser session limited to 12 hours. **Sign out** revokes the saved session. Use this option only on a personal device. Use **Settings → Switch account** or **Sign out** later. In **Settings → Change account password**, enter the current password if set, then save a new one. Leave the new password and confirmation empty to remove password protection: the account will then open immediately when clicked. Saving signs out existing sessions for that account. The host administrator can access underlying files; profiles are not separate operating-system security boundaries.

To delete the current account, open **Settings → Delete account** after signing in, enter its current password if protected, then choose **Permanently delete account**. This removes the profile, uploaded books, email credentials, settings and history, cancels pending sends and signs out its sessions on all devices. Wait for any active operation to finish before retrying. Original books in the account’s library folder remain on disk; other accounts keep their own data. A new account starts empty after the installation’s first account has been deleted. Existing backups are unchanged.

## Authorize your sender in Amazon

| Field | What to enter |
| --- | --- |
| Kindle destination | Your device's **Send to Kindle email address**, usually ending in `@kindle.com`. |
| Sender / SMTP username | Your complete outgoing email address. |
| Email app password | A password generated by that provider for Kindle Sync, where supported. |

1. Sign in to the Amazon account linked to Kindle and open **[Manage Your Content and Devices](https://www.amazon.com/mycd)**. Use your local Amazon store if registered elsewhere.
2. Open **Preferences → Personal Document Settings → Send-to-Kindle E-Mail Settings**. Copy the address for your device.
3. Under **Approved Personal Document E-mail List**, add the **complete sender email address** you will enter in Kindle Sync. Approve each sender used by your accounts.
4. Enter those addresses in the app, confirm sender approval and verify email settings.
5. Send a small book first. Connect Kindle to Wi-Fi and check the sender's inbox for any confirmation Amazon requests.

**SMTP accepted** means the email provider accepted the message; Kindle delivery is not confirmed. Amazon can request verification or reject a document later. See [Amazon sender authorization](https://www.amazon.com/gp/help/customer/display.html?nodeId=GX9XLEVV8G4DB28H) and [Send to Kindle by email](https://www.amazon.com/gp/sendtokindle/email).

## Google / Gmail setup

Kindle Sync uses SMTP with an **app password**, not a “Sign in with Google” consent screen.

1. Sign in to the Google account you want to send from.
2. Open **[Google Account → Security](https://myaccount.google.com/security)** and enable **2-Step Verification** if needed.
3. Open **[App passwords](https://myaccount.google.com/apppasswords)**, authenticate if requested, create one named **Kindle Sync**, and copy the generated 16-character password.
4. Choose **Gmail** in the app. Enter your full address and paste that app password without display spaces. The preset uses `smtp.gmail.com`, port `465`, direct TLS; port `587` with STARTTLS is also supported.
5. Approve that sender in Amazon and select **Save and verify**.

Use the generated password, not your normal Google password. No Gmail API project or IMAP access is needed. App passwords may be unavailable with organization policy, Advanced Protection or security-key-only verification. Use an eligible account or another SMTP provider. Changing your Google password revokes app passwords; generate a replacement and update the app. Revoke access anytime in Google Account settings.

References: [Google app-password help](https://support.google.com/accounts/answer/185833?hl=en) · [Google SMTP settings](https://developers.google.com/workspace/gmail/imap/imap-smtp).

## iCloud, Yahoo and other providers

| Provider | SMTP host | Port | Encryption | Authentication |
| --- | --- | --- | --- | --- |
| Gmail | `smtp.gmail.com` | `465` | Direct TLS | Full address + Google app password |
| iCloud Mail | `smtp.mail.me.com` | `587` | STARTTLS | Full iCloud address + app-specific password |
| Yahoo Mail | `smtp.mail.yahoo.com` | `465` | Direct TLS | Full address + Yahoo app password |
| Other | Provider's SMTP host | Usually `465` or `587` | Direct TLS or STARTTLS, respectively | Provider-supported SMTP credentials |

**iCloud:** enable two-factor authentication for your Apple Account. At **[account.apple.com](https://account.apple.com/)** open **Sign-In and Security → App-Specific Passwords**, generate one for Kindle Sync, and use your full iCloud Mail address. See [Apple app-specific passwords](https://support.apple.com/102654) and [iCloud SMTP settings](https://support.apple.com/102525).

**Yahoo:** open **[Yahoo Account Security](https://login.yahoo.com/account/security)**, find the option to create an app password, generate one for Kindle Sync and use the Yahoo preset. Menu labels and availability vary by account. See [Yahoo app passwords](https://en-global.help.yahoo.com/kb/new-yahoo-mail/generate-password-sln15241.html) and [server settings](https://help.yahoo.com/kb/sln4724.html).

**Other providers:** choose manual configuration and enter their SMTP host, port, encryption, username and supported password. This release supports password-based SMTP over TLS, not OAuth-only login. Some Microsoft or organization accounts require OAuth; use another compatible SMTP account in that case. Keep provider security protections enabled.

The email app password is separate from your app login. Each account can use a different sender and recipient. Change later in **Settings → Configure recipient and sender**. Saved email passwords are not displayed or returned to the browser.

## Books and reading

Search names and subfolders; filter by folder or format. **Read** never sends a book. Use **Send now** individually or select multiple titles. Switch automatic sending off for individual books. Reader controls include page navigation, a table of contents, fonts and day/sepia/night themes.

| Format | Reading | Sending |
| --- | --- | --- |
| EPUB | Paginated text, images, internal links | Original |
| PDF | Original pages and zoom | Original |
| TXT, HTML/HTM | Text / sanitized HTML | Original |
| DOCX, RTF | Local EPUB conversion | Original |
| DOC | Locally extracted text | Original |
| MOBI, AZW, AZW3, FB2, ODT | Local conversion with Calibre | Converted EPUB |
| CBZ | Comic pages | Converted EPUB |

DRM-protected or damaged files cannot be read or converted. Conversion can take up to 90 seconds. Reflowable page counts change with screen and font; PDF numbering stays fixed. Reading positions are saved per account in the same browser/device and do not sync with Kindle.

Uploads are limited to **45 MB**. Email providers can impose lower limits, including encoded-message overhead: a file that uploads successfully may still be too large to send. Amazon's supported formats and delivery rules also apply.

The first account owns the installation's `library/` folder. Put books there or upload them. Other profiles start empty and can upload private books. Administrators can list account IDs with `docker compose exec app python -m app.account_admin list`; private folders are `data/users/ACCOUNT-ID/library/`.

Use **Bookcase** to arrange books in separate bookcases. Hold a book or object to move it, or select **Rearrange books**. A short press opens the linked file or the book's details. To show a book spine, photograph it or choose an existing photo and crop it. Use **Objects** to add a plant, building bricks, a bookend or your own photo on a stand.

**To read** is a separate list. Save titles from Files, Recommended or Downloads. Kindle Sync links titles to EPUB metadata or unambiguous file names. Open or send a linked file from the list, or download an available edition through Telegram. When marking a book as read, you can give it a personal rating from 1 to 5 in half-point steps. Book details show the date added and the finish date when recorded.

To bring over your Goodreads library, export it as a CSV and choose **Settings → Import from Goodreads**. The import uses your Goodreads shelves, reading status and available dates and ratings. Your personal rating is shown separately from the Goodreads community average.

## Recommendations and optional Telegram downloads

Open **Recommended** to see books published today or yesterday and an overall top 10. The overall chart combines rankings from enabled sources including IBS, Feltrinelli, Mondadori and Goodreads. ISBNs and normalized titles and authors identify duplicate editions. Cards display the cover, publication date, available reader ratings and links to the sources. Expand a synopsis to read more when the source provides one. The worker scans at 00:00, 06:00, 12:00 and 18:00 in Europe/Rome local time. Choose **Refresh now** to scan manually. If a source fails, its previous results retain their original timestamp.

Manage sources and see their status in one list under **Settings → Sources**. Enable, disable, remove or add up to 12 HTTPS sources. Supported inputs are IBS/Feltrinelli/Mondadori charts and new-release lists, Goodreads book lists, pages exposing schema.org `Book` records, and book-oriented RSS/Atom feeds. Arbitrary article pages may not provide extractable book data; the source status explains failures. Charts and sources represent their own audiences, not a universal sales ranking.

Open **Downloads** to search available catalogues by **title, author or ISBN**. Choose **Photograph ISBN** to scan a barcode or select an existing photo. The browser reads the photo locally and fills in the ISBN. Select **Search** to see matching books. Results can show a cover, reader rating and synopsis when the source provides them. Select **Download** on a result to look for a matching edition through your connected Telegram bot. Catalogue browsing is available before connecting Telegram.

**Download books** is optional. **Settings → Download language** defaults to **Same as app language (automatic)**. You can choose Italian, English, French, Spanish, Chinese or Russian independently of the interface; the setting is saved per account and survives Telegram disconnect/reconnect. Changing the effective download language clears old results, and download tokens are bound to the language used for that search. Enter your Telegram **API ID and API hash** from [my.telegram.org/apps](https://my.telegram.org/apps), your book bot username and your international phone number in the app, then enter Telegram's login code and two-step verification password if requested. The integration uses your Telegram account to send the selected title and author to that bot. It recognizes editions explicitly identified by the bot in the chosen language, prefers **EPUB**, then chooses the **smallest file** within that preference. Only supported book files up to 45 MB are accepted; an EPUB declaring a different language from the selection is rejected. Bot response formats vary: if no compatible edition in the selected language is recognized, the app shows a message and a link to the bot. A live bot compatibility check requires your connected Telegram account.

Choose an account’s download subfolder in **Settings → Download folder**. Kindle Sync creates it if needed. Leave the field blank to use the root of the mounted download folder. Downloads go to the host folder set by `BOOK_DOWNLOAD_HOST_PATH` in `.env` (default `./library/Singoli`), mounted at `/book-downloads` for the web app. Create a custom folder before restarting and give the app user write access. After editing `.env`, run the usual launcher to apply the setting. Existing files are preserved; identical same-named files are deduplicated and different editions get a filename suffix. Downloads appear immediately in the current account’s library. The usual automatic-send preference applies. `TELEGRAM_BOOK_BOT` can prefill the bot username. Downloaded files in this host folder are shared installation files and are preserved when an app account is deleted. The first local account can explicitly delete a downloaded file from the shared folder from its library entry.

Recommendation sources, caches and Telegram credentials/session keys are stored per account in the local database. Telegram secrets are never returned to the browser; protect and back up the database like SMTP credentials. Disconnect revokes the Telegram session when reachable; deleting an app account removes its local credentials and caches. Your host administrator can access this data.

## Privacy and network connections

**No developer-operated cloud service, analytics, tracking or telemetry.** Books, accounts, email settings, sending history and converted copies live on the host computer. Reading, conversion and cover extraction are local. Translations, flags and PDF.js are bundled; reading needs no CDN or translation service.

| Action | Where data goes |
| --- | --- |
| Read, search or convert | Between your browser and your local installation. |
| Use another home-network device | Between that device and your host over the local network. |
| Verify email settings | Your chosen SMTP provider receives a TLS-authenticated connection; no book is sent by this test. |
| Send manually or enable automatic sending | Documents, sender and recipient go through your email provider to Amazon's Send to Kindle service under their policies. |
| Install or rebuild | Docker downloads runtime packages and dependencies from registries. |
| Check for or install an app update | The host requests the public release metadata and archive from GitHub. |
| Load recommendations | The host fetches public book pages and cover images from the enabled sources. |
| Search the Downloads catalogue | The host sends title, author or ISBN queries to Open Library and supported enabled catalogue providers. It fetches cover images and book details from public sources. |
| Connect Telegram / download a book | Telegram receives authentication requests and the chosen title/author sent to your configured bot. The selected file is downloaded to the host. |
| Open a help link | Your browser visits that external website. |

After installation, local reading works without internet while the host and containers remain running. Sending requires internet. A document leaves the computer when you choose to email it.

By default the app listens only on **`127.0.0.1`**. Accounts with a password use salted scrypt hashes; accounts created with an empty password are accessible to anyone who can reach the app; cookies are HttpOnly/SameSite; changes require CSRF protection; SMTP uses TLS with certificate verification. Containers run as a non-root user with a read-only root filesystem and restricted capabilities. These protections reduce risk; they do not guarantee immunity from vulnerabilities.

**Storage is not encrypted by Kindle Sync.** Email app passwords must be recoverable for sending and are stored in local configuration files with restrictive permissions. Protect the host account, disk and backups. Your OS, backup utility or cloud-synced folder may copy files elsewhere independently of this app. Profile names appear on the chooser; the host administrator can access data and reset accounts.

## Optional phone / home-network access

Keep the host on and Docker running. To allow devices on a **trusted home network**, run from the installation folder:

```sh
# macOS / Linux
sh scripts/start.sh --lan
```

```powershell
# Windows PowerShell
.\Start.cmd -Lan
```

Open `http://YOUR-COMPUTER-IP:1997` on the other device. Find the private IP in the host's network settings (`ipconfig` on Windows, `hostname -I` on Linux). Allow the port through the firewall for private networks if needed. Do not forward it on your router. LAN mode uses HTTP: browser-to-host traffic is not encrypted, so use a network you trust.

To restore computer-only access, set `APP_BIND_IP=127.0.0.1` in `.env` and run `docker compose up -d --wait`. A browser shortcut/add-to-home-screen can open the app on a phone; it still needs the host.

## Update from Kindle Sync

Starting with v1.2.1, releases can be installed from **Settings → Updates**. Use the launcher included in the release ZIP or TAR.GZ on Linux, macOS or Windows (64-bit Intel/AMD or ARM). It starts the local update helper along with the app. After a computer restart, run the launcher again if Docker has already restarted the containers and the update panel reports that the helper is offline.

1. Open **Settings → Updates** with the first local account. The app shows the installed version and the latest available version. Choose **Check for updates** to refresh the result.
2. Choose **Install update** and confirm. Kindle Sync downloads the official archive from GitHub, checks its SHA-256 digest and builds the new image. When the image is ready, the app and worker restart. Books, accounts, mail settings and the existing `.env` stay in your installation folder.
3. Leave Docker running during the update. The page reconnects after restart. If the new services fail to start, the helper tries to restore the previous version. If Docker was shut down before recovery finished, restart Docker and run the launcher.

An update needs free disk space and enough time for Docker to build the release. Keep using the launcher to apply configuration changes to an updated installation. The update helper runs on the host without adding a Docker socket to the app. It sends requests for public release information and archives to GitHub and keeps its working files under `.kindle-updater/`.

If you are upgrading from v1.1.0 or earlier, first install v1.2.1 using the manual steps below. Installations using a custom `COMPOSE_FILE` can use the manual update steps with their own Compose setup.

## Operation, updates and backups

From the installation folder:

```sh
docker compose ps                         # Check app and worker
docker compose logs --tail=50 app worker  # Read logs locally
docker compose stop                      # Stop app and sending
docker compose start                     # Restart
```

**Back up:** stop both containers, copy **all of `data/`, `.env` and `library/`** to a private location, then start them. Backups contain books and email credentials. Rebuilding containers or `docker compose down` keeps these bind-mounted folders.

**Manual update:** back up first. Extract the new release separately, then copy its application files into your existing installation, preserving `data/`, `library/` and `.env`. Run the launcher to rebuild. Never replace your data with an empty folder.

**Uninstall:** run `docker compose down`. Delete the installation folder only if you intend to delete its books, accounts and settings. Revoke the email app password if no longer needed. Remove the Docker image through Docker Desktop if desired.

## Existing installations and password recovery

Fresh installations need **no activation code**. Existing books or settings are protected by a one-time first-owner code. Retrieve its link locally:

```sh
docker compose exec app python -m app.account_admin owner-code
```

Open the link on the host. For another address/port, replace only the address and keep `#activate=...`. The code is also in `data/account-setup-code` until used. The first account claims the old library and settings; later accounts start empty. The wizard shows the saved sender and recipient without revealing the password. The code survives a refresh in the same tab.

Forgot a local password? On the host:

```sh
docker compose exec app python -m app.account_admin list
docker compose exec app python -m app.account_admin reset "ACCOUNT NAME"
```

Enter the new password twice. That account's old sessions are revoked; books and settings remain. There is no online recovery service.

Advanced import into an empty installation is available through `python3 scripts/bootstrap.py --migrate-from /PATH/TO/OLD/DATA` before starting Docker. This optional helper needs host Python 3; normal launchers do not.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Docker not found / not ready | Install/start Docker and wait for the engine. Check Linux permissions or Windows Linux-container / WSL setup. |
| Slow first start | Leave the terminal open; converters must download. Check internet and Docker disk space. |
| Port 1997 is in use | Stop the conflicting app, or change `APP_PORT` in `.env` to an unused port such as `1998`, then run the launcher. |
| Phone cannot connect | Enable LAN mode; check private IP, firewall and same-network access. A phone's `localhost` means the phone itself. |
| Gmail password rejected | Use an app password without display spaces. Generate a replacement if your Google password changed. |
| App passwords missing | Check provider eligibility and policy; use another compatible provider if unavailable. |
| SMTP timeout / TLS failure | Check host, port, encryption, network restrictions and system clock. Keep certificate verification enabled. |
| Accepted but missing on Kindle | Check approved sender, destination, confirmation emails, Wi-Fi, provider limits and file compatibility. |
| Uncertain sending result | Check Kindle and email before retrying: an interrupted connection may have delivered it. |
| Unsupported book | Use a supported, DRM-free file that opens correctly. |
| Update not visible | Refresh the browser; after changing source, run the launcher to rebuild. |
| macOS launcher not executable | Use `sh scripts/start.sh`, or `chmod +x Start.command`. |

Use activity and diagnostics in the app. Review logs before sharing: personal file names or network information may appear. Never post `.env`, databases, passwords, activation links or books in issues.

## Release contents and third-party software

Packages contain **application source, launchers, public assets and documentation only**. No user's books, email addresses, passwords, databases, backups, private `.env` files or activation codes are included.

Conversion uses [Calibre](https://calibre-ebook.com/) and Antiword inside the container. [Mozilla PDF.js](https://github.com/mozilla/pdf.js) is bundled with its upstream license in `app/static/vendor/pdfjs/LICENSE`. Dependencies retain their respective licenses. This independent app is not affiliated with Amazon, Google, Apple or Yahoo.
