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

# Top-of-page buttons linking into the GitHub repository: "view" (eye)
# opens the current page's source in the repo; "edit" (pencil) opens the
# GitHub edit flow. Furo's determine_page_view_link builds the URLs from
# these three options.
html_theme_options = {
    "top_of_page_buttons": ["view", "edit"],
    "source_repository": "https://github.com/pauleveritt/satyrn-engine",
    "source_branch": "main",
    "source_directory": "docs/",
}
