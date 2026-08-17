# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

project = "Satyrn Engine"
copyright = "2026, Satyrn Engine contributors"
author = "Satyrn Engine contributors"

extensions = [
    "myst_parser",  # MyST Markdown support
    "sphinx.ext.viewcode",  # Add links to source code
    "sphinx.ext.todo",  # Support for to do items
    "sphinx.ext.intersphinx",  # Cross-reference external docs
]

# MyST configuration
myst_parse_frontmatter = True
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "smartquotes",
    "substitution",
    "tasklist",
]

# Generate anchors for h1-h3 so cross-file links can target a section.
# Without this, a path#fragment link silently resolves to the document
# and drops the anchor.
myst_heading_anchors = 3

pygments_style = "sphinx"
pygments_dark_style = "monokai"

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"

# A GitHub octocat in the footer, linking to the repository home — Furo's
# native footer_icons option.
html_theme_options = {
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/pauleveritt/satyrn-engine",
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0" '
                'viewBox="0 0 16 16" height="1em" width="1em">'
                '<path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 '
                '5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94'
                '-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 '
                '1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 '
                '0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 '
                '1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 '
                '2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 '
                '1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z">'
                "</path></svg>"
            ),
        },
    ],
}

# A GitHub octocat in the page header, injected by docs/_static/github-header.js
# — Furo 2025.12.19 has no native header slot (its octocat is footer-only and
# ReadTheDocs-gated). If Furo grows a native header icon, delete the shim and
# this entry.
html_static_path = ["_static"]
html_js_files = ["github-header.js"]
