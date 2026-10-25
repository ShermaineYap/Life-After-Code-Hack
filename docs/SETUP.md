# Setup: from this repo to a live, judged submission

Deadline: **Tuesday 27 October 2026, 13:00 UTC (21:00 Malaysia time)**. Submit by
17:00 Malaysia time; Devpost slows down near the deadline.

Everything in this repo is built and tested. What's left needs your accounts:
GitLab onboarding, the GitLab project settings, the flow registration, Google Cloud,
the demo runs, the video and the Devpost form. Steps are in the order to do them.

---

## 1. Accounts (do first; approval takes up to ~24 business hours)

1. Register on Devpost: <https://gitlab-transcend.devpost.com/> → **Join hackathon**.
2. Start contributor onboarding: <https://contributors.gitlab.com/transcend-hackathon>.
   You get a provisioned group, a project and an onboarding issue.
3. **If approval hasn't arrived by Monday 09:00**, start a GitLab Ultimate trial on your
   own top-level group and use a project there. Triggers need Premium/Ultimate; the
   rules only require a public GitLab project.

## 2. Put the code in the GitLab project

In a terminal (replace the GitLab URL with your provisioned project's clone URL):

```bash
git clone https://github.com/ShermaineYap/life-after-code-hack.git
cd life-after-code-hack
git remote add gitlab https://gitlab.com/<your-group>/<your-project>.git
git push gitlab main
```

Then in GitLab:

- **Settings > General > Visibility**: project **Public**. Pipelines visibility: everyone.
- Check the **About** panel on the project page shows **MIT License** (it reads `LICENSE`).
- **Settings > Repository > Protected branches > main**: *Allowed to merge* =
  **Developers + Maintainers** (the flow's service account is a Developer and merges via auto-merge).
  *Allowed to push and merge* = **No one**.
- **Settings > Repository > Push rules**: if any rule is on, make sure it doesn't block
  service accounts (the flow pushes branches).
- **Settings > Merge requests**: enable *Pipelines must succeed*.
- **Settings > CI/CD > Runners**: GitLab-hosted runners **on**.

## 3. Turn on Duo and register the flows

1. **Group** (top level) **> Settings > GitLab Duo > Change configuration**: Agent Platform on,
   **Allow custom flows** on, **Allow foundational flows** on.
2. In the project: **AI > Flows > New flow**
   - Display name `Downstream`, description from the top comment of the file,
     visibility **Public** (so judges can see it in the AI Catalog).
   - Configuration: paste `.gitlab/duo/flows/downstream.yaml`. **Create flow**.
3. Repeat for `Downstream Review` with `.gitlab/duo/flows/downstream-review.yaml`.
   (Only create `downstream-hands-off.yaml` if you switch to Hands-off, step 9.)
4. **Enable** each flow in the project and add triggers:
   - Downstream: **Work item → Created**, type **Issue**. Add a second trigger **Mention**
     as a fallback (`@ai-downstream-<group>` in an issue comment starts it).
   - Downstream Review: **Merge request → Created** and **Assign reviewer**.
5. Note the service account names GitLab creates (`ai-downstream-<group>`). Mention it once
   in a test issue to confirm it autocompletes.

## 4. Google Cloud (for the deploy, and the +0.2 bonus)

Run in **Google Cloud Shell** (console.cloud.google.com → the `>_` icon). New accounts get
free-trial credit; Cloud Run's free tier covers this demo.

```bash
PROJECT=downstream-$RANDOM
REGION=asia-southeast1
gcloud projects create $PROJECT
gcloud config set project $PROJECT
gcloud billing accounts list                     # copy the ACCOUNT_ID
gcloud billing projects link $PROJECT --billing-account=<ACCOUNT_ID>
gcloud services enable run.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create downstream --repository-format=docker --location=$REGION

gcloud iam service-accounts create gitlab-deployer --display-name="GitLab Downstream deployer"
SA=gitlab-deployer@$PROJECT.iam.gserviceaccount.com
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=$ROLE --quiet
done
gcloud iam service-accounts keys create gcp-key.json --iam-account=$SA
echo "GCP_PROJECT_ID=$PROJECT"
cat gcp-key.json                                  # copy the whole JSON, then delete the file
rm gcp-key.json
```

If key creation is blocked by an organisation policy, use a project outside any
organisation (personal account), or ask in the hackathon Discord.

## 5. CI/CD variables (Settings > CI/CD > Variables)

| Key | Value | Type | Flags |
| --- | --- | --- | --- |
| `GCP_PROJECT_ID` | the project ID printed above | Variable | — |
| `GCP_SA_KEY` | the whole key JSON | **File** | Masked if allowed, Protected |
| `GITLAB_PRIVATE_TOKEN` | optional: a project access token (role Developer, scope `api`) so CI can open incidents itself | Variable | Masked, Protected |
| `PRODUCTION_URL` | set after the first prod deploy (step 6) | Variable | — |

Without the GCP variables the pipeline still runs end to end in **dry-run** deploy
mode (the monitor checks the built warehouse instead of a live URL). Use that to get a
green pipeline first.

## 6. First release (creates the baseline every later monitor compares against)

1. **Build > Pipelines > Run pipeline** on `main`.
2. Watch: lineage-review → dbt-build → tests and scans → package → deploy-staging →
   monitor-staging → prepare-release → release `v1.0.0` → `deploy-production` waits (manual).
3. Press **play** on `deploy-production`. monitor-production records the baseline.
4. Copy the production URL from **Operate > Environments > production** into the
   `PRODUCTION_URL` variable, and put it in the Devpost form.
5. **Build > Pipeline schedules > New schedule**: hourly, target `main` (runs `monitor-scheduled`).

## 7. First live flow run: what to check

The flows are written against the published flow registry v1 schema and the Duo tool
registry, and every script they call is tested, but this is their first run in a real
group. Open a throwaway issue ("Add a not_null test to kpi_dashboard.orders") and watch
**AI > Sessions**:

| If you see | Do this |
| --- | --- |
| Flow config rejected on save | Read the error; the usual cause is a key your instance doesn't allow. Remove it and re-save. `pytest pytests/test_flows.py` checks the rest |
| Session queued forever | Runners: hosted runners on, or a runner tagged `gitlab--duo` |
| `pip install` fails in setup | Delete `network_policy` from `.gitlab/duo/agent-config.yml` (or add the blocked domain), commit to `main` |
| `run_command` refused for `python` or `bash` | The instance restricts commands: in the flow YAML give the agent `run_command` only, and ask in Discord which programs are allowed |
| `git push` rejected | Push rules / protected branch settings in step 2 |
| `ds.py` returns HTTP 403 | The flow token can't use that API: do that step in CI instead (`GITLAB_PRIVATE_TOKEN` path) |
| Agent can't find the project ID | The trigger's goal includes the issue URL; the agents read the project from it |

Fix, re-save the flow, close the throwaway issue, then run the demo.

## 8. Record the demo

Follow [`VIDEO.md`](VIDEO.md). File the issues in [`../demo/issues.md`](../demo/issues.md).
Record the real run; trim waiting with cuts, never fake output.

## 9. Optional: Hands-off

Set CI variable `DOWNSTREAM_AUTONOMY=hands-off`, create a flow from
`downstream-hands-off.yaml`, give it the Work item trigger instead of the Supervised
flow. Only switch if three runs in a row finish cleanly; Supervised is the safer entry.

## 10. Submit

Use [`DEVPOST.md`](DEVPOST.md) for the form text.

- [ ] GitLab project public; MIT shown in About; pipeline history shows ≥ 3 full runs incl. a rollback
- [ ] Flow YAML, `agent-config.yml`, `AGENTS.md` committed (they're part of the MIT submission)
- [ ] No secrets in the repo (`git log -p | grep -i private_key` returns nothing)
- [ ] YouTube video public, under 3:00, no music or third-party logos
- [ ] Devpost: Path A, Supervised, repo URL, video URL, Cloud Run URL, Environmental opt-in text
- [ ] Submitted, confirmation email received
