# Report draft — superseded

**This file is superseded by [`docs/report/linesense-report.tex`](../report/linesense-report.tex)
and its compiled PDF, `docs/report/linesense-report.pdf`.**

The original Task 27 brief asked for a Markdown report draft. During the task
the user asked instead for a full LaTeX project report with TikZ diagrams. That
report is the deliverable: it contains every section this draft was supposed to
contain, plus nine hand-laid-out diagrams that Markdown could not carry. Editing
this file would create a second, drifting copy of the same content, which is
exactly what the project's own documentation rule forbids.

## Where each required section now lives

| Section required by the brief | In the LaTeX report |
|---|---|
| Introduction / problem | §1 |
| Domain and users | §2 |
| Requirements | §3 (plus `docs/requirements.md`) |
| System design — C4, agents, protocol, data model, state machines | §4, §5, §6 (figures 1–3, 5–6) |
| Methodology — deterministic core plus bounded agents, retrieval, NLP, evaluation design | §7 |
| Implementation | §8 |
| Workflows | §9 (figures 4, 7, 8) |
| Security | §10 |
| Responsible AI implementation | §11 |
| Evaluation results | §12 |
| Reliability, performance, deployment | §13 (figure 9) |
| Commercialization plan with pricing | §14 (plus `commercialization.md`) |
| Limitations and future work | §15 |
| References | after §16 |
| Appendix — role matrix, API list, command reference, ADRs | A–D |

## Template gap

**The official report template was not supplied to this project.** The LaTeX
report is self-structured and says so in its section 1.3. When the template
becomes available, restructure the report's content into it rather than
rewriting the content — every claim in it is already tied to an artefact in the
repository.

## Building it

```bash
scripts/heavy-job.sh tectonic docs/report/linesense-report.tex
```

Tectonic 0.17.0 was used, and the build was run: the 37-page PDF is committed at
`docs/report/linesense-report.pdf`. Any pdfLaTeX/XeLaTeX installation with a
standard TeX Live set (`tikz`, `pdflscape`, `booktabs`, `tabularx`, `amsmath`,
`hyperref`) builds it the same way.
