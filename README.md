## Usage

You can run the Worker defined by your new project by executing `wrangler dev` in this
directory. This will start up an HTTP server and will allow you to iterate on your
Worker without having to restart `wrangler`.

### Types and autocomplete

This project also includes a pyproject.toml with some requirements which
set up autocomplete and type hints for this Python Workers project.

HTML pages are fetched from `public/` through the assets binding and rendered
with Flask's Jinja environment. Other assets, such as CSS, remain static, while
Flask also handles API routes such as `/api/`. Pass template values as keyword
arguments to `serve_asset`, for example `serve_asset("cards.html", cards=cards)`.

To get these installed you'll need `uv`, which you can install by following
https://docs.astral.sh/uv/getting-started/installation/.

Once `uv` is installed, you can run the following:

```
uv venv
uv sync
```

Then point your editor's Python plugin at the `.venv` directory. You should then have working
autocomplete and type information in your editor.

### D1 database

The ORM models in `src/database.py` describe the schema; they do not create
tables or insert rows automatically. Apply the checked-in D1 migration before
using the API:

```powershell
npx wrangler d1 migrations apply terraforming-db --local
```

This creates the card/list tables and the initial `banlist` and `restricted`
format-list rows in Wrangler's local D1 database. To apply migrations to the
Cloudflare database instead, use `--remote`:

```powershell
npx wrangler d1 migrations apply terraforming-db --remote
```

Add later schema changes as new, sequentially numbered SQL files in `migrations/`,
then apply them to the intended database with the same command.

The restricted-list API uses `/api/restricted/`. Apply all migrations, including
`0004_rename_reserved_slug.sql`, to update existing databases from the old
`reserved` slug without changing their card entries.

Card details shown on the Cards page are fetched in batches from Scryfall and
cached in D1 for 24 hours before they are refreshed. Apply migration
`0005_create_scryfall_card_cache.sql` to both local and remote databases before
using this feature.

### Adding a card to the banlist

Send a JSON `POST` to `/api/banlist/`. `oracle_id` is required and `reason`
is optional:

```powershell
$body = @{
    oracle_id = "94fac5fe-97d5-4c12-a80c-8efff9d853ae"
    reason = "Example reason"
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://127.0.0.1:8787/api/banlist/" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
```

The route creates the card record if it is not already present, then adds it to
the banlist. It returns `201 Created` on success and `409 Conflict` if that card
is already listed. Card records contain only `oracle_id`; list entries contain
`oracle_id` and `reason`, along with the list relationship key. This endpoint
currently has no authentication; protect it before deploying if banlist changes
should be restricted.

To remove a card from the banlist, send a `DELETE` request using its Oracle ID:

```powershell
Invoke-WebRequest `
    -Uri "http://127.0.0.1:8787/api/banlist/94fac5fe-97d5-4c12-a80c-8efff9d853ae/" `
    -Method Delete
```

The route returns `204 No Content` when removed and `404 Not Found` if the card
isn't currently on the banlist. It removes only the banlist entry, not the card
row or entries in other lists.
