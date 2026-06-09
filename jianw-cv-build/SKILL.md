---
name: jianw-cv-build
description: >-
  Set up the LaTeX toolchain and build Jian Wang's resume PDF (jianw_cv.pdf)
  from jianw_cv.tex and the makepdf script. Use when building this resume on a
  new or fresh macOS machine, when pdflatex / the `res` resume class / Palatino
  fonts are missing, or when the user asks to compile jianw_cv.tex / makepdf and
  the build fails with "res.cls not found", "pplr7t Metric (TFM) file not found",
  or "pdflatex: command not found".
---

# Build jianw_cv.pdf on a fresh macOS machine

This resume (`jianw_cv.tex` + `makepdf`) needs three things that are NOT present
on a clean Mac: a LaTeX engine (`pdflatex`), the `res` resume document class, and
the Palatino fonts. This skill installs all three, then builds the PDF.

## Quick start

From the resume folder (the one containing `jianw_cv.tex` and `makepdf`):

```bash
bash ~/.cursor/skills/jianw-cv-build/scripts/setup.sh --build
```

`setup.sh` is idempotent — re-running it skips anything already installed. Omit
`--build` to only install prerequisites.

## Important: the sudo step

Installing BasicTeX requires `sudo` (an interactive password prompt). An agent
cannot type the password, so when the toolchain is missing, the **user** must run
`setup.sh` (or at least the BasicTeX step) in their own terminal. After BasicTeX
exists, the agent can run everything else (res.cls + Palatino + build) itself.

## What setup.sh does

1. **BasicTeX** — `brew install --cask basictex`. If the cask's sudo step fails,
   it finds the downloaded `.pkg` in the Homebrew cache and runs
   `sudo installer -pkg <pkg> -target /`. Then loads TeX onto PATH via
   `eval "$(/usr/libexec/path_helper)"` and `/Library/TeX/texbin`.
2. **`res` class** — copies the bundled `assets/res.cls` into
   `$(kpsewhich -var-value TEXMFHOME)/tex/latex/resume/` and runs `mktexlsr`.
   This modern `res.cls` has the `margin` and `line` options built in, so no
   separate `margin.sty` / `line.sty` are needed. (`res` is an old LaTeX 2.09
   class not shipped in TeX Live, which is why it is bundled here.)
3. **Palatino fonts** — installs without sudo via tlmgr user mode:
   `tlmgr init-usertree` then
   `tlmgr --usermode --repository <mirror> install palatino`.
   Uses the `ctan.math.illinois.edu` tlnet mirror because the default mirror is
   sometimes unreachable.

## Building manually (after prerequisites exist)

```bash
eval "$(/usr/libexec/path_helper)"; export PATH="/Library/TeX/texbin:$PATH"
bash makepdf            # runs: pdflatex jianw_cv  (twice, for the "N of M" page refs)
```

The two passes are required: `res.cls` prints a "-- page of total --" footer that
needs a second run to resolve.

## Verifying the output matches the reference

`jianw_cv22.pdf` is the reference. To visually compare a page (macOS `sips` only
renders page 1; use the `pdfpages` trick for other pages):

```bash
# page N of any PDF -> PNG
printf '\\documentclass{article}\\usepackage{pdfpages}\\begin{document}\\includepdf[pages={N}]{FILE.pdf}\\end{document}\n' > /tmp/p.tex
pdflatex -output-directory=/tmp -interaction=nonstopmode /tmp/p.tex >/dev/null
sips -s format png -Z 1400 /tmp/p.pdf --out /tmp/pageN.png
```

`pdftotext` / `pdfinfo` are NOT in BasicTeX — do not rely on them for comparison;
render to PNG and look at the image instead.

## Gotchas baked into jianw_cv.tex

- Bullet lines use `{*}` not a bare `*`, because `\\` followed by `*` is parsed as
  the `\\*` (no-page-break) form and the asterisk disappears.
- Two explicit `\newpage` breaks lock the pagination to match `jianw_cv22.pdf`
  (page 1 ends after "Research Affiliate"; page 2 ends mid the "Excellent Thesis
  Award" entry). Remove them only if exact page splits no longer matter.
- Header (name + phone) alignment: `res.cls`'s `\opening` typesets the `\name`
  line on a box that is wider than the rule below it by `\hoffset` (= the margin
  `\sectionwidth`, 1.3in). A bare `\hfill` therefore pushes the phone past the
  page's right edge and clips it. Fix: end the name with `\hspace*{\sectionwidth}`
  so the phone is pulled back to the rule's right edge:

  ```
  \name{{\LARGE {\bf Jian \,\, Wang} } \hfill {\rm Phone: (607) 240-1369}\hspace*{\sectionwidth}\vspace*{.1in}}
  ```

  The name stays flush-left at the rule's left edge automatically. Do NOT wrap the
  name in `\hbox to \resumewidth{...}` (it indents the name) and do NOT add a
  negative `\hspace` (it pushes the phone further off-page).
