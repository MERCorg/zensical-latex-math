# Overview

As opposed to `MathJax` or `KaTeX`, this plugin uses a full LaTeX engine to
render math expressions as `SVG` files that are directly embedded in the
generated HTML. This is particularly useful for documentation that involves
pseudocode and content that is also used in scientific publications. It is
inspired by the [MyST](https://myst-parser.readthedocs.io/) project. By
embedding the SVGs as opposed to linking them as images, it is also possible to
style them with CSS.

## Usage

This plugin requires a [LaTeX](https://www.latex-project.org/) installation with
`latex` and `dvisvgm` available in the system `PATH`, and any packages included
in the math preambles.

This plugin renders inline math, delimited by single dollar signs `$...$`.
Additionally, fenced code blocks with the info string `math` are also rendered.
For example, dollar-delimited inline math:

```markdown
See the following equation for the area of a circle $A = \pi r^2$.
```

We can also use a special fenced `math_preamble` code block to include LaTeX
packages or define macros. Note that in the actual code the apostrophess should
be regular backticks.

```markdown

'''math_preamble
\usepackage{tikz}
'''

Later on in the tikz we can draw a circle with tikz:

'''math
\begin{tikzpicture}
  \draw (0,0) circle (1cm);
\end{tikzpicture}
'''
```

The svgs are generated with the `--currentcolor` option to `dvisvgm`, so they
will pick up the current text color, but since they are embedded as inline SVGs,
it is also possible to set their colors with CSS. For example, to make the math
images blue, you can add the following CSS rule:

```css
svg {
    color: blue;
}
```

A fenced block's info string can carry extra tags after `math`, e.g.
`math algorithm`, or `math algorithm foo` for more than one. Each tag adds
a `latex-math-block--<tag>` modifier class to the rendered block's wrapper
`<div>`, alongside the base `latex-math-block` class — so `math algorithm`
renders as `<div class="latex-math-block latex-math-block--algorithm">`.
The plugin itself has no opinion on what a tag means; it's a hint the page
author gives directly in the markdown, not something inferred from the
LaTeX body.

## Sizing

Every snippet is typeset by LaTeX at a fixed `\fontsize{14pt}{14pt}`
(`_MATH_FONT_PT` in `latex_math.py`). A LaTeX **point** (`pt`) is TeX's own
native unit of length (1pt = 1/72.27in, close to but not quite the desktop
publishing "big point"), and it's what `dvisvgm` uses for the `width`,
`height` and `viewBox` it writes into the SVG it generates from the DVI
output — a fixed physical size, with no idea what page or font-size it will
eventually be embedded into.

A CSS **em**, on the other hand, is a *relative* unit: `1em` means "the
font-size of the element this is used on" (or, for `font-size` itself, the
parent's font-size). Two elements sized in `em` next to each other stay in
proportion no matter what the actual font-size resolves to — whether that
comes from the page theme, a responsive breakpoint, a `<small>` or a table
cell, or the reader's browser zoom.

dvisvgm's `pt`-based output has none of that: it is one specific, absolute
size, unrelated to whatever CSS font-size happens to apply where the
snippet gets inlined into a Markdown page. Left as `pt` (or converted to
`px`), a block or inline snippet stops tracking the surrounding text the
moment that text's font-size changes for any reason — it would need to be
re-rendered, or blown up/down with a separate scaling hack, to match again.

So before an SVG is inlined, `_svg_dims_to_em` rewrites its outer `width`
and `height` from `Xpt`/`Ypt` to `(X / _MATH_FONT_PT)em`/`(Y /
_MATH_FONT_PT)em`, leaving the `viewBox` (and everything drawn inside it)
untouched. That one division fixes the meaning of `1em` for every snippet
to "`_MATH_FONT_PT` TeX points", so from then on the browser — not LaTeX —
decides the final pixel size, using whatever font-size cascades to the
element the SVG landed in. The math stays visually in sync with the
surrounding text everywhere it's embedded, without a second render pass or
any container-width-dependent CSS.

## Configuration
 
This plugin can be configured via the `mkdocs.yml` configuration file. There are
some specific options for this plugin:
 
 - 'dvisvgm_path': Path to the dvisvgm tool. Default: `dvisvgm`
 - 'latex_path': Path to the latex tool. Default: `latex`
 - 'asset_subdir': Subdirectory in the site output directory to store generated images
 - 'temp_dir': Directory to use for temporary files, if enabled will dump files for inspection. Default: system temp directory.
