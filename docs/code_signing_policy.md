# Code signing policy

The Windows files are to be signed with a certificate that a non-profit signing service issues
to open-source projects, so Windows can tell a genuine download from a tampered one. This page
says what is signed, where the files come from, who may request a signature, and what the app
does with your data.

## What is signed

| File | Platform | Signed |
|---|---|---|
| `teachers-teammate-<version>-w64-setup.exe` (the installer) | Windows | yes, once the certificate is in place |
| `Uninstall.exe` (installed next to the app) | Windows | yes, once the certificate is in place |
| `teachers-teammate.exe` (the program) | Windows | yes, once the certificate is in place |
| `teachers-teammate-<version>-macos.dmg` | macOS | no — not signed or notarized |
| `teachers-teammate_<version>_amd64.deb` | Linux | no — see below |

**Windows — not signed yet.** Until the certificate is in place, Windows shows "Windows protected
your PC": click **More info → Run anyway**. Only the three files above will be signed; the
libraries inside the app folder (`_internal/`: Python, Qt, OpenCV, …) are published by their own
authors.

**macOS** builds are not signed or notarized: allow the app under **System Settings → Privacy &
Security → Open Anyway**.

**All platforms:** every release has a `SHA256SUMS` file, and every installer a GitHub
build-provenance attestation for the workflow run that built it — see *Checksums and provenance*
in the [deployment guide](deployment.md).

## Where the files come from

- GitHub Releases is the only source. A download from anywhere else is not ours.
- Only CI builds of tagged commits are submitted for signing: `release.yml` builds them on
  GitHub-hosted runners from the tag's source. No self-hosted runner or maintainer machine takes
  part.
- Workflow files are reviewed like source code before they are merged.

## Who may request a signature

| Role | Who | What it means |
|---|---|---|
| Author | the maintainer | writes the change and the release |
| Reviewer | the maintainer | reads the workflow and the installer script before they are merged |
| Approver | the maintainer | approves each signing request by hand in the signing service's web page |

One person holds all three roles. Every account involved (GitHub, signing service) uses
two-factor authentication, and every release needs a manual approval, so pushing a tag alone
cannot produce a signature.

## Privacy statement

What this app does with your data (not a statement of the signing service):

- **No telemetry:** no analytics, crash reporting or usage statistics, no profile of you or your
  documents.
- **Update check:** once per start the app asks GitHub (`api.github.com`) for a newer release.
  The request carries nothing about you or your documents. Turn it off under **Settings →
  General** (from the next start), on the installer's second page, or with `/NOUPDATECHECK` for a
  silent install; an update keeps the choice.
- **Online AI services** are contacted only after you set one up and confirm a run; the provider,
  model and text are shown before you confirm.
- **Ollama** is contacted only at the address you chose (by default this computer).
- **Models and add-ons** are downloaded only when you ask, from the address shown next to the
  download.
- **Documents, settings and results** stay on your computer unless you confirm a run with an
  online service.

## Reporting a problem

A download without a valid signature, or a file claiming to be Teacher's Teammate from outside
the releases page: please open an issue at
<https://github.com/JoHoenk/teachers-teammate/issues>.
