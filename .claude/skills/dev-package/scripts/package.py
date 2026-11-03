#!/usr/bin/env python3
"""Build a minimal distributable package of the yt-media-feed-helper marketplace + plugin (stdlib only).

Contents = tracked files only (git ls-files), filtered by an allowlist:
  .claude-plugin/marketplace.json, README.md, LICENSE, docs/ARCHITECTURE.md, docs/SECURITY.md,
  yt-media-feed-helper/** except tests/ — but tests/fixtures/{youtube,steam}/ are kept (mock mode reads them at runtime).
Relative Markdown links to files that are not packaged are rewritten to the GitHub URL of the source repo.

Output (in --out, default <repo>/dist):
  yt-media-feed-helper-<version>/            staged marketplace root (can be added with /plugin marketplace add <dir>)
  yt-media-feed-helper-<version>.zip         reproducible zip (sorted entries, fixed timestamps) of that folder
  the staged folder contains SHA256SUMS for every packaged file.

Checks: refuses a dirty working tree (tracked files) unless --allow-dirty; denylist guard; Markdown link check;
`claude plugin validate --strict` on marketplace + plugin (if `claude` is on PATH); MCP smoke test of the packaged
server in mock mode over stdio (needs a Python with the plugin dependencies: --python, default = this interpreter).

Usage (repo root):  .venv\\Scripts\\python.exe -I .claude\\skills\\dev-package\\scripts\\package.py
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

PLUGIN = "yt-media-feed-helper"
INCLUDE = [
    ".claude-plugin/marketplace.json",
    "README.md",
    "LICENSE",
    "docs/ARCHITECTURE.md",
    "docs/SECURITY.md",
    f"{PLUGIN}/*",
]
EXCLUDE = [
    f"{PLUGIN}/tests/*",
]
KEEP_ANYWAY = [  # runtime data under an excluded folder
    f"{PLUGIN}/tests/fixtures/youtube/*",
    f"{PLUGIN}/tests/fixtures/steam/*",
]
NEVER = [  # must not appear in a package even if tracked by mistake
    "docs-internal/*", ".venv/*", "*/__pycache__/*", "*.pyc", ".claude/*", "CLAUDE.md", "CLAUDE.local.md",
    "*token*.json", "client_secret*", "credentials*.json", "*.jsonl", ".env", "dist/*",
]
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
LINK = re.compile(r"(\[[^\]]*\]\()([^)\s]+)(\))")


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, **kw)


def match(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def select(files: list[str]) -> list[str]:
    out = []
    for f in files:
        if match(f, NEVER):
            continue
        if not match(f, INCLUDE):
            continue
        if match(f, EXCLUDE) and not match(f, KEEP_ANYWAY):
            continue
        out.append(f)
    return sorted(out)


def github_base(repo: Path) -> str | None:
    r = run(["git", "remote", "get-url", "origin"], repo)
    url = r.stdout.strip()
    m = re.match(r"(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?$", url)
    return f"https://github.com/{m.group(1)}" if m else None


def rewrite_links(stage: Path, packaged: set[str], gh: str | None, ref: str) -> list[str]:
    """Relative links to non-packaged files → GitHub URLs. Returns problems (broken links that could not be fixed)."""
    problems = []
    for rel in sorted(p for p in packaged if p.endswith(".md")):
        f = stage / rel
        text = f.read_text(encoding="utf-8")

        def fix(m: re.Match, rel: str = rel) -> str:
            target = m.group(2)
            if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^<", target, re.I):
                return m.group(0)  # absolute URL, anchor or placeholder
            path, _, anchor = target.partition("#")
            resolved = str(PurePosixPath(rel).parent / path)
            norm = os.path.normpath(resolved).replace("\\", "/")
            if norm in packaged or (stage / norm).is_dir():
                return m.group(0)
            if gh:
                kind = "tree" if path.endswith("/") else "blob"
                return f"{m.group(1)}{gh}/{kind}/{ref}/{norm}{'#' + anchor if anchor else ''}{m.group(3)}"
            problems.append(f"{rel}: broken link {target}")
            return m.group(0)

        new = LINK.sub(fix, text)
        if new != text:
            f.write_text(new, encoding="utf-8", newline="\n")
    return problems


def write_zip(stage: Path, zip_path: Path) -> None:
    root = stage.name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo(f"{root}/{p.relative_to(stage).as_posix()}", date_time=FIXED_ZIP_TIME)
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())


def mcp_smoke(stage: Path, python: str) -> tuple[bool, str]:
    """Start the PACKAGED server in mock mode over stdio, list tools, run one fixture-backed call.

    Runs on a throwaway copy of the staged folder: Python writes __pycache__ next to the modules it imports,
    which must not end up in the package."""
    with tempfile.TemporaryDirectory(prefix="ytfeed-pkg-", ignore_cleanup_errors=True) as tmp:
        copy = Path(tmp) / "pkg"
        shutil.copytree(stage, copy)
        launch = copy / PLUGIN / "server" / "launch.py"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("YTFEED_", "CLAUDE_"))}
        env.update(YTFEED_SKIP_VENV="1", YTFEED_MOCK="1", YTFEED_WORKSPACE=str(Path(tmp) / "ws"),
                   YTFEED_DATA_DIR=str(Path(tmp) / "data"), YTFEED_SECRETS_DIR=str(Path(tmp) / "secrets"), PYTHONUTF8="1")
        proc = subprocess.Popen([python, "-I", str(launch), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=tmp, env=env, text=True, encoding="utf-8")

        def rpc(i, method, params):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError("server closed stdout: " + proc.stderr.read()[-400:])
                msg = json.loads(line)
                if msg.get("id") == i:
                    return msg

        try:
            init = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "package-smoke", "version": "1"}})
            proto = init.get("result", {}).get("protocolVersion", "?")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.flush()
            tools = rpc(2, "tools/list", {})["result"]["tools"]
            call = rpc(3, "tools/call", {"name": "catalog_sync_subscriptions", "arguments": {"dry_run": True}})
            env_out = json.loads(call["result"]["content"][0]["text"])
            added = len((env_out.get("data") or {}).get("added") or [])
            ok = len(tools) > 0 and env_out.get("ok") is True and added > 0
            return ok, f"protocol {proto}, {len(tools)} tools, mock sync dry-run found {added} channels"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
        finally:
            stop(proc)


def stop(proc: subprocess.Popen) -> None:
    """Graceful MCP shutdown: close stdin (server exits on EOF). launch.py runs the server as a CHILD process, so a plain
    kill() of the launcher would orphan it (and keep cache.sqlite locked on Windows) — kill the whole tree as a fallback."""
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=15)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()
    proc.wait(timeout=10)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="repo root (default: current directory)")
    ap.add_argument("--out", default=None, help="output folder (default: <repo>/dist)")
    ap.add_argument("--python", default=sys.executable, help="Python with plugin deps for the MCP smoke test")
    ap.add_argument("--ref", default=None, help="git ref used in rewritten GitHub links (default: v<version> if that tag exists, else main)")
    ap.add_argument("--allow-dirty", action="store_true", help="package even with uncommitted changes to tracked files")
    ap.add_argument("--skip-validate", action="store_true", help="skip `claude plugin validate`")
    ap.add_argument("--skip-smoke", action="store_true", help="skip the MCP smoke test")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    if not (repo / ".claude-plugin" / "marketplace.json").is_file():
        log(f"ERROR: {repo} is not the marketplace root")
        return 2
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], repo).stdout.strip()
    if dirty and not a.allow_dirty:
        log("ERROR: uncommitted changes to tracked files (commit first or pass --allow-dirty):\n" + dirty)
        return 2

    version = json.loads((repo / PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    mk_version = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["plugins"][0].get("version")
    problems: list[str] = []
    if mk_version and mk_version != version:
        problems.append(f"version mismatch: plugin.json {version} vs marketplace.json {mk_version}")

    files = run(["git", "ls-files", "-z"], repo).stdout.split("\0")
    chosen = select([f for f in files if f])
    out = Path(a.out).resolve() if a.out else repo / "dist"
    name = f"{PLUGIN}-{version}"
    stage = out / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for rel in chosen:
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / rel, dst)

    tag = f"v{version}"
    ref = a.ref or (tag if run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], repo).returncode == 0 else "main")
    problems += rewrite_links(stage, set(chosen), github_base(repo), ref)

    leaked = [str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file() and match(p.relative_to(stage).as_posix(), NEVER)]
    if leaked:
        problems.append(f"forbidden files in package: {leaked}")
    if not list((stage / PLUGIN / "tests" / "fixtures" / "youtube").glob("*.json")):
        problems.append("mock fixtures missing (tests/fixtures/youtube)")
    if list((stage / PLUGIN / "tests").glob("test_*.py")):
        problems.append("test code leaked into the package")

    sums = []
    for p in sorted(stage.rglob("*")):
        if p.is_file():
            sums.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")

    if not a.skip_validate:
        claude = shutil.which("claude")
        if not claude:
            log("WARN: `claude` not on PATH — skipped plugin validation")
        else:
            for target in (stage, stage / PLUGIN):
                r = run([claude, "plugin", "validate", "--strict", str(target)], repo)
                last = (r.stdout.strip().splitlines() or [""])[-1]
                log(f"validate {target.relative_to(out)}: {last}")
                if r.returncode != 0:
                    problems.append(f"claude plugin validate failed for {target.name}: {r.stdout[-400:]}")
    if not a.skip_smoke:
        ok, detail = mcp_smoke(stage, a.python)
        log(f"MCP smoke (packaged server, mock mode): {'OK' if ok else 'FAILED'} — {detail}")
        if not ok:
            problems.append("MCP smoke test failed: " + detail)

    # Re-check right before zipping: the staged folder must contain exactly the checksummed files.
    listed = {line.split("  ", 1)[1] for line in sums}
    actual = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    if actual != listed:
        problems.append(f"staged folder changed after checksums: extra {sorted(actual - listed)[:5]}, missing {sorted(listed - actual)[:5]}")

    zip_path = out / f"{name}.zip"
    write_zip(stage, zip_path)
    size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
    log(f"\nPackage: {stage}\nZip:     {zip_path} ({zip_path.stat().st_size:,} bytes; staged {len(sums)} files, {size:,} bytes)")
    log(f"Links to non-packaged files point to ref: {ref}")
    if problems:
        log("\nPROBLEMS:\n- " + "\n- ".join(problems))
        return 1
    log("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
