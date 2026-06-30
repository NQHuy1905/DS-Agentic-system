Before getting into the steps, the single most useful thing to internalize for an agent system is this: a senior's EDA is not a fixed checklist run top to bottom. It's a loop — look, form a hypothesis, test it, follow the smell. Juniors run df.describe(), df.info(), a correlation heatmap, and call it done. Seniors spend the first chunk of time not touching the data at all, and the rest chasing whatever looks wrong. If you model EDA as a linear pipeline, your agent will behave like a junior. If you model it as a hypothesis-driven loop with a few framing gates up front, it'll behave like a senior.
Here's the full arc.
Phase 0 — Framing (before loading anything)
This is the phase juniors skip and seniors never do. Before a single line of code, a senior establishes:

The objective. What decision or downstream system does this data feed? EDA for "train a churn model" looks different from EDA for "build a nightly aggregation pipeline." The purpose dictates what counts as a problem.
The grain. What does one row represent? One transaction? One user-day? One event? Almost every later check (duplicates, joins, aggregations) depends on knowing the intended unit of analysis.
The provenance. Where did this come from, and what process generated it? A senior mentally models the source system because most data quirks are artifacts of how data was produced, not how it was stored.
Expectations / priors. What should the data look like if everything is healthy? Roughly how many rows? What ranges? What's the expected null rate? Without priors you can't recognize an anomaly — you have nothing to be surprised against.

For a data engineer specifically, Phase 0 also includes the schema/contract question: what is the expected schema, and is this a one-off file or a recurring feed that a pipeline must handle reliably?
Phase 1 — First contact (does it even load?)
The goal here is "shape of things," not analysis. Load the data and confirm it loaded correctly — encoding, delimiter, quoting, header row, parse errors. Then: dimensions (rows × columns), column names, dtypes, and crucially a visual eyeball of actual rows (head, tail, and a random sample). Summaries lie; a senior always looks at raw values because that's where you catch things like "this 'numeric' column is actually strings with commas" or "dates are in three different formats." Dtype mismatches against expectation are the first big tell.
Phase 2 — Structural integrity & data quality
Now the systematic quality pass. This is where a DE spends real time:

Missingness — not just the count, but the pattern. Are nulls random, or concentrated in certain rows/columns/time periods? Missingness that correlates with something is a signal about the source process.
Duplicates — both full-row duplicates and key-level duplicates. The key test is: does the intended grain from Phase 0 actually hold? If user_id is supposed to be unique and isn't, something upstream is wrong.
Validity — ranges (negative ages, future dates), valid category sets, referential integrity across tables, type coercion problems, and string hygiene (whitespace, casing, encoding artifacts, unicode lookalikes).

A senior frames findings here as risks to the downstream system, not just statistics.
Phase 3 — Univariate analysis (one variable at a time)
Understand each column on its own before relating them. Numeric columns: distribution shape, center, spread, skew, and outliers — with a constant "is this plausible?" check against Phase 0 priors. Categorical columns: cardinality (a 50,000-category column is a red flag), frequency distribution, rare categories, and near-duplicate variants ("USA" vs "U.S.A." vs "United States"). Datetime columns: full range, granularity, and gaps (missing days often reveal pipeline outages). If there's a target variable, its distribution and class balance get special attention.
Phase 4 — Bivariate & multivariate
Relationships now. Feature-to-feature (correlation, collinearity), feature-to-target, conditional distributions, and grouped aggregations across meaningful segments. The senior move here is using relationships to explain anomalies found earlier — "oh, the nulls in column X only appear when column Y is a certain value, so this is a known business case, not a bug."
Phase 5 — Time, drift & leakage
Often overlooked but critical, especially for engineers. Is the data stable across time/batches, or does the distribution drift between loads? For recurring feeds this determines whether a pipeline built today survives next month. If the data feeds a model, this is also where leakage gets hunted (a feature that wouldn't be available at prediction time, or that encodes the target).
Phase 6 — Hypothesis-driven deep dives
This is the loop, and it's where most of the value and most of the judgment live. Every surprise from earlier phases spawns a "why is this weird?" investigation. The senior's actual skill is choosing what to look at next based on what they just saw — they don't re-run the whole battery, they zoom in. This phase has no fixed length and is the hardest to encode in an agent.
Phase 7 — Synthesis & handoff
EDA isn't done when the looking stops; it's done when the findings are captured. A senior produces a data dictionary, a log of known issues and caveats, the decisions made (what to drop/impute/transform and why), and recommendations for the pipeline — all in a reproducible notebook or script so it's re-runnable on the next batch.

What actually makes it "senior" (the part to encode in your agent)
A few things distinguish the senior flow, and these are the design targets for your system:
The senior carries priors and gets surprised. The whole loop runs on the gap between "what I expected" and "what I see." An agent with no expectation model can only describe data; it can't evaluate it. Giving your agent an explicit "expected vs observed" mechanism is probably the highest-leverage design choice.
The senior prioritizes by downstream impact, not by completeness. They don't report every statistic; they report what threatens the objective. Your agent needs a notion of "what matters here," which comes from Phase 0.
The senior is adaptive, not exhaustive. After Phase 2 they branch based on findings. A purely sequential agent will be thorough but shallow. You likely want a planner/controller that decides the next probe, with the mechanical describe-everything pass as just one available tool.
And the senior always traces back to the source process. Anomalies are clues about how data was produced. An agent that can hypothesize why a quirk exists (an upstream join, a default value, a timezone bug) is far more useful than one that just flags it.

A practical note on the data-engineer flavor versus data-scientist flavor, since you said engineer: a DE weights Phases 0, 2, and 5 heavily (schema, contracts, quality at scale, drift, pipeline robustness, reproducibility) and treats Phases 3–4 as means to that end. A DS weights 3, 4, and 6 (distributions, relationships, modeling implications). Your agent's emphasis should follow whichever role it's supporting.