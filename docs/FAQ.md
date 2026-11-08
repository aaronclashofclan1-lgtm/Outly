# FAQ

## Is Outly free?

The application is MIT-licensed and free to use. It relies on provider APIs
that may have free tiers and limits. YouTube, your LLM provider, Exa, and Gmail
set their own quotas and prices.

## Does Outly upload my data?

No. There is no Outly account or telemetry. Requests go only to the providers
you configure. Your database and `.env` stay in the app folder.

## Why is the interface French if the documentation is English?

Version 1.0 was built for a French creator. The public documentation and
repository are English so more people can evaluate and contribute. A full UI
localization is a separate, larger change; the user guide translates every
screen and label used in the workflows.

## Does Outly find "similar channels" automatically?

YouTube has no supported similar-channel endpoint. Outly combines seeds,
keywords, your own channel, featured channels, description links, and an
optional LLM expansion. Search results can be noisy; scoring and filters remove
irrelevant candidates.

## What is an outlier?

For a channel, Outly computes the median views of its latest Shorts and long
videos separately. An outlier score is `views / median of the same format`.
There is no comparison between a Short and a long video.

## Why do quotas look different from old tutorials?

Since June 2026, `search.list` has its own bucket (100 calls/day) and other
endpoints share 10,000 units/day. Old “search costs 100 units” advice is out of
date. Outly tracks both buckets and the Pacific calendar day.

## Does it work without an LLM?

Discovery, enrichment, metrics, reports, local comparison, and parts of the
strategy/production analysis work locally. LLM scoring and most generative
features need a configured OpenAI-compatible endpoint.

## Can I use a local model?

Yes. Point `LLM_BASE_URL` to an OpenAI-compatible local server such as Ollama,
set `LLM_MODEL`, and leave `LLM_API_KEY` empty. Verify with
`python -m outly doctor --probe`.

## Where are my reports?

In `out/`: `channels.csv`, `outliers.csv`, and `report.md`. Email delivery is
optional and uses the same report content.

## Can I move the app?

Yes. Copy the whole folder, including `data/` and `.env`, to another Windows
machine. Treat the copy as sensitive. If the folder is read-only, the app uses
`%LOCALAPPDATA%\Outly`.

## Why is the exe unsigned?

Code signing requires a certificate and recurring process. The project
publishes a SHA-256 hash so you can verify the download. SmartScreen warnings
are expected; do not bypass them for a file whose hash you cannot verify.

## How do I delete my data?

Close the app and delete its folder. Also delete `%LOCALAPPDATA%\Outly` if it
was used. Revoke API keys and OAuth access at the provider. Backups elsewhere
are yours to delete.

## Can I contribute?

Yes. Read [CONTRIBUTING.md](../CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md). Do not include secrets or personal
data in issues or pull requests.
