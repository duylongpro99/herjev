# Microsoft Teams channel

The `teams` channel posts each alert to a Teams group chat as a Teams user,
through Microsoft Graph with delegated permissions. This needs no Teams bot and
no tenant admin, as long as the tenant lets members register apps and consent
to these permissions.

1. Register an app once at entra.microsoft.com → App registrations: single
   tenant, no redirect URI. Under Authentication, set **Allow public client
   flows** to Yes. Under API permissions → Microsoft Graph → Delegated, add
   `ChatMessage.Send`, `Chat.ReadBasic` and `User.Read`.
2. Put its IDs in `.env` as `TEAMS_APP_ID` and `TEAMS_TENANT_ID`.
3. Run the setup. It signs the Teams account in with a device code
   (`herjev teams login`); open the URL on any device and sign in as the account
   that should post. It then lets you pick the group chat and writes
   `TEAMS_CHAT_ID` (with the two IDs) to this `.env`:

   ```bash
   scripts/setup-teams.sh                                    # pick from a list
   scripts/setup-teams.sh --chat "ops-alerts" --test        # by name, send a test alert
   scripts/setup-teams.sh --relogin                          # switch Teams account
   ```
4. Add the channel and restart `herjev run`:

   ```yaml
   channels:
     teams: {type: teams}      # chat_id defaults to TEAMS_CHAT_ID
   ```

The token is cached in `$HERJEV_HOME/teams_token.json` (mode 600) and refreshed
automatically; if the refresh token expires, the channel fails with a message
to run `herjev teams login` again. Messages appear under that user's name.
