# Social publishing: YouTube setup

Kiril publishes clips to YouTube Shorts through a Google OAuth client that you
create once. Users then connect their own channel from **Settings -> Social
Accounts**. This page covers YouTube; TikTok and Instagram are in
[configuration.md](./configuration.md).

## 1. Create the Google OAuth client

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create
   or pick a project.
2. **APIs & Services -> Library**: enable **YouTube Data API v3**.
3. **APIs & Services -> OAuth consent screen**: choose *External*, fill in the app
   name and your email. Add these scopes:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube.readonly`
4. While the app is in *Testing*, add every Google account that will connect
   under **Test users**. Other accounts get "access blocked".
5. **APIs & Services -> Credentials -> Create credentials -> OAuth client ID**,
   type **Web application**. Under **Authorised redirect URIs** add:
   - local: `http://localhost:3107/api/social/callback/youtube`
   - hosted: `https://<your-domain>/api/social/callback/youtube`

   The URI must match exactly, including scheme, port and no trailing slash.
6. Copy the client ID and secret into `.env` (never commit them).

## 2. Configure Kiril

```bash
YOUTUBE_OAUTH_CLIENT_ID=...apps.googleusercontent.com
YOUTUBE_OAUTH_CLIENT_SECRET=...
SOCIAL_PUBLISHING_ACCESS=all        # off (default) | admins | all
SOCIAL_PUBLISHING_PROVIDERS=youtube # optional: only offer YouTube
SOCIAL_OAUTH_REDIRECT_BASE_URL=http://localhost:3107  # defaults to NEXT_PUBLIC_APP_URL
```

Recreate the API and worker so they pick the values up:

```bash
docker compose up -d backend worker frontend
```

`SOCIAL_PUBLISHING_ACCESS` defaults to `off`, which hides Social and Analytics
and makes every `/social/*` route return 403. For a local trial set it to `all`
in your own `.env`; do not commit that file.

## 3. Connect and publish

1. Open `http://localhost:3107/settings/social` and choose **Connect YouTube**.
   The browser goes to Google's consent screen.
2. After consent Google redirects to `/api/social/callback/youtube`, which hands
   the code to the backend and returns you to Settings -> Social Accounts.
3. On a finished task, publish a clip from the task page.

This works with `NEXT_PUBLIC_AUTH_DISABLED=true`: the frontend signs requests as
the local user, so no separate local-only path is needed.

## Limits you will hit

- **Unverified project:** videos uploaded by an API project that has not passed
  Google's audit are forced to *private*. Use the YouTube API Services audit
  form to lift this.
- **Quota:** the default is 10,000 units a day; an upload costs about 1,600.
- **Scheduling** requires the Creator plan on the hosted service
  (`can_schedule_posts`); self-hosted installs have no plan limit.

## Troubleshooting

| Message | Cause |
|---|---|
| `invalid_client: The OAuth client was not found` | Wrong client ID or secret, or placeholders still in `.env` |
| `redirect_uri_mismatch` | The redirect URI in Google differs from `SOCIAL_OAUTH_REDIRECT_BASE_URL` plus `/api/social/callback/youtube` |
| `access_denied` / "access blocked" | The Google account is not a test user while the consent screen is in Testing |
| 403 from `/social/*` | `SOCIAL_PUBLISHING_ACCESS` is `off`, or `admins` and you are not an admin |
