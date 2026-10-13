# Tamesu

Tamesu documents AI experiments and carries their results in portable packages.
[Inspect AI](https://inspect.aisi.org.uk/) runs the tests and automated scorers.
Tamesu adds a live results overview and optional human review of text, code, images,
or multiple artifacts.

## Start

Requires Python 3.12 or later.

```sh
pip install -e .
tamesu new eval arithmetic/first/baseline
# Set META_API_KEY, or edit eval.yml to use a model/provider whose credentials you have.
tamesu run arithmetic/first/baseline
tamesu show arithmetic
```

The starter creates three files: `case.yml`, `eval.yml`, and an ordinary Inspect
`task.py`. Inputs and prompts start inline in `eval.yml`; split them into files
when useful. The task checks correctness, not just output formatting.
Record the experiment's question, method, findings, and limitations alongside it.
There is no draft activation or closing step.

## Share and review

```sh
tamesu pack arithmetic
tamesu verify build/packages/local-arithmetic-0.1.0.tamesu.tar.gz
tamesu unpack build/packages/local-arithmetic-0.1.0.tamesu.tar.gz --cases-dir received/cases
cd received
tamesu show arithmetic
```

Packages contain the experiment definitions, results, artifacts, and any recorded
reviews. They do not need publishing metadata or a hosting service. Viewing and
human review do not call a model or execute the included task code. Rerunning a
received task requires reviewing its code and passing `--trust-code`.

Add a rubric through `evaluation.human_review` to enable human review. Only include
`human` in `evaluation.acceptance.requires` if that review must gate acceptance.
Then export a blinded review pack, open its `review.html`, and import the downloaded
responses. Review records bind the artifacts and rubric by checksum.

```sh
tamesu review export arithmetic/first/baseline --out review-pack
tamesu review import arithmetic/first/baseline review-pack/responses.yml
```

Automated model grading belongs in Inspect scorers. Images generated through the
existing image APIs use an Inspect model adapter; there is no native runner.
Older stored evidence remains viewable, but does not satisfy a changed experiment.

Use `tamesu show` for a live overview with answers and checks at a glance, item-completion
updates, recorded cost to six decimal places, and **Review / update decisions**
that saves directly. `/api/state` provides the same live state to scripts.
Costs update when items finish; they are not a provider billing stream.

Read the [author-first walkthrough](docs/happy-path-walkthrough.md) or
[tutorial](docs/tutorial.md) for the full flow and
[reference docs](docs/reference/README.md) for file contracts and migration details.

## Development

```sh
python -m unittest discover -s tests -v
```

### Authored experiment workflow

```sh
tamesu case new base-calc
tamesu exp new base-calc/add
tamesu eval new base-calc/add/int
tamesu lint .
tamesu plan base-calc/add/int
tamesu run base-calc/add/int --limit-items 5 --repetitions 1 --dry-run
tamesu show
```

Fill in `case.yml`, `experiment.yml`, and the eval's `eval.yml` and Inspect `task.py`.
Use `evaluation.scorers` and Inspect metric references such as `correctness/accuracy`.
External datasets can use `{{ root.datasets }}/<dataset>/dataset.yml`.
`show` lists cases and provides a documentation editor. `report` exports an optional
Markdown snapshot; `analyze --scaffold` creates an interpretation template without AI.
See [the authored walkthrough](docs/happy-path-walkthrough2.md) and
[CLI reference](docs/reference/cli.md) for limited runs and shell completion.
