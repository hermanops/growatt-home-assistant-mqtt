# Releasing

This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and keeps its history in [CHANGELOG.md](CHANGELOG.md).

## Source of truth

The **git tag** `vX.Y.Z` is the only place the version number is stored. There
is no version string in the code, so nothing can drift from the tag. A release
is simply a signed-off, annotated tag on `master`.

## Version policy

Given a version `MAJOR.MINOR.PATCH`:

- **MAJOR** — incompatible changes: MQTT topic or Home Assistant discovery
  layout changes, config format changes, or a raised Python floor that an
  existing install must migrate for. Record the migration in
  [MIGRATION.md](MIGRATION.md).
- **MINOR** — backwards-compatible additions: new sensors, new optional config
  keys, new decoded registers.
- **PATCH** — backwards-compatible fixes only: decoding, timing, service, or
  docs corrections with no new behaviour.

Dependency bumps from Dependabot are `PATCH` unless they change the required
callback/Modbus API surface, in which case treat them as `MINOR`/`MAJOR`.

## Release process

1. Ensure `master` is green (CI passes) and has the changes to ship.
2. Update `CHANGELOG.md`:
   - Rename the `## [Unreleased]` heading to `## [X.Y.Z] - YYYY-MM-DD`.
   - Add a fresh empty `## [Unreleased]` section above it.
   - Update the compare links at the bottom of the file.
3. Open a PR for the changelog update, review, and merge to `master`.
4. Tag the merge commit on `master` and push it:

   ```bash
   # Host: development machine, from the repository root, on up-to-date master
   git checkout master && git pull --ff-only
   git tag -a "vX.Y.Z" -m "Release vX.Y.Z"
   git push origin "vX.Y.Z"
   ```

Pushing the `vX.Y.Z` tag triggers `.github/workflows/release.yml`, which
creates the GitHub Release using the matching `CHANGELOG.md` section as the
release notes. No manual release notes are needed.

## Deploying a release to a Pi

The service tracks `master` via `scripts/update.sh` (`git pull --ff-only`); it
does not check out tags. To run a specific release on a Pi, fast-forward
`master` to that release's commit, or check the tag out explicitly, then run
`scripts/update.sh` as the checkout owner (not root).
