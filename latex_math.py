import hashlib
import os
import re
import subprocess
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


def _namespace_svg_ids(svg_markup: str, prefix: str) -> str:
    """Prefix every id (and the hrefs pointing at it) in a dvisvgm-generated
    SVG with a hash unique to this snippet's source.

    dvisvgm names glyph paths after nothing more than the font slot and
    character code (e.g. `g0-102`), which is only unique within a single
    dvisvgm invocation. Every snippet on a page is rendered by a separate
    invocation and then inlined as raw SVG into the same HTML document, so
    two snippets that use different fonts (e.g. roman text in a `tikzpicture`
    node vs. math italic in `$f$`) can easily assign that same id to two
    different glyph outlines. Because ids must be document-unique, the
    browser keeps only the first definition and resolves every `<use>`
    referencing that id to it - silently swapping in the wrong, differently
    shaped/scaled glyph everywhere else it's reused, which is what produced
    the garbled text in the rendered `tikzpicture`. Prefixing with a hash of
    the snippet's own source keeps ids unique across snippets while staying
    stable (and cache-friendly) for repeats of the same snippet.
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
            svg_markup = _render_to_svg(body, pdflatex_preamble, "latex-" + h,
                                        temp_output_dir, latex_path, dvisvgm_path)
            svg_markup = _namespace_svg_ids(svg_markup, h)
        return f"\n{svg_markup}\n"

    return fence_re.sub(repl, md_text)


def _replace_display_math(
    md_text: str, pdflatex_preamble: str, temp_output_dir: str,
    latex_path: str, dvisvgm_path: str,
    placeholders: dict[str, str],
) -> str:
    disp_re: Pattern[str] = re.compile(r"\$([^\n]+?)\$")

    def repl(m: Match[str]) -> str:
        body = f"${m.group(1).strip()}$"
        h = _hash(body)
            svg_markup = _render_to_svg(body, pdflatex_preamble, "latex-" + h,
                                        temp_output_dir, latex_path, dvisvgm_path)
            svg_markup = _namespace_svg_ids(svg_markup, h)
            svg_markup = re.sub(r"<\?xml[^?]*\?>", "", svg_markup).strip()
            svg_markup = svg_markup.replace("\n", "")
            span_html = (
                f'<span style="display: inline-block; vertical-align: middle;">'
                f'{svg_markup}</span>'
            )
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
        self.md._latex_svg_placeholders: dict[str, str] = {}

        temp_output_dir = self._temp_output_dir()
        os.makedirs(temp_output_dir, exist_ok=True)

        text, preamble = _extract_math_preamble(text)
        text = _replace_fenced_math(text, preamble, temp_output_dir,
                                    self.config.latex_path, self.config.dvisvgm_path)
        text = _replace_display_math(text, preamble, temp_output_dir,
                                     self.config.latex_path, self.config.dvisvgm_path,
                                     self.md._latex_svg_placeholders)
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
