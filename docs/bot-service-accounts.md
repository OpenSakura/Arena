# Bot Service Accounts

Bot service accounts let an external judge client create battles, wait for the
backend to finish both model runs, and submit a vote with bounded metadata. The
backend owns battle execution and vote storage. The external judge or LLM runner
that decides the vote is outside this backend's scope.

## Token Creation

Create and manage service accounts from the Admin UI at
`/admin/service-accounts`.
Admin-created service-account tokens are shown in plaintext exactly once. The UI
warning is:

```text
Copy now. This token will not be shown again.
```

If the token is dismissed, the page is refreshed, or the token list is loaded
again, only redacted token metadata is available. Store the plaintext in your own
secret manager when it is created. The database stores only a token prefix and a
HMAC/SHA-256 token hash.

The v1 bot scopes are exactly:

- `battle:create`
- `battle:read`
- `battle:execute`
- `vote:create`

The admin API can also be called from a browser-backed admin session. Unsafe
requests must include the current `X-CSRF-Token` returned by
`GET /api/v1/auth/session` and the HttpOnly session cookie set by login.

Example admin API flow with an existing backend session:

```bash
curl -X POST "https://arena.example.com/api/v1/admin/service-accounts" \
  -H "X-CSRF-Token: <csrf-token-from-auth-session>" \
  -H "Content-Type: application/json" \
  -b "arena_session=<session-cookie>" \
  -d '{"name":"Auto Judge Bot","description":"External judge client","enabled":true}'
```

```bash
curl -X POST "https://arena.example.com/api/v1/admin/service-accounts/<service_account_id>/tokens" \
  -H "X-CSRF-Token: <csrf-token-from-auth-session>" \
  -H "Content-Type: application/json" \
  -b "arena_session=<session-cookie>" \
  -d '{"scopes":["battle:create","battle:read","battle:execute","vote:create"],"expires_at":null}'
```

A token create response includes plaintext once, for example:

```json
{
  "plaintext_token": "osa_bot_example_copy_once_not_a_real_secret",
  "token": {
    "id": "00000000-0000-0000-0000-000000000002",
    "service_account_id": "00000000-0000-0000-0000-000000000001",
    "token_prefix": "osa_bot_example",
    "status": "active",
    "scopes": ["battle:create", "battle:read", "battle:execute", "vote:create"]
  }
}
```

The `osa_bot_example...` value above is fake. Do not put real generated tokens in
documentation, screenshots, logs, issue trackers, or analytics.

## Create And Wait

Bot clients use the non-streaming create-and-wait endpoint with the service token
as a bearer token. Include `Idempotency-Key` for retry-safe external jobs.

```bash
curl -X POST "https://arena.example.com/api/v1/bot/battles/create-and-wait" \
  -H "Authorization: Bearer osa_bot_example_copy_once_not_a_real_secret" \
  -H "Idempotency-Key: judge-run-001" \
  -H "Content-Type: application/json" \
  -d '{"task_id":"00000000-0000-0000-0000-000000000010","timeout_seconds":60}'
```

A completed response includes the battle id, status URL, model ids, and outputs
needed by the external judge:

```json
{
  "battle_id": "00000000-0000-0000-0000-000000000020",
  "status": "completed",
  "status_url": "/api/v1/bot/battles/00000000-0000-0000-0000-000000000020",
  "result": {
    "battle_id": "00000000-0000-0000-0000-000000000020",
    "run_a": {"side": "A", "model_id": "model-a", "output_text": "..."},
    "run_b": {"side": "B", "model_id": "model-b", "output_text": "..."}
  }
}
```

Use `GET /api/v1/bot/battles/{battle_id}` with `battle:read` to poll a timed-out
or previously created bot-owned battle.

## Vote Metadata

After your external judge evaluates the outputs, submit the vote to the normal
battle vote endpoint with the same service token and `vote:create` scope:

```bash
curl -X POST "https://arena.example.com/api/v1/battles/<battle_id>/vote" \
  -H "Authorization: Bearer osa_bot_example_copy_once_not_a_real_secret" \
  -H "Content-Type: application/json" \
  -d '{
    "winner": "A",
    "comment": "external judge selected A",
    "bot_metadata": {
      "external_run_id": "judge-run-001",
      "judge": "example-auto-judge",
      "score": 0.87,
      "rationale": "A is more faithful to the source text."
    }
  }'
```

`bot_metadata` must be a JSON object. It is bounded to 16 KiB serialized UTF-8,
max depth 4, max 64 keys per object, max key length 128, and max string length
4096. Human/OIDC callers cannot submit `bot_metadata`.

## Leaderboard And Export Filters

Public leaderboard filters use `judge_type`:

- `GET /api/v1/leaderboard` defaults to `judge_type=all` and includes human and
  bot votes.
- `GET /api/v1/leaderboard?judge_type=human` uses only human votes.
- `GET /api/v1/leaderboard?judge_type=bot` uses only bot votes.

Public callers cannot filter by `service_account_id`. Admin-only views can use
service-account filters where supported, including:

- `GET /api/v1/admin/leaderboard?judge_type=bot&service_account_id=<id>`
- `GET /api/v1/admin/export/votes.jsonl?service_account_id=<id>`

Vote exports include safe attribution fields such as `voter_actor_type`,
`service_account_id`, `service_account_name`, `service_account_token_id`, and
`bot_metadata`. They do not include plaintext tokens or token hashes.
