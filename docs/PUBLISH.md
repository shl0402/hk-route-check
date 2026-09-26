# Publish the code on GitHub

The project is prepared locally; no GitHub repository or remote has been created.

Create an empty GitHub repository, then run in this project's folder:

```sh
git init -b main
git add .
git diff --cached --stat
```

The staged files should be code, tests, documentation and three screenshots. The existing `data/`, `tools/`, `.venv/`, logs and cache archives are ignored. The ignore file deliberately uses an allowlist; update it when adding a new source directory.

After checking the staged file list:

```sh
git commit -m "Add Hong Kong routing research preview"
git remote add origin https://github.com/YOUR-NAME/YOUR-REPOSITORY.git
git push -u origin main
```

Replace the placeholder URL with your own repository URL. Keep the repository description clear: **two-location public-transport research checker; multi-stop worker optimisation is not implemented**.

The prepared source-only ZIP is an alternative clean starting folder. Extract it before committing; do not commit the ZIP itself.

The optional `dist/hk-routing-source-cache.tar.gz` is separate. It can be regenerated with `python3 run.py export-cache`. If you later publish a cache release, first resolve the included source data's redistribution terms and add the release link to README. Do not force-add the cache or generated GTFS/graph to Git.
