# Agent handoff

This is out_of_news (Out of News), an independent Persian daily-news Telegram project
The last user-provided channel handle is @khabar_ara; a new handle is not yet
confirmed. Read README.md and
 docs/product.md before implementation. The original in-the-ai repository is
background experience, not this project's live runtime or database.

## Accepted requirements

- Public Telegram news output, exactly THREE sentences per news body.
- No daily post-count cap has been accepted; do not impose the proposed eight
  posts/day or one-hour spacing. Polling cadence is still undecided.
- Exercise caution with insulting or derogatory material about religion, faith,
  the government and leaders of the Islamic Republic of Iran. This includes
  implications introduced by literary devices. Hold ambiguous or insulting
  drafts for review; preserve the selected devices during revision. Critical
  reporting alone is not prohibited. Do not distort facts or quotations or
  represent this editorial preference as legal assurance.
- Language enrichment through natural vocabulary and literary devices in prose.
- Randomly choose two or three DISTINCT devices from the configured list, without
  compatibility filters, weighted suitability, replacement or rerolling.
- Preserve and record the exact chosen devices for each draft; rewriting the same
  news must keep its selection. Do not quietly omit difficult devices.
- No educational appendix, device labels, explanations or word definitions in
  public posts. Put source links outside the three-sentence body.
- Preserve source facts, attribution and uncertainty. Literary language must not
  invent factual claims, quotations, accusations or personal experiences.
- Every paragraph must begin with a Persian word for Telegram RTL readability.
- Keep tokens, .env, SQLite data, backups and generated outputs outside Git.
- Use separate credentials, channel binding, database and eventual scheduler.
  Never use in-the-ai credentials or modify its live service/database for this
  project. Do not publish or schedule before this project's destination is set.

## Current stage

Scaffolding only. Confirm pending product choices in docs/product.md as needed;
record accepted changes durably. Do not inherit the other bot's news sources,
model, reasoning setting, cadence or editorial style without a decision here.
The preliminary device list contains user-named examples, not a final taxonomy.
