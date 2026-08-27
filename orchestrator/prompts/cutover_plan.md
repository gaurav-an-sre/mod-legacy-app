# Propose the cutover plan for the $slice slice

The $slice service now passes the parity gate at $parity_match_rate. Propose how its traffic should
be ramped from 0% to 100%, for a human to review before anything moves.

Ground every step in this incident-free but reversible framing: each ramp step must name what would
tell you to stop, and every stop condition must be observable from the façade's own access log or
the service's `/healthz` — not from something nobody is watching. Base the plan on what you learned
building the slice: the routes that were hardest to match, the ones carrying writes, and anything
you listed as a benign or unresolved difference are the risk, and the ramp should reflect that.

Do not change any weights, and do not modify code in this run.

## Reply

Reply with strict JSON and nothing else:

{"slice": "$slice",
 "steps": [{"weight": <int 0..100>, "soak_minutes": <int>, "watch": ["<observable signal>", ...]}],
 "rollback_triggers": [{"signal": "<observable>", "threshold": "<value>", "action": "weight 0"}],
 "irreversible_operations": [{"what": "<operation>", "why_risky": "<one line>"}],
 "residual_risk": "<one paragraph a reviewer can disagree with>"}
