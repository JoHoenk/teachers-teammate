# Code signing policy

Teacher's Teammate is a free, open-source program for teachers. Its Windows installers are
signed with a certificate that a non-profit signing service issues to open-source projects, so
that Windows can tell a genuine download from a tampered one. This page says what is signed,
where the files come from, who may ask for a signature, and what the app does with your data.

## What is signed

| File | Platform | Signed |
|---|---|---|
| `teachers-teammate-<version>-w64-setup.exe` (the installer) | Windows | yes, once the certificate is in place |
| `Uninstall.exe` (installed next to the app) | Windows | yes, once the certificate is in place |
| `teachers-teammate.exe` (the program) | Windows | yes, once the certificate is in place |
| `teachers-teammate-<version>-macos.dmg` | macOS | no — not signed or notarized |
| `teachers-teammate_<version>_amd64.deb`, `teachers-teammate-<version>-linux-x86_64.tar.xz` | Linux | no — see below |

**Windows — signing: not yet applied for.** The certificate is requested from a signing service
for open-source projects; until it is granted, Windows shows the "Windows protected your PC"
message and you click **More info → Run anyway**. The three files above are the only ones that
will carry a signature. Nothing inside the app folder (`_internal/`) is signed: those are
libraries from other projects (Python, Qt, OpenCV), each published by its own authors.

**macOS** builds are not signed with an Apple Developer certificate and not notarized, so macOS
asks you to allow the app under **System Settings → Privacy & Security → Open Anyway**.

**Linux** packages are not signed. Every release ships a `SHA256SUMS` file next to the
downloads, and every installer carries a build-provenance attestation that GitHub issues for the
workflow run that produced it — see *Checksums and provenance* in the
[deployment guide](deployment.md).

## Where the files come from

GitHub Releases is the only place the files come from. A download from anywhere else is not ours.

Only CI builds of tagged commits are submitted for signing: the `release.yml` workflow builds
them on GitHub-hosted runners, from the source of the tag, and nothing else is ever sent to the
signing service. No self-hosted runner takes part in a Windows build, so the machine that
produces the file is GitHub's, not a maintainer's. The workflow files are reviewed like source
code before they are merged.

## Who may request a signature

| Role | Who | What it means |
|---|---|---|
| Author | the maintainer | writes the change and the release |
| Reviewer | the maintainer | reads the workflow and the installer script before they are merged |
| Approver | the maintainer | approves each signing request by hand in the signing service's web page |

One person holds all three roles, as in other small open-source projects. Every account involved
— on GitHub and at the signing service — is protected by two-factor authentication, and every
release needs a manual approval, so a signature cannot be produced by pushing a tag alone.

## Privacy statement

This is what the app does with your data. It is written for this app; it is not a general
statement of the signing service.

- **No telemetry.** The app has no analytics, no crash reporting, and no usage statistics. It
  does not create a profile of you or of your documents.
- **The update check.** Once per start the app asks GitHub (`api.github.com`) whether a newer
  release exists, and shows a note if one does. The request carries nothing about you or your
  documents beyond the request itself. You can turn it off under **Settings → General**; the
  change takes effect at the next start. The installer's welcome page says so before anything is
  installed.
- **Online AI services** are contacted only after you have set one up in the app and confirmed
  the run that sends text to it. Which provider, which model and which text is sent is shown
  before you confirm.
- **Ollama** (the local AI program) is contacted only at the address you chose — by default the
  one on your own computer.
- **Models and add-ons** are downloaded only when you ask for them, from the address the app
  names next to the download.
- **Your documents, settings and results** stay on your computer. Nothing is uploaded unless you
  turn on an online service and confirm a run.

## Reporting a problem

If a download does not carry a valid signature, or you find a file that claims to be Teacher's
Teammate but is not from the releases page, please open an issue:
<https://github.com/JoHoenk/teachers-teammate/issues>.
