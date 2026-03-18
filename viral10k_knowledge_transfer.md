# Viral10K — Automated Viral Content Detection System

## Project Context

Social media marketing company (FMO Media) posts 3-7x per week for ~250 clients across Facebook, Instagram, TikTok, and YouTube. Videos exceeding 10K views are considered viral. Previously, viral content was tracked manually — someone noticed it and posted in Slack. This system automates detection and reporting.

The system feeds into a broader **Phase 2: Central Wins Tracker** that Andrew is building, with fields: Client Name, Date, Type (Result/Praise/Review/Upgrade), Slack link, Screenshot, Review requested?, Case study candidate?, Extracted quote.

## Architecture Overview

```
Agorapulse API → Python Script → Threshold Detection → Slack Alerts + CSV Exports + HTML Reports
                                                      → Slack Unfurl → Thumbnail Fetch → 3D Showcase
```

## Agorapulse API Details

**Base URL:** `https://api.agorapulse.com`
**Auth:** `x-api-key` header (NOT Bearer token)
**Rate Limit:** 500 requests per 30 minutes
**Data Freshness:** Synced daily, 1-3 day delay from networks, updated by 4pm UTC
**Plan Requirement:** Custom plan required for Analytics Open API access

### Endpoints (confirmed working)

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1.0/core/organizations` | GET | List all organizations |
| `/v1.0/core/organizations/{orgId}/workspaces` | GET | List workspaces in org |
| `/v1.0/core/organizations/{orgId}/workspaces/{wsId}/profiles` | GET | List all social profiles |
| `/v1.0/report/organizations/{orgId}/workspaces/{wsId}/profiles/{profileUid}/insights/content?since={ts}&until={ts}` | GET | Content performance report |

### Date Parameters
- `since` and `until` use **Unix timestamps in seconds** (NOT ISO strings — ISO returns 400 error)

### Organization & Workspace IDs (FMO Media)

```python
ORG_WORKSPACES = [
    ("290398", "190399"),   # FMO Media 4
    ("217688", "117689"),   # FMO Media 1
    ("510368", "410258"),   # FMO Media 2
    ("377130", "277088"),   # FMO Media 6
    ("352521", "252495"),   # FMO Media 5
]
```

### Profile Response Structure

```json
{
  "profiles": [
    {
      "profileUid": "youtube_36232",
      "profileName": "Client Name Here",
      "profileType": "YOUTUBE",
      "socialNetworkId": "UCo_X6vx...",
      "workspaceId": 252495
    }
  ]
}
```

Profile types seen: `LINKEDIN_COMPANY`, `LINKEDIN_PERSONAL`, `FACEBOOK_PAGE`, `TIKTOK`, `YOUTUBE`, `INSTAGRAM`, `GOOGLE`

### Content Report Response Structure (confirmed from real data)

```json
{
  "data": [
    {
      "id": "126761874027792_1515924460541211",
      "publishingDate": "2026-02-25T14:00:37Z",
      "postUrl": "https://www.facebook.com/1515924460541211",
      "text": "Post caption text...",
      "username": "Brittany Wiseman",
      "tags": [],
      "viewsCount": 104,
      "organicViewsCount": 104,
      "paidViewsCount": 0,
      "videoViewsCount": 18,
      "organicVideoViewsCount": 18,
      "paidVideoViewsCount": 0,
      "reachCount": 88,
      "organicReachCount": 88,
      "paidReachCount": 0,
      "engagementCount": 1,
      "likeCount": 1,
      "commentsCount": 0,
      "sharesCount": 0,
      "reactionsCount": 1,
      "clicksCount": 0,
      "linkClicksCount": 0,
      "videoViewsTimeWatchedCount": 2,
      "videoViewsTimeWatchedRate": 43.4,
      "engagementRatePerView": 1.0,
      "engagementRatePerReach": 1.1
    }
  ]
}
```

**Key fields for viral detection:**
- `viewsCount` — total views (all content types, organic + paid). This is the PRIMARY metric used for viral threshold detection.
- `videoViewsCount` — video-specific 3s+ views. Can be `null` for non-video posts.
- `organicViewsCount` / `paidViewsCount` — breakdown
- `postUrl` — direct link to the post
- `publishingDate` — ISO format timestamp
- `engagementCount` — total engagement
- `text` — post caption
- `tags` — Agorapulse labels

**No thumbnail/image field exists in the API response.**

### Platforms NOT supported by API
- `GOOGLE` profiles return: `{"code":1002,"subCode":1104,"message":"This social profile is not handled by open APIs"}`
- `LINKEDIN_COMPANY` and `LINKEDIN_PERSONAL` return 0 posts (no content report data)

### Platform filter in script
Only these platforms are scanned: `FACEBOOK_PAGE`, `INSTAGRAM`, `TIKTOK`, `YOUTUBE`
This reduced profiles from 1,074 to 675 and cut scan time significantly.

## Scan Performance

| Metric | Value |
|---|---|
| Total profiles (unfiltered) | 1,074 |
| Total profiles (filtered) | 675 |
| Scan time (filtered) | ~22-25 minutes |
| API requests per scan | ~680 |
| Posts found (14-day window) | ~3,296 |
| Posts found (90-day window) | ~3,296+ |
| Viral videos (10K+ threshold) | 11-77 depending on lookback |
| Rate limit delay | 2 seconds between requests |
| Safety pause | 30 seconds every 200 requests |

## Threshold Tiers

```python
THRESHOLDS = [
    {"views": 10_000,  "label": "VIRAL",            "slack_channel": "#viral-wins"},
    {"views": 25_000,  "label": "CASE STUDY",       "slack_channel": "#viral-wins"},
    {"views": 50_000,  "label": "LEADERSHIP ALERT",  "slack_channel": "#viral-wins + #leadership"},
    {"views": 100_000, "label": "MEGA VIRAL",        "slack_channel": "#viral-wins + #leadership"},
]
```

## Slack Integration

### Webhook Alerts
- Uses Slack Incoming Webhook to post formatted Block Kit messages
- Env var: `SLACK_WEBHOOK_URL`
- Currently posts to Andrew's DM for testing

### Thumbnail Fetching via Slack
The system uses Slack as a thumbnail proxy:
1. Bot posts viral video URLs to a DM channel it has access to (`D0ALV437AAU`)
2. Waits 20 seconds for Slack to unfurl the links
3. Reads the message back via `conversations.history` API
4. Extracts `image_url` or `thumb_url` from unfurled `attachments`
5. Downloads the thumbnail image
6. Base64 encodes it and embeds in the HTML as a data URI
7. Deletes the temporary message

**Why this approach:** TikTok blocks direct thumbnail scraping (JavaScript-rendered pages). TikTok's oembed API (`/oembed?url=...`) returns video metadata but does NOT include `thumbnail_url`. Slack's servers fetch thumbnails server-side and cache them on their CDN (`slack-imgs.com`), proxying the original TikTok CDN URL.

**Known limitation:** When posting many URLs (77+) in one message, Slack only unfurls a subset. Needs batching for large result sets.

### Bot Token Scopes Required
```
incoming-webhook
im:history
im:read
im:write
chat:write
chat:write.public
channels:history
channels:read
groups:read
```

### Slack Workspace Info
- Workspace ID: `T02PL2QGU2H`
- Bot DM channel for thumbnails: `D0ALV437AAU`
- Bot app name: `Viral10K`

## Script Commands

```bash
# Set environment variables
export AGORAPULSE_API_KEY='key'
export SLACK_WEBHOOK_URL='https://hooks.slack.com/...'
export SLACK_BOT_TOKEN='xoxb-...'

