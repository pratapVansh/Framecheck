# Publish the source to GitHub

Run these commands from the project root after reviewing the changes. If this folder does not already contain a Git repository, first run `git init -b main`.

```bash
git add .
git diff --cached --stat
git status --short
git commit -m "Prepare Framecheck source and documentation"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Create the matching empty GitHub repository and replace the example remote URL with your own. If `origin` is already configured, inspect it with `git remote -v` before deciding whether to update it.

The checked-in `.gitignore` keeps local secrets, uploads, SQLite databases, datasets, generated reports, course bundles, model weights, environments, and cleanup backups out of the publication. Small runtime policies and checkpoint metadata remain included so a fresh checkout can run. Do not use `git add -f` to bypass these boundaries for secrets or private photographs.

The `.gitattributes` file preserves exact bytes of frozen calibration inputs. Do not normalize the protected files' line endings without creating and validating a new calibration version.

Private photo regressions skip when their local files are unavailable. The public suite still tests the single-product flow using procedural images. Historical evaluation and browser scripts require local assets and are documented separately from first-run setup.

No remote repository is created or pushed by the application. No licence for the author's code was selected as part of this cleanup; choose a licence deliberately before offering licensed reuse. Pretrained models and source photographs have their own upstream terms and are not redistributed here.
