"""Common source-language extensions shared by routing and workspace discovery.

This registry helps AlienApe recognize text files; it does not imply that their
contents can be executed or automatically verified.
"""

import re

LANGUAGE_SUFFIXES = {
    '.py': 'python', '.pyw': 'python', '.pyi': 'python',
    '.gd': 'gdscript', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript', '.tsx': 'typescript', '.mts': 'typescript', '.cts': 'typescript',
    '.java': 'java', '.kt': 'kotlin', '.kts': 'kotlin', '.scala': 'scala', '.sc': 'scala',
    '.groovy': 'groovy', '.clj': 'clojure', '.cljs': 'clojurescript', '.cljc': 'clojure',
    '.rb': 'ruby', '.rake': 'ruby', '.php': 'php', '.phtml': 'php',
    '.swift': 'swift', '.m': 'objective-c', '.mm': 'objective-c++',
    '.c': 'c', '.h': 'c', '.cc': 'cpp', '.cpp': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp', '.hxx': 'cpp',
    '.cs': 'csharp', '.fs': 'fsharp', '.fsx': 'fsharp', '.vb': 'visual-basic',
    '.rs': 'rust', '.go': 'go', '.zig': 'zig', '.nim': 'nim', '.nims': 'nim', '.v': 'v',
    '.lua': 'lua', '.pl': 'perl', '.pm': 'perl', '.r': 'r', '.jl': 'julia',
    '.ex': 'elixir', '.exs': 'elixir', '.erl': 'erlang', '.hrl': 'erlang',
    '.hs': 'haskell', '.lhs': 'haskell', '.elm': 'elm', '.dart': 'dart',
    '.sql': 'sql', '.sh': 'shell', '.bash': 'shell', '.zsh': 'shell', '.fish': 'fish',
    '.ps1': 'powershell', '.psm1': 'powershell', '.psd1': 'powershell', '.bat': 'batch', '.cmd': 'batch',
    '.html': 'html', '.htm': 'html', '.css': 'css', '.scss': 'scss', '.sass': 'sass', '.less': 'less',
    '.vue': 'vue', '.svelte': 'svelte', '.astro': 'astro', '.xml': 'xml', '.xsl': 'xslt', '.xsd': 'xml',
    '.graphql': 'graphql', '.gql': 'graphql', '.proto': 'protobuf', '.tf': 'hcl', '.hcl': 'hcl',
    '.nix': 'nix', '.sol': 'solidity', '.move': 'move', '.vhd': 'vhdl', '.vhdl': 'vhdl',
    '.sv': 'systemverilog', '.svh': 'systemverilog', '.asm': 'assembly', '.s': 'assembly',
    '.make': 'makefile', '.mk': 'makefile', '.cmake': 'cmake',
}

SOURCE_SUFFIXES = frozenset(LANGUAGE_SUFFIXES)
LANGUAGE_NAMES = frozenset(LANGUAGE_SUFFIXES.values()) | frozenset({'c++', 'c#', 'f#', 'objective c'})
CODE_EXTENSION_PATTERN = '|'.join(re.escape(suffix[1:]) for suffix in sorted(SOURCE_SUFFIXES, key=len, reverse=True))
TEXT_SOURCE_SUFFIXES = SOURCE_SUFFIXES | frozenset({'.godot', '.tscn', '.tres', '.cfg', '.ini', '.json', '.toml', '.yaml', '.yml'})


def language_for_path(path):
    """Return a normalized language label for a recognized source suffix."""
    from pathlib import PurePosixPath
    return LANGUAGE_SUFFIXES.get(PurePosixPath(str(path).replace('\\', '/')).suffix.casefold(), '')


