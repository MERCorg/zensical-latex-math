import hashlib
import html
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Match, Pattern

from markdown import Extension
from markdown.postprocessors import Postprocessor
from markdown.preprocessors import Preprocessor

try:
    from zensical.extensions.context import ContextPreprocessor as _ContextPreprocessor
    _HAS_ZENSICAL = True
except ImportError:
    _HAS_ZENSICAL = False


logger = logging.getLogger("mkdocs.plugins.latex_math")


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------


@dataclass
class LatexMathConfig:
    latex_path: str = "latex"
    dvisvgm_path: str = "dvisvgm"
    output_dir: str = "tmp"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _hash(tex: str) -> str:
    h = hashlib.sha1()
    h.update(b"latex-math")
    h.update(tex.encode("utf-8"))
    return h.hexdigest()


_SVG_ID_DEF_RE = re.compile(r"""\bid=(['"])(?P<id>[^'"]+)\1""")
_SVG_ID_REF_RE = re.compile(r"""\b(xlink:href|href)=(['"])#(?P<id>[^'"]+)\2""")


def _namespace_svg_ids(svg_markup: str, prefix: str) -> str:
    """Prefix every id (and the hrefs pointing at it) in a dvisvgm-generated
    SVG with a hash unique to this snippet's source.

    dvisvgm names glyph paths after nothing more than the font slot and
    character code (e.g. `g0-102`), which is only unique within a single
    dvisvgm invocation. Every snippet on a page is rendered by a separate
    invocation and then inlined as raw SVG into the same HTML document, so
    two snippets that use different fonts can easily assign that same id to two
    different glyph outlines.
    """
    svg_markup = _SVG_ID_DEF_RE.sub(rf"id=\g<1>{prefix}-\g<id>\g<1>", svg_markup)
    svg_markup = _SVG_ID_REF_RE.sub(rf"\g<1>=\g<2>#{prefix}-\g<id>\g<2>", svg_markup)
    return svg_markup