# Setup steps (run in order first time)
python3 viral_detector.py --discover-orgs
python3 viral_detector.py --discover-profiles
python3 viral_detector.py --test-one

# Full scan
python3 viral_detector.py

# Help
python3 viral_detector.py --help
```

## Output Files

| File | Contents |
|---|---|
| `all_videos_TIMESTAMP.csv` | Every post scanned with view counts |
| `viral_videos_TIMESTAMP.csv` | Only posts exceeding 10K views |
| `errors_TIMESTAMP.csv` | API errors during scan |
| `viral_report_TIMESTAMP.html` | Interactive dashboard (Chart.js) — leaderboard, platform donut, timeline |
| `viral_showcase_TIMESTAMP.html` | 3D floating spheres (Three.js) — sized by views, thumbnails texture-mapped, labels |
| `all_profiles.csv` | All discovered profiles |
| `org_workspaces.json` | Org/workspace ID pairs |
| `test_response.json` | Raw API response from test profile |

## HTML Report Features

### Dashboard (`viral_report_*.html`)
- Dark theme, Chart.js powered
- Stats bar: viral count, total views, posts scanned, top video
- Donut chart: platform breakdown by views
- Bar chart: timeline of viral videos by publish date
- Leaderboard table: ranked by views, proportional bars, tier badges, clickable rows

### 3D Showcase (`viral_showcase_*.html`)
- Three.js r128 powered
- Spheres sized proportionally to view count
- Platform-colored (TikTok=cyan, Facebook=blue, Instagram=pink, YouTube=red)
- Glow rings around each sphere
- Floating text labels: client name, view count, platform
- Thumbnail textures from Slack unfurl (base64 embedded)
- Mouse drag to orbit, scroll to zoom, hover for tooltip, click to open post
- Auto-rotates slowly when not dragging
- Star particle background
- Self-contained single HTML file (~462KB with thumbnails)

## Wins Tracker Integration (Phase 2)

When a video crosses 10K, the system can auto-populate these Wins Tracker fields:

| Field | Auto? | Source |
|---|---|---|
| Client Name | YES | profileName from Agorapulse |
| Date | YES | Date threshold crossed |
| Type | YES | Auto-tagged "Result" |
| Slack link | YES | Permalink to Slack alert |
| Screenshot | NO (v1) | Needs headless browser or manual |
| Review requested? | PARTIAL | Defaults N |
| Case study candidate? | PARTIAL | Auto-flags Y at 25K+ |
| Extracted quote | NO | Human input |

## Known Issues & Gotchas

1. **API key security:** Andrew shared API keys and webhook URLs in chat twice. All have been revoked and regenerated. Keys should ONLY be stored in environment variables.
2. **Token health:** If Agorapulse tokens for social profiles are disconnected, data will be stale/missing. YouTube tokens invalid >7 days = all data deleted permanently.
3. **CSV export bug (fixed):** Viral videos have extra fields (tier_label, tier_views, slack_channel) that regular videos don't. Fixed with `extrasaction="ignore"` and collecting all keys across rows.
4. **Python version:** Andrew's Mac has Python 3.9 with LibreSSL 2.8.3 (triggers urllib3 OpenSSL warnings — harmless).
5. **Thumbnail batching:** Slack only unfurls a limited number of links per message. For 77+ viral videos, need to batch URLs across multiple messages.
6. **No Instagram/YouTube thumbnails tested yet:** Only TikTok and Facebook confirmed.

## Future Improvements

- Automated daily cron job (currently manual terminal execution)
- Proper database (Notion/Airtable/Sheets) instead of CSV exports
- Wins Tracker auto-population
- Thumbnail batching for large result sets
- Dedicated Slack channel (#viral-wins) instead of DM testing
- Automated screenshots via headless browser
- Content type auto-classification
- Client notification email templates
- Competitive benchmarking using Agorapulse Competitors Report
- Plateau detection (stop tracking videos with <5% view growth)
- Looker Studio dashboard via Agorapulse's native connector

## Environment

- Machine: Mac (Darwin), Python 3.9
- Script location: `~/Downloads/viral_detector.py`
- All credentials via environment variables
- Sharing: HTML files via email, Google Drive, or Netlify Drop
