# Local live UI and API

`tamesu show <case> --open` serves the local live overview on loopback. It reads
stored evidence once per second. Start it before a CLI `run` in another terminal
to watch progress. A five-column run table and an answer/check/review table give
an overview; full evidence and narrative remain linked. Failed-item errors are
available in each run's evidence details. Recorded costs use six decimal places;
unpriced completed items are explicitly unknown rather than free.

`GET /api/state` returns schema version 1, case name, evaluations, run states,
selected/completed/failed counts, known cost, unknown-cost item count, answers,
scores, and human review progress. `GET /data.json` retains the rendered
presentation contract. Neither endpoint executes an Inspect task. For example:

```sh
curl http://localhost:8000/api/state
```

## Direct review

Click **Review / update decisions** on the overview. The local server generates
a blinded review session using the existing export contract. **Save review**
imports validated decisions directly; no download or CLI import is required.
Previously reviewed artifacts can be reviewed again. The latest valid decision
per reviewer applies and immutable history remains. The form starts unanswered
when reopened, so only complete items you intend to review or update.

Sessions last until the server stops. The server keeps import keys in the
experiment and deletes temporary reviewer packs on shutdown. File-based blinded
export/import still supports reviewers on another machine.

The write API checks a session token, loopback Host, and browser Origin. It only
addresses evaluations in the selected case and runs the same rubric/artifact
digest validation as CLI import. Reviewer IDs are labels, not authenticated
accounts. This is a local notebook server, not shared-user hosting.

## Update boundaries

Updates use one-second polling, not push streaming. Costs and usage arrive when
Inspect completes samples; in-flight requests do not expose continuously updated
billing. Unknown prices stay unknown. The static/full narrative pages rebuild
on file changes but require a browser refresh. Starting runs and editing task or
configuration files remain terminal/editor actions.
