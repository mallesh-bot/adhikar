# Conversation design (draft before writing any agent code)

Six turns, the shape every demo run should follow:

1. **User** states their situation in plain language (occupation, city/state,
   the problem they're trying to solve).
2. **Agent** asks exactly one clarifying question -- almost always
   occupation + state is enough to pick a category; ask a second only if
   the first answer still leaves more than one plausible category.
3. **User** answers.
4. **Agent** calls `search_schemes`, gets candidates back, filters against
   what it's heard, and replies with up to 4 schemes: name, one line on why
   it matches, apply link.
5. *(Branch, for the demo's "responsible AI" beat)* If the situation implies
   a sensitive attribute (disability, income band, caste category), the
   agent asks for explicit consent before calling `record_consent` and
   retrying `search_schemes` with that attribute included.
6. **Agent** offers one natural follow-up ("want me to check anything
   family-related?") and calls `log_interaction` with the matched category.

Two concrete example situations to script for the demo video:

- "I deliver food on my bike in Bengaluru, I don't have any insurance."
  -> PMSBY, e-Shram, Karnataka Gig Workers Act.
- "I'm a tailor working from home, I want a loan to buy a better machine."
  -> PM Vishwakarma, Mudra Yojana.

Keep this file updated as the real conversations you test start to diverge
from this script -- that's the point of drafting it first.
