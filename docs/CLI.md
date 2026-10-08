# `stagr` CLI

`stagr` is the command line of the agentic-foundation control plane. It is a thin, deterministic layer
over the neutral core (`stagr/core/`): no network, and **no secret values are ever read, printed, or
logged** — only the secret *names* the contract references.

**Today the commands are `stagr help`, `stagr init`, `stagr profile`, `stagr plan` and `stagr apply`.**
`init` writes a starter `.agentic/config.yml`, `profile` lists or switches its profile, and `plan` and
`apply` turn that config into workflow files. `doctor` is planned, see
[Planned commands](#planned-commands).

## What you need

- **Python 3.10 or newer.** Check with `python3 --version` (Linux/macOS) or `py --version` (Windows —
  the launcher, since a default Windows install exposes `py`/`python`, not `python3`). That is the only
  prerequisite — `stagr` is pure Python and its two dependencies (PyYAML, jsonschema) install
  automatically.
- **[pipx](https://pipx.pypa.io)** is the recommended installer: it puts `stagr` on your `PATH` in an
  isolated environment so it never clashes with other Python tools. If you don't have it, bootstrap it,
  add its shims to `PATH`, and open a new terminal:
  ```bash
  # Linux / macOS
  python3 -m pip install --user pipx && python3 -m pipx ensurepath
  # Windows (py launcher)
  py -m pip install --user pipx && py -m pipx ensurepath
  ```
  `ensurepath` is what makes the `pipx` command available in the next shell. Before that PATH entry is
  active you can still invoke it as a module — `python3 -m pipx install …` (or `py -m pipx install …`)
  — which is equivalent to the `pipx …` commands below.

`stagr` runs the same way on **Linux, macOS, and Windows** — one Python package, one command.

## Install

> **Not on PyPI yet.** Until the first release, install from the repository's source archive — pip/pipx
> download and build it with **no `git` required** (so it works on a clean Python-only machine,
> including Windows). For a reproducible, auditable install, pin to an **immutable revision** — a
> commit SHA, or a release tag listed in [CHANGELOG.md](../CHANGELOG.md); use `main` only for the latest
> evaluation build:
>
> ```bash
> # a release, by its tag:
> pipx install "https://github.com/exepex/agentic-foundation/archive/refs/tags/v0.5.2.tar.gz"
> # reproducible — replace <commit> with a specific commit SHA:
> pipx install "https://github.com/exepex/agentic-foundation/archive/<commit>.tar.gz"
> # or the latest tip of main (evaluation only, mutable):
> pipx install "https://github.com/exepex/agentic-foundation/archive/refs/heads/main.tar.gz"
> ```
>
> or, from a local checkout of this repository: `pipx install .` (or `pip install .`).

Once published, the standard install will be:

```bash
pipx install stagr        # isolated global command on Linux / macOS / Windows
pip install stagr         # or into the current environment / CI
```

Verify it:

```bash
stagr help
```

## Commands

### `stagr help`

Discover commands without leaving the terminal:

```bash
stagr help          # list every command with its purpose
stagr help <command>  # detail for one command (also: `stagr <command> help`)
```

### `stagr init`

```bash
stagr init [--app-id ID] [--profile minimal|standard] [--force] [--root DIR]
```

Onboards a repository: writes `<root>/.agentic/config.yml`, the one file `plan` and `apply` read.

- **Asks** for the numeric ID of your Stagr publisher GitHub App (see
  [CONFIGURATION.md](CONFIGURATION.md), "Publisher"), or takes it from `--app-id`. Without a terminal
  (for example in a script), `--app-id` is required.
- **`--profile`** picks the starter stage graph, `standard` by default; what each profile contains is
  under `profile` in [CONFIGURATION.md](CONFIGURATION.md).
- **Checks** the config it wrote with the same validation as `stagr plan` (it does not look at the
  workflow files), then prints how many pipeline files the config produces.
- **Never overwrites** an existing config unless you pass `--force`. Like `apply`, it refuses to write
  through a symlink, and it does not create a missing `--root`.
- **`--force` regenerates** an existing config as the full template. It keeps the config's profile and
  publisher App ID unless you pass `--profile` or `--app-id`; a `custom` config needs `--profile`.
  Every other active setting goes back to its commented form; `init` lists each one it commented
  out, and separately each one the template does not offer at all, which you add back by hand. If
  the check fails or you cancel, the replaced config's content is put back unchanged.
- **Writes the full template.** Only `version`, `profile` and the publisher App are active. Every
  other option that changes what Stagr generates (platform trust settings, provider secrets, the
  stage catalog, the fast path, `remediation`) is present but commented out, under a short comment on
  what it does. Uncomment what you want, then run `stagr plan`. The profile guide and the stage
  catalog are generated from the profile definitions, so they always match what a profile turns on.
- **Shows the allowed values.** A setting with a fixed set of values carries an end-of-line comment
  naming them. The comment is generated from the schema and from what the installed Stagr can
  render, so it cannot go stale.

A typical first run in a new repository:

```bash
stagr init        # asks for the App ID, writes .agentic/config.yml
stagr plan        # preview the pipeline files
stagr apply       # write them under .github/workflows/
```

### `stagr profile`

```bash
stagr profile [--root DIR]            # list the profiles; the one the config uses is marked *
stagr profile <name> [--root DIR]     # switch the config to <name>
```

- **Lists** every profile with the stages it expands to. The text comes from the profile definitions,
  so it always matches what `plan` produces.
- **Switches** by rewriting only the config's top-level `profile:` line; every other line and comment
  stays. It then checks the config with the same validation as `stagr plan`. If the new profile does
  not validate (for example `custom` with no `stages`), the config is restored and the command exits 1.
- It never touches the workflow files: run `stagr plan` to see the effect, then `stagr apply`.

```bash
stagr profile minimal   # standard -> minimal
stagr plan              # review unchanged, governance changed, stage-security.yml: remove
stagr apply             # writes the changes and deletes stage-security.yml
```

### `stagr plan` and `stagr apply`

```bash
stagr plan  [--root DIR] [--force]    # validate, render, list the changes; writes nothing
stagr apply [--root DIR] [--force]    # validate, render, make those same changes
```

`--root` is the project root (default: the current directory). Both commands read
`<root>/.agentic/config.yml`, and `apply` writes and removes files under `<root>`.

They run **one shared pipeline**: read the config, run the static checks (the list and their status
are in [design-docs/07-validation.md](../design-docs/07-validation.md)), render every file, compare with what is on disk, and find generated files the config no
longer produces. Only the last step differs — `plan` prints the list, `apply` makes those changes
(write or remove) and prints the same list. So a config that `plan` accepts is a config
`apply` accepts (`apply` can still fail on the file system, for example a read-only directory), and
an invalid config fails both with the same message and exit code 1. `apply` renders everything
before it writes anything, so a validation error never leaves a half-written set.

Each line shows what would happen to a file, its size, and the start of its SHA-256 hash:

```
plan: 4 file(s) under .; nothing was written
  new       .github/workflows/stage-review.yml  72280 bytes  sha256:2a5ea4ba8697
  ...
```

- **`new`** — the file does not exist. **`changed`** — it exists with different content, and `apply`
  replaces it. **`unchanged`** — identical; `apply` leaves it alone, so running `apply` twice changes
  nothing. **`remove`** — a file Stagr generated earlier that the config no longer produces (for
  example after switching profile or disabling a stage); `apply` deletes it.
- **Files written:** one `.github/workflows/stage-<stage id>.yml` per enabled stage, plus
  `.github/workflows/routing.yml` and `.github/workflows/governance.yml`. Each starts with the line
  `# Generated by stagr from .agentic/config.yml. …`, which marks it as Stagr's.
- **`resolve-outdated-threads.yml`** is also written when a stage's review backend posts review
  threads (Codex does). On each new commit of a pull request, and each submitted review, it resolves
  the threads a commit made outdated, when every comment in the thread came from such a backend. With `remediation` on, it also
  resolves a thread the agent answered as fixed. A thread with any other comment, such as a human's or
  the agent's decline, is left open. Only same-repository pull requests from a trusted author run it. A finding
  that is still real is raised again by the review of the new commit.
  It uses the platform token secret (section 2 of [CONFIGURATION.md](CONFIGURATION.md)).
- **`remediation.yml`** is written when the config has a `remediation` section: the agent that fixes
  or declines review findings ([CONFIGURATION.md](CONFIGURATION.md), `remediation`).
- **Files removed:** only files in `.github/workflows/` that start with that line and that the config
  no longer produces. A workflow without that line (your own workflows) is never listed, changed or
  deleted, and a symlink is never followed.
- **Refuses** to overwrite a workflow Stagr did not generate: if one of your own files already sits at
  a path Stagr writes (for example your own `routing.yml`), `plan` and `apply` stop with an error and
  write nothing. Rename or remove that file, or take it over with `--force`.
- **`--force`** takes ownership of every path Stagr generates: `plan --force` lists, and
  `apply --force` makes, the changes as if every file there were Stagr's. Files at produced paths are
  overwritten even without the `Generated by stagr` line. Stale files written by an older Stagr are
  removed: a name Stagr generates (`stage-*.yml`, `routing.yml`, `governance.yml`,
  `resolve-outdated-threads.yml`, `remediation.yml`) and a first line
  `name: "Stagr …"`. Other workflows, such as your own `stage-deploy.yml`, are never removed. Use it to
  take over files from an older Stagr release, or a file you want Stagr to replace:

  ```bash
  stagr profile minimal
  stagr plan --force      # preview what is overwritten and removed
  stagr apply --force
  ```
- **Needs** `platform.publisher.app_id` in the config: the numeric ID of the Stagr GitHub App that
  publishes the check runs (see [CONFIGURATION.md](CONFIGURATION.md)).
- **Refuses** to write through a symlink or over a directory, so a file can never land outside the
  root.
- Warnings (for example V-S11, a disabled fast path that still lists routing keys) go to stderr and do
  not fail the command.

## Planned commands

These do not exist yet. They are described here so the design is visible; do not rely on them.

- **`stagr doctor`** — validate the config and list the secret **names** the pipeline needs.

Renderers only return the files they would produce and never write them; `plan` lists those files plus
the stale generated files to remove, and `apply` makes exactly those changes, so what `plan` shows is
what `apply` does.

## Design rules

- **No network.** The CLI never calls out.
- **Secrets by name only.** The CLI reads the config, which references secrets by name; it never
  reads the environment for a secret value and never prints one.
- **Fail loud.** An invalid config or an unsupported contract shape stops the command with a precise
  error rather than producing a broken pipeline.

## Build the package (maintainers)

The CLI and its data (schema + `templates/`) live in the `stagr/` package, so a standard build ships
everything needed:

```bash
pip install build
python -m build            # writes dist/stagr-<version>-py3-none-any.whl and .tar.gz
pipx install dist/stagr-*.whl   # smoke-test the built wheel
```

The single wheel is what every install path uses (`pipx`, `pip`, and — later — any OS package that
wraps it). The project is licensed (Business Source License 1.1); publishing to PyPI is a future
step — reserve the `stagr` name and run `twine upload dist/*`.