def _render_to_svg(
    tex_body: str,
    pdflatex_preamble: str,
    basename: str,
    temp_output_dir: str,
    latex_path: str,
    dvisvgm_path: str,
) -> str:
    build_dir = os.path.join(temp_output_dir, basename)
    svg_path = os.path.join(build_dir, basename + ".svg")
    if os.path.exists(svg_path):
        with open(svg_path, encoding="utf-8") as f:
            return f.read()

    env = r"""\documentclass{article}
\usepackage{amsmath,amssymb}
\usepackage[active,tightpage,align=middle]{preview}
%s
\begin{document}
\fontsize{14pt}{14pt}\selectfont

\begin{preview}
%s
\end{preview}
\end{document}
    """
    tex = env % (pdflatex_preamble, tex_body)

    os.makedirs(build_dir, exist_ok=True)
    tex_file = os.path.join(build_dir, basename + ".tex")
    with open(tex_file, "w", encoding="utf-8") as f:
        f.write(tex)

    proc = subprocess.run(
        [latex_path, "-interaction=nonstopmode", "-halt-on-error",
         "-output-directory", build_dir, tex_file],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"latex failed with code {proc.returncode}\n{proc.stdout.decode('utf-8')}"
        )

    dvi_file = os.path.join(build_dir, basename + ".dvi")
    proc = subprocess.run(
        [dvisvgm_path, "--no-fonts", "--currentcolor", dvi_file, "-o", svg_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"dvisvgm failed with code {proc.returncode}\n{proc.stdout.decode('utf-8')}"
        )

    with open(svg_path, encoding="utf-8") as f:
        return f.read()


def _is_build_command() -> bool:
    """True when this process was launched via `zensical build` rather than
    `zensical serve`.

    Zensical runs as a single long-lived embedded Python interpreter for the
    whole process, so the CLI subcommand chosen at start-up (`sys.argv[1]`)
    reliably tells us which lifecycle we're in: `build` should fail hard on
    bad LaTeX/syntax so CI/publishing catches it, while `serve`'s live-reload
    loop should keep running and show the error inline instead.
    """
    return len(sys.argv) > 1 and sys.argv[1] == "build"


def _render_error_html(tex_body: str, error: Exception) -> str:
    """Render a visible error block instead of raising, so a bad LaTeX/syntax
    snippet turns into an in-page error rather than aborting the whole build
    (which would otherwise kill `zensical serve`)."""
    message = html.escape(str(error)).replace("\n", "<br>")
    source = html.escape(tex_body)
    return (
        '<pre style="color:#b00020; background:#fdecea; border:1px solid #b00020; '
        'border-radius:4px; padding:0.75em; white-space:pre-wrap; '
        'font-size:0.85em;"><strong>LaTeX render error:</strong> '
        f"{message}\n\n{source}</pre>"
    )


def _extract_math_preamble(text: str) -> tuple[str, str]:
    fence_re = re.compile(
        r"(^|\n)(?P<fence>```|~~~)\s*(?P<info>math_preamble*\b[^\n]*)\n(?P<body>.*?)(?P=fence)\s*(?:\n|$)",
        re.S,
    )
    m = fence_re.search(text)
    if not m:
        return text, ""
    body = m.group("body").rstrip()
    start, end = m.span()
    return text[:start] + text[end:], body


def _replace_fenced_math(
    md_text: str, pdflatex_preamble: str, temp_output_dir: str,
    latex_path: str, dvisvgm_path: str,
) -> str:
    fence_re: Pattern[str] = re.compile(
        r"(^|\n)(?P<fence>```|~~~)\s*(?P<info>math\b[^\n]*)\n(?P<body>.*?)(?P=fence)\s*(?:\n|$)",
        re.S,
    )

    def repl(m: Match[str]) -> str:
        body = m.group("body").rstrip()
        h = _hash(body)
        try:
            svg_markup = _render_to_svg(body, pdflatex_preamble, "latex-" + h,
                                        temp_output_dir, latex_path, dvisvgm_path)
            svg_markup = _namespace_svg_ids(svg_markup, h)
        except Exception as exc:
            logger.error("Failed to render LaTeX math block:\n%s\n%s", body, exc)
            if _is_build_command():
                raise
            return f"\n{_render_error_html(body, exc)}\n"
        return f"\n{svg_markup}\n"

    return fence_re.sub(repl, md_text)


_SVG_VIEWBOX_RE = re.compile(
    r"""\bviewBox=(['"])\s*[-\d.]+\s+(?P<min_y>-?[\d.]+)\s+[-\d.]+\s+(?P<height>[\d.]+)\s*\1"""
)


def _svg_baseline_offset(svg_markup: str) -> float:
    """Depth (in pt) that a dvisvgm SVG extends below its own baseline.

    dvisvgm places the DVI baseline at local y=0, with the viewBox's min-y
    equal to minus the ascent. So min_y + height is how far the glyph's
    lowest point sits below that baseline (descenders) — 0 when nothing
    hangs below it. Used to replace `vertical-align: middle` (which
    aligns the box's vertical center to the x-height midline, not the
    baseline) with the offset that actually lines the rendered TeX up
    with the surrounding text's baseline.
    """
    m = _SVG_VIEWBOX_RE.search(svg_markup)
    if not m:
        return 0.0
    return float(m.group("min_y")) + float(m.group("height"))


def _replace_display_math(
    md_text: str, pdflatex_preamble: str, temp_output_dir: str,
    latex_path: str, dvisvgm_path: str,
    placeholders: dict[str, str],
) -> str:
    disp_re: Pattern[str] = re.compile(r"\$([^\n]+?)\$")

    def repl(m: Match[str]) -> str:
        body = f"${m.group(1).strip()}$"
        h = _hash(body)
        try:
            svg_markup = _render_to_svg(body, pdflatex_preamble, "latex-" + h,
                                        temp_output_dir, latex_path, dvisvgm_path)
            svg_markup = _namespace_svg_ids(svg_markup, h)
            descent = _svg_baseline_offset(svg_markup)
            svg_markup = re.sub(r"<\?xml[^?]*\?>", "", svg_markup).strip()
            svg_markup = svg_markup.replace("\n", "")
            span_html = (
                f'<span style="display: inline-block; vertical-align: {-descent:.3f}pt;">'
                f'{svg_markup}</span>'
            )
        except Exception as exc:
            logger.error("Failed to render inline LaTeX math %r: %s", body, exc)
            if _is_build_command():
                raise
            span_html = _render_error_html(body, exc)
        placeholder = f"LATEXSVGINLINE{h}"
        placeholders[placeholder] = span_html
        return placeholder

    return disp_re.sub(repl, md_text)


# ----------------------------------------------------------------------------
# Processors
# ----------------------------------------------------------------------------


class LatexMathPreprocessor(Preprocessor):
    """Render fenced math blocks and replace inline $...$ with placeholders."""

    name = "latex_math"

    def __init__(self, md: Any, config: LatexMathConfig) -> None:
        super().__init__(md)
        self.config = config

    def _temp_output_dir(self) -> str:
        if _HAS_ZENSICAL:
            context = _ContextPreprocessor.from_markdown(self.md)
            if context:
                cfg = context.config
                site_dir = cfg.get("site_dir", "site")
                root_dir = cfg.get("root_dir", ".")
                if not os.path.isabs(site_dir):
                    site_dir = os.path.join(root_dir, site_dir)
                return os.path.join(site_dir, self.config.output_dir)
        return os.path.join("site", self.config.output_dir)

    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)

        # Per-page placeholder map, read by LatexMathPostprocessor.
        placeholders: dict[str, str] = {}
        self.md._latex_svg_placeholders = placeholders

        try:
            temp_output_dir = self._temp_output_dir()
            os.makedirs(temp_output_dir, exist_ok=True)

            text, preamble = _extract_math_preamble(text)
            text = _replace_fenced_math(text, preamble, temp_output_dir,
                                        self.config.latex_path, self.config.dvisvgm_path)
            text = _replace_display_math(text, preamble, temp_output_dir,
                                         self.config.latex_path, self.config.dvisvgm_path,
                                         placeholders)
        except Exception:
            # Per-snippet latex/dvisvgm failures are already handled above
            # (and re-raised here to fail `build` hard when relevant); this
            # catches anything else unexpected. During `serve`, that must
            # not take down the dev server for every other page, so log it
            # and fall back to the untouched source for this page. During
            # `build`, surface it as a hard failure like the per-snippet
            # errors above.
            logger.exception("latex_math preprocessor failed; leaving page source untouched")
            if _is_build_command():
                raise
            return lines

        return text.split("\n")


class LatexMathPostprocessor(Postprocessor):
    """Substitute inline-math placeholders with their SVG <span> HTML."""

    name = "latex_math"

    def run(self, text: str) -> str:
        for placeholder, span_html in getattr(self.md, "_latex_svg_placeholders", {}).items():
            text = text.replace(placeholder, span_html)
        return text


# ----------------------------------------------------------------------------
# Extension
# ----------------------------------------------------------------------------


class LatexMathExtension(Extension):
    """Render LaTeX math as inline SVG via latex + dvisvgm."""

    name = "latex_math"

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    def extendMarkdown(self, md: Any) -> None:
        md.registerExtension(self)
        config = LatexMathConfig(**self._kwargs)

        preprocessor = LatexMathPreprocessor(md, config)
        # Priority 30: before superfences' fenced_code_block (25) so we claim
        # ```math blocks before superfences can treat them as code.
        md.preprocessors.register(preprocessor, preprocessor.name, 30)

        postprocessor = LatexMathPostprocessor(md)
        # Priority 25: after raw_html (30) restores stashed HTML blocks.
        md.postprocessors.register(postprocessor, postprocessor.name, 25)


def makeExtension(**kwargs: Any) -> LatexMathExtension:
    return LatexMathExtension(**kwargs)
