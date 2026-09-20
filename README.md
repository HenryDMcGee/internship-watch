# Internship Watch

Finds Summer 2027 MBA product internships every morning and shows them on a private dashboard. Claude Code builds it through GitHub Actions, and every change goes through a pull request you review.

- The approved plan: `docs/plan.md`
- The rules Claude Code follows here: `CLAUDE.md`
- Your job titles: `config/titles.json`
- Deploying to Cloudflare: `docs/deploy.md`

## Start the build

### 1. Set up the repository

1. Create a **private** repository named `internship-watch` on GitHub. Adding a README is optional; the one in this starter pack replaces it either way.
2. Run `claude setup-token` on your computer and sign in. Copy the token it shows.
3. In the repository, go to **Settings**, then **Secrets and variables**, then **Actions**. Add a secret named `CLAUDE_CODE_OAUTH_TOKEN` and paste the token. Never paste it anywhere else.
4. Install the Claude GitHub app on this repository only. In Claude Code, `/install-github-app` walks you through it.
   - This starter pack already includes the Claude workflow, `.github/workflows/claude.yml`. If the installer adds its own workflow file, delete one of the two before pushing. Two Claude workflows would make Claude run twice on every `@claude`.

### 2. Add these files

Push this starter pack to `main` **before** you protect the branch. Branch protection would block this first direct push.

If the offline snapshot you downloaded earlier is in the same folder, rename it first so the clone doesn't collide with it. That snapshot also unzips to a folder named `internship-watch`.

```bash
mv internship-watch internship-watch-snapshot   # only if the old snapshot folder is here
git clone https://github.com/<your-username>/internship-watch.git
cd internship-watch
# copy everything from this starter pack into the folder, including .github and .gitignore
git add -A
git commit -m "Add starter pack"
git branch -M main   # makes sure your branch is called main
git push -u origin main
```

On a Mac, the `.github` folder and `.gitignore` are hidden in Finder. Press Cmd+Shift+. to show them, or copy the files with the terminal.

### 3. Protect main

In **Settings**, add a branch rule for `main` that requires a pull request before merging. From now on, nothing reaches `main` without your review.

### 4. Build the four phases

Each phase has its issue text in `issues/`. Do them in order, and start the next one only after you merge the previous phase's pull request.

For each phase:
1. Open a new issue. Use the first line of the phase file as the title, and paste everything below the `---` line as the body.
2. The body ends with `@claude`, which starts Claude Code. Follow along in the **Actions** tab.
3. Review the pull request Claude opens. Comment `@claude` on it to ask for changes, and merge when you're happy.
4. Do the "after merging" steps listed in the pull request description, then deploy with `docs/deploy.md`.

| Phase | File | Before you open it |
| --- | --- | --- |
| 1. Local version | `issues/phase-1.md` | Starter pack pushed, `main` protected |
| 2. Daily job | `issues/phase-2.md` | Database ID, database token, and account ID saved in GitHub |
| 3. Claude agent | `issues/phase-3.md` | Routine URL and token saved in GitHub |
| 4. Automatic deploys | `issues/phase-4.md` | Deploy token saved in GitHub, with R2 permission |

Only your own comments can start Claude. Mentions from anyone else are ignored.

## Usage

Building a phase is a long Claude Code session on your Claude subscription. It can use a large share of one five-hour usage window, so start a phase when you don't need Claude for anything else for a while.

## Seed data

`seed/` holds a snapshot of the dashboard you use today, taken September 19, 2026: 9 postings, 30 sources, 2 search log entries, 100 companies, and your settings (US and Japan). Phase 1 loads it into the local database.
