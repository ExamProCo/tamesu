# Image-generation eval authoring

Use for `task: image_generation`. The project-local `docs/image-evals.md`,
`docs/manifests.md`, and `tamesu lint` are authoritative over this summary.

## Shape

- `task: image_generation`; no `output_schema`. Each arm has one prompt:
  `prompts: {prompt: ../../prompts/generate.md}`. The template may use `{{ item.input.* }}`.
- Dataset items carry the brief under `input` and `expected: {}` (there is no expected
  image).
- Parameters: `parameters.image` (`size`, `output_format`, `count: 1`) and
  `parameters.provider_options` for anything vendor-specific. Never put `max_tokens` or
  `effort` here. Use one size for every product.
- Pick a registered model whose capability is `image_output` (`tamesu models`). Prefer a
  dated snapshot when repeatability over time matters.
- If the output must be fictional, disable grounding tools explicitly (for Meta,
  `tool_enablement` all `false`). They are not global defaults.

## Decide before the first run

1. Which evidence gates acceptance (`acceptance.requires`). Default to
   `[mechanical, human]`; keep a model judge at `role: screen`.
2. Repetitions and `selection`. Default to one repetition and `selection: none`.
3. Reviews per item, and `on_disagreement` if more than one.
4. The rubric: one yes/no claim per dimension with reason codes, in a `.yml` file with the
   prose rationale beside it. Do not ask a judge for a numeric score or an overall verdict.

## Checks and judges

- Integrity (decodes, PNG/JPEG/WebP, sane size) is always applied. Declare content checks
  per eval (`min_resolution`, `format_match`, `non_uniform`, `has_transparency`); a flat
  colour can be the right answer to a different eval.
- Judges need `defaults.judge_budget_usd`. Judge prompts see only the brief and rubric.
- Prefer a judge from a different model family than the generator, and report agreement
  with the human reviewer.

## Do not

- Regenerate to get a better-looking image, or change `acceptance` after a run exists.
- Treat a judge verdict as ground truth, or merge human and judge verdicts into one score.
- Commit generated images unless the repository tracks them with LFS or DVC.
