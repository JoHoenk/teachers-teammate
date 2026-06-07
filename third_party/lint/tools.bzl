"""Module extension that pins ruff and ty to explicit, reproducible versions.

aspect_rules_lint ships ruff/ty as built-in tools via @multitool, but the
resolved version is invisible from this repo, which makes it easy for a locally
pip-installed `ruff` (run from an activated .venv) to silently diverge from the
version Bazel/CI use. Pinning here makes the versions explicit and the *only*
source: run linters through Bazel (`bazel test //:lint`), never a venv tool.

The versions below match what aspect_rules_lint 2.6.0 bundles today; the sha256
hashes are copied verbatim from rules_lint's multitool lockfile. To bump, fetch
the desired release from https://github.com/astral-sh/{ruff,ty}/releases and
update the version + hashes.
"""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

RUFF_VERSION = "0.15.13"
TY_VERSION = "0.0.37"

# (repo_name, target_triple, archive_ext, sha256)
_RUFF = [
    ("ruff_linux_aarch64", "aarch64-unknown-linux-musl", "tar.gz", "1084d8038eeb51f55c7b7c9779bb37e5f53b28b987767df61333e9d0da7b2701"),
    ("ruff_linux_x86_64", "x86_64-unknown-linux-musl", "tar.gz", "66ac0b0582c8d24badc210b8e933e9aa0ad6707414e236409c9b9ad638972fb9"),
    ("ruff_macos_aarch64", "aarch64-apple-darwin", "tar.gz", "66ab3abeeba319266612e675dfbe0ca9c63f1e5f6ca3c6174482b0b088b8f6ff"),
    ("ruff_macos_x86_64", "x86_64-apple-darwin", "tar.gz", "3ff692a61d5231ea6c94e6dafc575f87582a089d1281fac6b31cef2b4f2e4b6b"),
    ("ruff_windows_x86_64", "x86_64-pc-windows-msvc", "zip", "59892c6773df805effe7928e69796447585b42af54310cb6f1552536b746c39a"),
]

_TY = [
    ("ty_linux_aarch64", "aarch64-unknown-linux-musl", "tar.gz", "6e46d949df5ce2a71db0fac9b26fa200c47a11564f01150df8d6b4a7c6da91f9"),
    ("ty_linux_x86_64", "x86_64-unknown-linux-musl", "tar.gz", "d9eabccb839fb66caba88a0ef4c234541fca113c6a60d1a8de939d06d927102b"),
    ("ty_macos_aarch64", "aarch64-apple-darwin", "tar.gz", "b7e140d394bfbe497f8fae5f91c820687b7ea9313478102a0b624aa246d54b00"),
    ("ty_macos_x86_64", "x86_64-apple-darwin", "tar.gz", "48bb648437430d17ee05177f116fb6721e8bf0a10b3a0f68f55a9fb039f40742"),
    ("ty_windows_x86_64", "x86_64-pc-windows-msvc", "zip", "a79ab5c10139ddc66a64128bd90ed3693c4cdbac50858398f503f1e8553944db"),
]

def _archive(tool, version, repo, triple, ext, sha):
    is_win = ext == "zip"
    binary = tool + (".exe" if is_win else "")
    url = "https://github.com/astral-sh/{tool}/releases/download/{ver}/{tool}-{triple}.{ext}".format(
        tool = tool,
        ver = version,
        triple = triple,
        ext = ext,
    )
    if is_win:
        http_archive(
            name = repo,
            urls = [url],
            sha256 = sha,
            build_file_content = 'exports_files(["%s"])' % binary,
        )
    else:
        http_archive(
            name = repo,
            urls = [url],
            sha256 = sha,
            strip_prefix = "{tool}-{triple}".format(tool = tool, triple = triple),
            build_file_content = 'exports_files(["%s"])' % binary,
        )

def _impl(_ctx):
    for repo, triple, ext, sha in _RUFF:
        _archive("ruff", RUFF_VERSION, repo, triple, ext, sha)
    for repo, triple, ext, sha in _TY:
        _archive("ty", TY_VERSION, repo, triple, ext, sha)

lint_tools = module_extension(implementation = _impl)
