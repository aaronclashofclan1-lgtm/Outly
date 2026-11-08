# YouTube Data API and Studio OAuth

## Public discovery key

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select one project.
3. Enable **YouTube Data API v3**.
4. Create an API key and restrict it to that API (and your IP if practical).
5. Put it in `.env` as `YOUTUBE_API_KEY`.
6. Do not create multiple projects to bypass quota; the Developer Policies
   prohibit quota sharing.

## Private Studio analytics (optional)

You need the channel owner's consent. Outly cannot read retention,
impressions, or audience for a channel you do not own.

1. In the same project, enable **YouTube Analytics API**.
2. Configure the OAuth consent screen.
3. Create an OAuth client of type **Desktop app**.
4. Add the client ID and secret to `.env`:
   ```text
   YOUTUBE_OAUTH_CLIENT_ID=...
   YOUTUBE_OAUTH_CLIENT_SECRET=...
   ```
5. In Outly open **Studio YouTube -> Connecter ma chaîne**.
6. The app opens a loopback server on `127.0.0.1`, completes consent, exchanges
   the code, and writes `YOUTUBE_REFRESH_TOKEN` to `.env`.
7. Click **Actualiser** in Studio.

Only a refresh token is stored. Revoke access from your Google account if the
file is lost. Analytics can lag 24-48 hours; impressions may be unavailable
for some videos. Outly explains missing data rather than inventing it.

Never share the OAuth client secret or refresh token. If either is exposed,
revoke the client/token immediately.
