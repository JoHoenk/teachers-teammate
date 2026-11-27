# Privacy

What the app does with your data:

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

Where a service is contacted, its own privacy policy applies:

- GitHub (update check):
  [privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)
- Hugging Face and Ollama (models you download): [Hugging Face](https://huggingface.co/privacy),
  [Ollama](https://ollama.com/privacy)
- PyPI (add-on packages you install): [privacy notice](https://policies.python.org/pypi.org/Privacy-Notice/)
- Online AI services: the policy of the provider you set up.
