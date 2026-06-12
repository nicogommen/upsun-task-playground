# Task container mid-run access: SSH and logs

Empirical findings on what a user can access **while a task is running**: SSH into the task container, and live logs. Probed on 2026-06-12 against project `vdaznsr6gfmd2` (env `main`, region `eu-3`), activity `emrwjvcjlzmf2`. Not part of the playground spec itself, this is platform behavior research.

## How it was tested

`coding-agent/run.py` has an `AGENT_SLEEP` probe mode (commit `bd7450e`): when the trigger payload contains `variables.env.AGENT_SLEEP`, the task idles for that many seconds, printing a heartbeat to stdout and stderr every 10 seconds and writing to candidate log files. No Anthropic call, no branch, no PR. This holds a side-effect-free run open long enough to probe:

```bash
upsun e:curl -p vdaznsr6gfmd2 -e main tasks/coding-agent/run \
  -X POST -d '{"variables":{"env":{"AGENT_SLEEP":"300"}}}'
```

## Summary

| Probe | Mid-run result |
|---|---|
| `upsun ssh --app coding-agent` | Fails: `WebApp not found` (CLI resolves webapps only) |
| Plain `ssh` with bare task name (`...--coding-agent@...`) | Rejected by gateway: "The service doesn't exist" |
| Plain `ssh` with **full container name** (`...--coding-agent--task--emrwjvcj@...`) | **Works.** Shell as user `web`, full filesystem and process access |
| Same SSH after the run completes | Revoked: "Access denied" (control SSH to `admin` app with same cert still works) |
| `upsun log --app coding-agent` | Fails: `WebApp not found` |
| `upsun activity:log <id>` (default follow) | **Works.** Streams stdout + stderr live, ~5 to 15 s latency |
| `upsun p:curl 'activities/<id>/log?max_delay=0'` | **Works.** JSONL snapshot, no long-poll |
| `upsun apps` mid-run | Task never appears (only `frontend`, `admin`) |
| `e:curl deployments/current` | Has a `tasks` key listing `coding-agent` alongside `webapps` |

## SSH into a running task

The SSH gateway accepts the **full container name**, which is the task name suffixed with `--task--` and the first 8 characters of the activity ID. The activity log preamble prints it:

```
Starting task container coding-agent--task--emrwjvcj
```

So the recipe is: read the container name from the activity log, then

```bash
ssh vdaznsr6gfmd2-main-bvxea6i--coding-agent--task--emrwjvcj@ssh.eu-3.platform.sh
# whoami -> web, hostname -> coding-agent--task--emrwjvcj.0
```

The SSH certificate is the normal one loaded by the Upsun CLI (`upsun ssh-cert:load`). Access is scoped to the run lifetime: the same command after completion returns "Access denied".

The CLI offers no path to this. `upsun ssh --app coding-agent` fails because the CLI validates the app name against the deployment's webapps list, and tasks are a separate `tasks` key it does not consult. A CLI gap worth raising: `upsun ssh --task coding-agent` (resolving the newest in-progress run) would make this discoverable.

### Observed container internals

- The container keepalive command is `sleep 3600` (`PLATFORM_APP_COMMAND`). The task command runs as a separate process under the configured `run.timeout` (900 s here).
- Task stdout is piped through `tee -a /var/log/app.log`, so the activity log content is also on disk inside the container.
- `/var/log` is not writable by the task process itself (Errno 13). Only the platform's tee writes there. The `/tmp` mount is writable as expected.
- `PLATFORM_APPLICATION_NAME=coding-agent`, standard `PLATFORM_*` env present.

## Live logs of a running task

The activity log is the log surface for tasks, and it streams during the run:

- `upsun activity:log <id>` follows an in-progress activity by default. stdout and stderr are interleaved, stderr prefixed with `W:`. Observed latency was 5 to 15 seconds per heartbeat.
- The raw endpoint `activities/<id>/log` supports `max_delay=0` for a non-blocking JSONL snapshot (each line `{"_id": n, "data": {"timestamp": ..., "message": ...}}`). This is the right primitive for the admin app's future log view (poll with `start_at=<last id>`).
- Scripting caveat: `upsun activity:log --refresh 0` on an in-progress activity can die on the CLI's internal 30 s curl timeout because the underlying request long-polls. Use the default follow mode or `max_delay=0` snapshots instead.
- `upsun log` (environment:logs) does not work for tasks: same webapp-only resolution as SSH. The data it would read (`/var/log/app.log`) does exist in the task container, so this is a CLI/API surface gap, not a missing capability.

## Product takeaways

1. Mid-run SSH exists and is lifetime-scoped, which is exactly the right security behavior, but it is undiscoverable: the container name only appears in the activity log preamble and the CLI cannot target tasks.
2. Live log streaming mid-run already works well through `activity:log`. A first-class `upsun task:log` or `upsun log --task` would remove the need to know the activity ID plumbing.
3. `upsun apps` hiding tasks while `deployments/current` lists them under a `tasks` key suggests the CLI simply predates the task type. Same root cause for the SSH and log gaps.
