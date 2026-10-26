# Fact-check AC27b: vendor X posts (Lucille)
Checked 2026-10-26, about 07:35 ET. Sources: public X API reads (posts-api.json.txt, profiles-api.json.txt), vendor sites, curl redirect chains.

## Post 1: x.com/mynameisyahia/status/2114212271258038294
- **Claim: the post exists.** CONFIRMED. Text: "@HazeyDataFred Hello! Can you add https://t.co/TjSwtUVs0K as well?" Posted 2026-10-25T04:27:40Z, which is **2026-10-25 00:27:40 ET (EDT)**. It replies to @HazeyDataFred's post 2114114836904690156 ("Every week we put 4 web tools to the test...").
- **Claim: every t.co link leads to context.dev.** CONFIRMED (redirect-chains.txt). t.co/TjSwtUVs0K → context.dev/ → www.context.dev/ (200). The bio link t.co/lqMcRkRhm1 follows the same chain. The profile URL t.co/KQex6aZnDX → link.context.dev/xp → context.dev/?utm… → www.context.dev/?utm… (200).
- **Claim: the author's identity.** CONFIRMED. Handle @mynameisyahia, name "Yahia Bakour", id 1229891964700512256, verified (blue, which is paid and not identity-vetted). Bio: "Founder & CEO @ Context.dev - One API to scrape, enrich, and understand the web."
- **Claim: officer role, from a non-X source.** CONFIRMED. The vendor-controlled page https://www.context.dev/authors/yahia-bakour (context-author-yahia.html) says: "Yahia Bakour is the founder and CEO of Context.dev". Its JSON-LD has "jobTitle":"Founder and CEO", worksFor Context.dev, and sameAs https://x.com/mynameisyahia. That sameAs link ties the X handle to the person. Snapshot: https://web.archive.org/web/20261026113459/https://www.context.dev/authors/yahia-bakour
- **Claim: the post explicitly asks to be tested.** CONFIRMED, as an inclusion request: "Can you add https://t.co/TjSwtUVs0K as well?" The word "test" is not in the post. "Add" refers back to the parent post about testing tools.
- **Residual risk:** The source is a blog author page on the vendor's own site, not a filing. The www.context.dev about, team and company pages return 404. @contextdotdev is a different, unrelated-looking account ("Feature Flags…"), so it was not used. The archive.org save of the post failed (HTTP 523) and no snapshot exists. The oEmbed fallback returned no body.

## Post 2: x.com/styskin/status/2114208595525144970
- **Claim: the post exists.** CONFIRMED. Text: "@HazeyDataFred Please include @KeenableAI into your tests." Posted 2026-10-25T04:13:03Z, which is **2026-10-25 00:13:03 ET (EDT)**. It replies to the same parent post.
- **Claim: the author's identity.** CONFIRMED. Handle @styskin, name "Andrey Styskin", id 1523343981983281153, verified (blue). Bio: "CEO and co-Founder at @KeenableAI the best search for AI Agents, ex-CEO Yandex Search, ex Amazon AGI". @KeenableAI (verified blue) links to keenable.ai.
- **Claim: officer role, from a non-X source.** CONFIRMED. The keenable.ai home page (keenable-home.html) shows the team block "Andrey Styskin    Co-founder & CEO    ex-CEO of Search and Ads at Yandex". Its JSON-LD has "founder":[{"name":"Andrey Styskin","jobTitle":"Co-founder and CEO","sameAs":["https://www.linkedin.com/in/andrey-styskin/","https://x.com/styskin"]}]. Snapshot: https://web.archive.org/web/20261026113537/https://keenable.ai/ . I also saved keenable.ai/about (snapshot https://web.archive.org/web/20261026113518/https://keenable.ai/about), but it does not name him, so I did not keep a local copy.
- **Claim: the post explicitly asks to be tested.** CONFIRMED: "Please include @KeenableAI into your tests."
- **Residual risk:** There is no filing or press release; the role rests on the vendor's own site. The archive.org save of the post failed (HTTP 523) and no snapshot exists. The oEmbed fallback returned no body.

## Overall residual risk
Both posts are evidenced only by API JSON and have no third-party archive. Blue verification does not vet identity. Both posts are replies to HazeyData's own thread, so they are solicited inclusion requests, not independent endorsements.
