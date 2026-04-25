from __future__ import annotations

import sys
from pathlib import Path


PACKAGE_NAME = "cli-anything-zotero"
PACKAGE_VERSION = "0.5.1"


def _handle_metadata_query(argv: list[str]) -> bool:
    if len(argv) != 2:
        return False
    if argv[1] == "--name":
        print(PACKAGE_NAME)
        return True
    if argv[1] == "--version":
        print(PACKAGE_VERSION)
        return True
    return False


if __name__ == "__main__" and _handle_metadata_query(sys.argv):
    raise SystemExit(0)

from setuptools import find_namespace_packages, setup


ROOT = Path(__file__).parent
README = ROOT / "README.md"
LONG_DESCRIPTION = README.read_text(encoding="utf-8") if README.exists() else ""


setup(
    name=PACKAGE_NAME,
    version=PACKAGE_VERSION,
    author="cli-anything contributors",
    author_email="",
    description="Zotero CLI and MCP server package — installs zotero-cli and zotero-mcp for AI-assisted library workflows.",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    url="https://github.com/PiaoyangGuohai1/cli-anything-zotero",
    project_urls={
        "Issues": "https://github.com/PiaoyangGuohai1/cli-anything-zotero/issues",
        "Documentation": "https://github.com/PiaoyangGuohai1/cli-anything-zotero#installation",
        "Changelog": "https://github.com/PiaoyangGuohai1/cli-anything-zotero/releases",
    },
    keywords=[
        "zotero", "cli", "mcp", "mcp-server", "model-context-protocol",
        "reference-manager", "bibliography", "ai-agents", "ai", "llm",
        "academic", "research-tools", "bibtex", "citation-manager",
        "claude", "claude-code", "cursor", "python",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering",
        "Topic :: Text Processing :: General",
    ],
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
        ],
        "mcp": [
            "mcp>=1.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "cli-anything-zotero=cli_anything.zotero.zotero_cli:entrypoint",
            "zotero-cli=cli_anything.zotero.zotero_cli:entrypoint",
            "zotero-mcp=cli_anything.zotero.zotero_cli:mcp_entrypoint",
        ],
    },
    package_data={
        "cli_anything.zotero": [
            "README.md",
            "skills/SKILL.md",
            "tests/TEST.md",
            "plugin/zotero-cli-bridge/manifest.json",
            "plugin/zotero-cli-bridge/bootstrap.js",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
