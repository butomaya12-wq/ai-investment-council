# TTL-expiry reevaluation policy proposal v1

## Status

`config/event/decision_ttl_reevaluation_policy_competition_v1.json` is a self-hashed, inactive proposal. Its values `active=false` and `status=DRAFT_NOT_AUTHORITY` mean it grants no model, provider, broker, B5, B6, or execution authority. The merged TTL preflight remains authoritative for current behavior: `TTL_REVIEW_SCOPE_UNDERSPECIFIED`.

## Proposed TTL-only path

For `TTL_EXPIRY` with no independently active research-reopen, material-evidence-change, mandate/policy-change, or thesis-invalidation trigger, the proposed minimum semantic path is `FRESH_JUDGE_ONLY`. A fresh Judge response would create the new `INVEST`, `WATCH`, or `ABSTAIN` semantic outcome. The proposal permits one fresh Judge call at most and no automatic retry, but it does not authorize that call: owner activation, paid approval, and a cost preflight remain required.

Historical B3 closure plus B4 Initial and Rebuttal outputs are hash-bound input lineage only. They do not satisfy a new decision requirement and do not authorize skipping any future Council stage. The historical Judge output is neither semantic input nor reactivation authority, and the old TTL cannot be refreshed.

Pure TTL expiry is proposed not to require a provider/research refresh before the fresh Judge. This is not provider-read authority. Any independently active evidence or research trigger leaves the TTL-only path for its separately authorized lifecycle.

## Post-Judge boundary

A fresh `WATCH` or `ABSTAIN` does not start B5. A fresh `INVEST` would require a separately bounded and authorized fresh B5 production read, option selection, and risk cycle; historic B5 data is not execution authority. B6 remains blocked until the new decision TTL is valid, fresh B5 exists, exact human approval is granted, and commit-time revalidation passes.
