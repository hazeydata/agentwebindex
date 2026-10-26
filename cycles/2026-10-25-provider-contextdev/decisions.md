# AWI-PROVIDER-CONTEXTDEV-001: context.dev vendor confirmation flip (R23, AC27b-d)

This PR records the vendor's written request and opens the context.dev live gate. It is flip-only (R13/AC24e): the confirmation fields in bench/config.yaml and bench/tos_matrix.yaml for contextdev, this file and the evidence file. It does not merge on its own; it is routed to Kitty and Weatherman. Stacked on hazeydata/agentwebindex#27.

## Record
- date: 2026-10-25 (post time 2026-10-25T04:27:40Z = 00:27:40 ET).
- sender: @mynameisyahia (Yahia Bakour, Founder and CEO of Context.dev).
- quote (verbatim from the saved API read): "@HazeyDataFred Hello! Can you add https://t.co/TjSwtUVs0K as well?"
- What it is: the vendor's CEO **asked to be added** to the weekly benchmark, in a reply to HazeyData's post 2114114836904690156. The word "test" is not in the post.
- evidence_path: cycles/2026-10-25-provider-contextdev/evidence/posts-api.json.txt, a byte-for-byte copy of fact's saved public X API read (posts-api.json.txt in fact's vendor-x evidence folder; SHA256 7a195d696344624a58cb001a8296481d9bbe42b0a253d9737741e17dbb155da2, listed in that folder's SHA256SUMS). The post itself could not be archived (archive.org HTTP 523), so the saved API read is the evidence.
- recorded_pr: this PR.

## Fact-check (AC27b passed)
- Fact's file: fact-check-lucille.md in the vendor-x evidence folder (SHA256 a32245986d79817824dad0395ed797928aa26d14bfaf7ec1a2a5c14b57e8e7b5).
- Officer role from a non-X source: the vendor's author page says "Yahia Bakour is the founder and CEO of Context.dev", with JSON-LD sameAs https://x.com/mynameisyahia. Archive snapshot: https://web.archive.org/web/20261026113459/https://www.context.dev/authors/yahia-bakour
- Every t.co link in the post and the profile resolves to context.dev (checked independently by fact and by chief, 2026-10-26, no cookies): t.co/TjSwtUVs0K and t.co/lqMcRkRhm1 -> www.context.dev/; t.co/KQex6aZnDX -> link.context.dev/xp -> www.context.dev/.
- Residual risk (from fact): blue verification is paid, not identity-vetted; the role rests on the vendor's own site; the post has no third-party archive; it is a solicited inclusion request in HazeyData's own thread.
