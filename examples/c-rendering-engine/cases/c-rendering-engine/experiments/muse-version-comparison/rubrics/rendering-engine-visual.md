# Rendering engine visual quality: rationale

This rubric judges only the **rendered image**. Reviewers never see the code, the model, or
the prompt arm, so they cannot be swayed by which version wrote the program. Everything the
image cannot show is out of scope here: whether the source is readable, whether it renders
directly to pixels, and how well the build instructions match the code. The manual
[rendering-engine-quality rubric](rendering-engine-quality.md) covers those and needs the
source, so it stays a separate, unblinded review.

Deterministic checks run first, inside the sandbox: the project builds, runs, writes a
decodable image, and the image is not blank, flat, or near-uniform. A reviewer therefore
never spends time on programs that did not render, and a pass here does not mean the image
is good, only that it is worth looking at.

Each dimension is **one** yes/no claim, so a report can say which claim failed most. The
prompt asked for a striking lighting demo, which is why `lighting-visible` and
`striking-demo` are separate: lighting can be present and still unremarkable.

Failures carry a reason code from a fixed list so failure rates can be counted. Every
dimension also has `other`, which a reviewer must explain in the notes. A high `other` count
means the rubric is missing a failure mode and should be revised before the next run.

There are no numeric scores. An image is accepted only if every dimension passes and its
deterministic checks passed.
