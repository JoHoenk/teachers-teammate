"""The one-request proofreading path: the evaluation baseline.

The app proofreads through the exchange
(:mod:`teachers_teammate.infrastructure.proofreading`), which asks three times per text.  This
package keeps the single review request the app used before, because every saved evaluation run
was measured with it: the correctors (:mod:`.correction`), the answer schema
(:mod:`.correction_schema`), the answer formats (:mod:`.answer_format`) and the per-language
policies (:mod:`.presets`).

Nothing under ``teachers_teammate/`` imports this package.
"""
