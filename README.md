# eic-ask

`eic-ask` is a small Python CLI for querying the EIC Documentation API from the shell.

## Usage

```console
eic-ask What is a good example for podio data analysis in C++?
```

Pipe input on stdin if you prefer:

```console
printf '%s\n' "How do I set up eic-shell?" | eic-ask
```

Useful options:

- `-e, --endpoint` — override the API URL
- `-t, --timeout` — set the request timeout in seconds
- `--json` — print the full response as formatted JSON
- `--no-references` / `--hide-references` — suppress numbered references printed below the answer

Responses include numbered references below the answer text by default when the API provides them.

Authentication is optional and can be provided via `EIC_ASK_TOKEN`.

## Local development

Requirements:

- Python 3.10+

Recommended workflow:

```console
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

That `pip install -e .` step installs the `eic-ask` launcher into the
environment's `bin/` directory, which is on `PATH` after activation.

To run the CLI from the checkout without installing it, use:

```console
python -m eic_ask --help
```
