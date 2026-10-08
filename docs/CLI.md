# `stagr` CLI

`stagr` is the command line of the agentic-foundation control plane. It is a thin, deterministic layer
over the neutral core (`stagr/core/`): no network, and **no secret values are ever read, printed, or
logged** — only the secret *names* the contract references.

**Today the commands are `stagr help`, `stagr init`, `stagr plan` and `stagr apply`.** `init` writes a
starter `.agentic/config.yml`; `plan` and `apply` turn that config into workflow files. `doctor` is
planned, see [Planned commands](#planned-commands).

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
> commit SHA (or a release tag once one exists); use `main` only for the latest evaluation build:
>
> ```bash
> # reproducible — replace <commit> with a specific commit SHA (or a release tag):
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
stagr init [--app-id ID] [--profile minimal|standard] [--root DIR]
```

Onboards a repository: writes `<root>/.agentic/config.yml`, the one file `plan` and `apply` read.

- **Asks** for the numeric ID of your Stagr publisher GitHub App (see
  [CONFIGURATION.md](CONFIGURATION.md), "Publisher"), or takes it from `--app-id`. Without a terminal
  (for example in a script), `--app-id` is required.
- **`--profile`** picks the starter stage graph, `standard` by default; what each profile contains is
  under `profile` in [CONFIGURATION.md](CONFIGURATION.md).
- **Checks** the config it wrote with the same pipeline as `stagr plan`, then prints how many pipeline
  files `plan` will list.
- **Never overwrites** an existing config: edit that file, or delete it to start over. Like `apply`, it
  refuses to write through a symlink, and it does not create a missing `--root`.

A typical first run in a new repository:

```bash
stagr init        # asks for the App ID, writes .agentic/config.yml
stagr plan        # preview the pipeline files
stagr apply       # write them under .github/workflows/
```

### `stagr plan` and `stagr apply`

```bash
stagr plan  [--root DIR]    # validate, render, list the files; writes nothing
stagr apply [--root DIR]    # validate, render, write those same files
```

`--root` is the project root (default: the current directory). Both commands read
`<root>/.agentic/config.yml`, and `apply` writes under `<root>`.

They run **one shared pipeline**: read the config, run the static checks (the list and their status
are in [design-docs/07-validation.md](../design-docs/07-validation.md)), render every file, compare with what is on disk. Only the last step differs — `plan` prints
the list, `apply` writes it and prints the same list. So a config that `plan` accepts is a config
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
  nothing.
- **Files written:** one `.github/workflows/stage-<stage id>.yml` per enabled stage, plus
  `.github/workflows/routing.yml` and `.github/workflows/governance.yml`. Other workflows in that
  directory are never touched, and nothing is deleted (removing files left behind by a renamed or
  disabled stage is a separate, later step).
- **Needs** `platform.publisher.app_id` in the config: the numeric ID of the Stagr GitHub App that
  publishes the check runs (see [CONFIGURATION.md](CONFIGURATION.md)).
- **Refuses** to write through a symlink or over a directory, so a file can never land outside the
  root.
- Warnings (for example V-S11, a disabled fast path that still lists routing keys) go to stderr and do
  not fail the command.

## Planned commands

These do not exist yet. They are described here so the design is visible; do not rely on them.

- **`stagr doctor`** — validate the config and list the secret **names** the pipeline needs.

Renderers only return the files they would produce and never write them; `plan` lists that result and
`apply` writes it, so what `plan` shows is what `apply` writes.

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
