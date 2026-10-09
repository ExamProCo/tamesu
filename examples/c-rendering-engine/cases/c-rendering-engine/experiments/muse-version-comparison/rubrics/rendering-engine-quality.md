# Rendering Engine Quality Rubric

Reviewers should evaluate extracted responses without model labels. Compile and run each
submission in a fresh, isolated directory before assigning ratings.

Rate every dimension from 0 to 4 and record concrete evidence. A score of 0 means absent
or unusable; 2 means partially successful with material gaps; 4 means complete, correct,
and convincing.

## Dimensions

1. **One-shot completeness** — Includes all source, build steps, runtime instructions,
   and required assets without relying on a follow-up turn.
2. **Buildability and runtime correctness** — Builds from the supplied instructions and
   runs without crashes, undefined-behavior symptoms, or missing dependencies.
3. **Direct pixel rendering** — Owns the framebuffer or image buffer and implements pixel
   writes itself rather than delegating scene rendering to an existing rendering engine.
4. **Primitive-to-scene progression** — Demonstrates a clear path from foundational
   primitives to composition of a meaningfully complex scene.
5. **Lighting implementation** — Implements substantive lighting or shading behavior,
   with technically coherent calculations and visible effects.
6. **Final visual impact** — Culminates in a deliberate, striking lighting demo rather
   than a token example or static primitive test.
7. **Engineering quality** — Uses readable organization, defensible memory and error
   handling, portable assumptions where practical, and documentation that matches the
   code.

## Required review notes

For each response, record:

- exact build and run commands;
- compiler and platform;
- whether it built and ran;
- warnings, crashes, missing files, or manual repairs;
- output artifact and screenshot paths;
- a 0–4 score with evidence for each dimension; and
- any safety concerns discovered before execution.

Do not silently repair a response before its first build attempt. Record any repair as a
separate intervention so one-shot completeness remains observable.
