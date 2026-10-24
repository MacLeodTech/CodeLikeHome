# Updating

Releases are tagged `vX.Y.Z`. A release is a git tag **and** a forwarder image with the same version, so the two halves can be
pinned and upgraded together.

```sh
cd CodeLikeHome
git fetch --tags && git log --oneline HEAD..origin/main      # what changed; read CHANGELOG.md
git checkout v0.2.0                                          # or: git pull, if you follow main
sed -i 's/^CLH_TAG=.*/CLH_TAG=0.2.0/' .env                    # pin the matching image
docker compose -f compose/codelikehome.yaml --env-file .env up -d      # recreates the forwarder (editor tabs reconnect)
sudo sh host/install.sh --user YOU --dry-run                 # shows host file changes, if any
sudo sh host/install.sh --user YOU
sudo sh host/verify.sh --user YOU
cat /etc/codelikehome/installed-version
```

If you include the compose file from another project, you only need `git checkout` plus the `.env` tag change, then your usual
`docker compose up -d`.

Why pin: the image runs next to your editor with a socket mount. A tag you bump on purpose is better than `latest` changing under
you. Images are scanned before publishing and signed with cosign (keyless): `cosign verify` against the repository's workflow identity if you want to check.

Watching for new releases: GitHub's "Releases only" watch on the repository, or Dependabot/Renovate on the `.env` tag.
