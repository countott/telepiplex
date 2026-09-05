# telepiplex Search waiting boundaries

Root discovery starts the Wikidata title lookup after the first successful
Chinese Wikipedia response. A single auxiliary worker overlaps that lookup with
entity enrichment and, when needed, English Wikipedia discovery. English
fallback and result sorting still use the same evidence and deterministic order.
Each provider retains its own HTTP timeout and retry limits. The worker is joined
before discovery exits, including failures.

Successful discovery uses the same provider calls as the sequential flow. A
failure of the first Wikipedia read starts no title request. If entity enrichment
or English Wikipedia fails later, the title lookup may already have started;
that is one additional logical lookup, with the existing retry limit. The
original root error retains priority. A title failure remains optional only when
an admissible Wikipedia root exists.

Candidate localization has a two-second **overall locale-stage budget**, including
queueing and all candidates. Localization may change a validated Chinese title,
aliases, Douban reference, source links and a missing poster. These changes form
one transaction on a private copy before candidates are displayed. A successful
transaction is accepted only if the original plan is unchanged, including its
revision, owner, candidate IDs/order, root identity, source references and scope.
Timeout discards the transaction and returns the existing plan. The usual exact
identity hydration, scope validation and final metadata v2 gate remain mandatory;
timeout does not create missing proof or authorize an unresolved candidate.

Timeout and cancellation stop the locale consumer. `SourceScheduler` continues
to shield shared reads for other consumers; an already-running synchronous
network request may finish under the adapter's own HTTP timeouts. Cancellation
does not terminate that thread. No late locale callback writes to the returned
plan or a confirmed contract. The existing independent poster stage retains its
own timeout; the two-second budget is not a total Search latency guarantee.

No additional root cache was introduced. The observed single-task offline
business flow contains distinct Chinese/English searches, title lookup, entity
batch and country-label reads. Exact reads after user selection serve a separate
verification purpose. Existing scheduler sharing and cache validation remain
unchanged; no owner/plan/revision-bearing plan is cached.

Local verification uses controlled external provider fixtures, including a fake
event-loop clock for budget expiry and real Search command/callback flows for
pagination, selection, S02E03 confirmation, cancellation, restart and wrong exact
identity rejection. These checks do not claim live-provider performance.
