# Baseline experiment

## Question

What share of generated images are usable product references, and does a model judge
agree with a person about which ones are?

## Arms and models

One prompt arm, run against two image models (`muse-image-1.0` and `gpt-image-2`) with
the same size and format. Provider-specific settings (Meta's reasoning strength and tool
switches, OpenAI's quality) live under each run's `provider_options` and are recorded.

## Acceptance (declared before any run)

An image is accepted only if it passes the mechanical checks **and** a person's review
against the rubric. The model judge is a screen: it is reported beside the human result and
never decides. One repetition and `selection: none`, so the first run measures the
pipeline instead of hunting for a good-looking image.

## Interpretation

Three products and one reviewer demonstrate the workflow. They do not support a claim
about either model.
